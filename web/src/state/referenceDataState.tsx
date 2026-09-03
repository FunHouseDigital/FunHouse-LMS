import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { localDataLifecycleIdentity } from '../domain/personalData';
import { localDataScopeForSession } from '../domain/authManager';
import type { LocalDataOwner, PlayerOut, ProductOut, Session } from '../domain/types';
import { clearAuthenticatedResponseCaches } from '../pwa/authenticatedCaches';
import {
  getCachedRead,
  writeBalances,
  writeCachedRead,
} from '../store/localStore';
import { useAuth } from './authState';

export type ReferenceDataStatus = 'idle' | 'loading' | 'ready' | 'offline' | 'error';

export interface ReferenceDataContextValue {
  status: ReferenceDataStatus;
  playersAvailable: boolean;
  productsAvailable: boolean;
  productsRequired: boolean;
  lastRefreshedAt: string | null;
  revision: number;
  cacheScope: string | null;
  owner: LocalDataOwner | null;
  playersCacheKey: string;
  productsCacheKey: string;
  refresh: () => Promise<void>;
  refreshPlayerEntitlements: (playerId: string) => Promise<boolean>;
}

const DEFAULT_VALUE: ReferenceDataContextValue = {
  status: 'idle',
  playersAvailable: false,
  productsAvailable: false,
  productsRequired: true,
  lastRefreshedAt: null,
  revision: 0,
  cacheScope: null,
  owner: null,
  playersCacheKey: 'players',
  productsCacheKey: 'products',
  refresh: async () => undefined,
  refreshPlayerEntitlements: async () => false,
};

const ReferenceDataContext = createContext<ReferenceDataContextValue>(DEFAULT_VALUE);

function isOnline(): boolean {
  return typeof navigator === 'undefined' || navigator.onLine;
}

function latestTimestamp(...timestamps: Array<string | undefined>): string | null {
  const present = timestamps.filter((value): value is string => value !== undefined);
  if (present.length === 0) return null;
  return present.reduce((latest, value) => (value > latest ? value : latest));
}

export function sessionScopeKey(session: Session | null): string | null {
  return session ? localDataScopeForSession(session) : null;
}

function cacheKey(dataset: 'players' | 'products', scope: string | null): string {
  return scope ? `${dataset}:${scope}` : dataset;
}

export function ReferenceDataProvider({ children }: { children: ReactNode }) {
  const { isAuthenticated, session, client, localDataOwner, isLocalDataOwnerCurrent } = useAuth();
  const cacheScope = useMemo(() => sessionScopeKey(session), [session]);
  const lifecycleIdentity = localDataLifecycleIdentity(localDataOwner);
  const productsRequired = session?.role !== 'facilitator';
  const playersCacheKey = cacheKey('players', cacheScope);
  const productsCacheKey = cacheKey('products', cacheScope);

  const [status, setStatus] = useState<ReferenceDataStatus>('idle');
  const [playersAvailable, setPlayersAvailable] = useState(false);
  const [productsAvailable, setProductsAvailable] = useState(false);
  const [lastRefreshedAt, setLastRefreshedAt] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const requestGeneration = useRef(0);
  const activeIdentity = useRef<string | null>(lifecycleIdentity);
  const refreshInFlight = useRef<Promise<void> | null>(null);
  const entitlementSequence = useRef(new Map<string, number>());

  // Invalidate network work synchronously when React renders a different account.
  if (activeIdentity.current !== lifecycleIdentity) {
    activeIdentity.current = lifecycleIdentity;
    requestGeneration.current += 1;
    refreshInFlight.current = null;
    entitlementSequence.current.clear();
  }

  const runRefresh = useCallback(async () => {
    if (!isAuthenticated || !cacheScope || !localDataOwner) return;

    const owner = localDataOwner;
    const generation = requestGeneration.current;
    const [cachedPlayers, cachedProducts] = await Promise.all([
      getCachedRead<PlayerOut[]>(playersCacheKey, owner),
      getCachedRead<ProductOut[]>(productsCacheKey, owner),
    ]);
    if (!isLocalDataOwnerCurrent(owner) || generation !== requestGeneration.current || activeIdentity.current !== lifecycleIdentity) return;

    const hadPlayers = cachedPlayers !== undefined;
    const hadProducts = !productsRequired || cachedProducts !== undefined;
    setPlayersAvailable(hadPlayers);
    setProductsAvailable(hadProducts);
    setLastRefreshedAt(
      latestTimestamp(cachedPlayers?.cached_at, cachedProducts?.cached_at),
    );
    setRevision((value) => value + 1);

    if (!isOnline()) {
      setStatus('offline');
      return;
    }

    setStatus('loading');
    // Protected reads are not stored in CacheStorage. Legacy cleanup is
    // opportunistic and must not delay account-scoped IndexedDB hydration.
    void clearAuthenticatedResponseCaches();
    const [playersResult, productsResult] = await Promise.allSettled([
      client.getPlayers(),
      productsRequired ? client.getProducts() : Promise.resolve([]),
    ]);
    if (!isLocalDataOwnerCurrent(owner) || generation !== requestGeneration.current || activeIdentity.current !== lifecycleIdentity) return;

    const refreshedAt = new Date().toISOString();
    const playerWrite =
      playersResult.status === 'fulfilled'
        ? await Promise.allSettled([
            writeCachedRead(playersCacheKey, playersResult.value, owner, refreshedAt),
          ])
        : [];
    const productWrite =
      productsRequired && productsResult.status === 'fulfilled'
        ? await Promise.allSettled([
            writeCachedRead(productsCacheKey, productsResult.value, owner, refreshedAt),
          ])
        : [];
    if (!isLocalDataOwnerCurrent(owner) || generation !== requestGeneration.current || activeIdentity.current !== lifecycleIdentity) return;

    const playersStored = playerWrite[0]?.status === 'fulfilled';
    const productsStored = !productsRequired || productWrite[0]?.status === 'fulfilled';
    setPlayersAvailable(hadPlayers || playersStored);
    setProductsAvailable(hadProducts || productsStored);
    if (playersStored || productsStored) {
      setLastRefreshedAt(refreshedAt);
      setRevision((value) => value + 1);
    }
    setStatus(playersStored && productsStored ? 'ready' : 'error');
  }, [
    cacheScope,
    client,
    isAuthenticated,
    isLocalDataOwnerCurrent,
    localDataOwner,
    playersCacheKey,
    productsCacheKey,
    productsRequired,
  ]);

  const refresh = useCallback(async () => {
    if (refreshInFlight.current) return refreshInFlight.current;

    const generation = requestGeneration.current;
    let operation: Promise<void>;
    operation = runRefresh()
      .catch(() => {
        if (generation === requestGeneration.current) {
          setStatus(isOnline() ? 'error' : 'offline');
        }
      })
      .finally(() => {
        if (refreshInFlight.current === operation) {
          refreshInFlight.current = null;
        }
      });
    refreshInFlight.current = operation;
    return operation;
  }, [runRefresh]);

  const refreshPlayerEntitlements = useCallback(
    async (playerId: string): Promise<boolean> => {
      if (!isAuthenticated || !cacheScope || !localDataOwner || !isOnline()) return false;

      const owner = localDataOwner;
      const generation = requestGeneration.current;
      const requestKey = `${cacheScope}:${playerId}`;
      const sequence = (entitlementSequence.current.get(requestKey) ?? 0) + 1;
      entitlementSequence.current.set(requestKey, sequence);

      try {
        const balances = await client.getPlayerEntitlements(playerId);
        if (
          !isLocalDataOwnerCurrent(owner) ||
          generation !== requestGeneration.current ||
          activeIdentity.current !== lifecycleIdentity ||
          entitlementSequence.current.get(requestKey) !== sequence
        ) {
          return false;
        }
        await writeBalances(playerId, balances, owner);
        if (
          !isLocalDataOwnerCurrent(owner) ||
          generation !== requestGeneration.current ||
          activeIdentity.current !== lifecycleIdentity ||
          entitlementSequence.current.get(requestKey) !== sequence
        ) {
          return false;
        }
        setRevision((value) => value + 1);
        return true;
      } catch {
        // The centrally wired client owns token-aware 401 revocation. This
        // request only reports whether fresh balances were stored.
        return false;
      }
    },
    [cacheScope, client, isAuthenticated, isLocalDataOwnerCurrent, lifecycleIdentity, localDataOwner],
  );

  useEffect(() => {
    if (!isAuthenticated || !cacheScope) {
      requestGeneration.current += 1;
      refreshInFlight.current = null;
      entitlementSequence.current.clear();
      setStatus('idle');
      setPlayersAvailable(false);
      setProductsAvailable(false);
      setLastRefreshedAt(null);
      return;
    }
    void refresh();
  }, [cacheScope, isAuthenticated, lifecycleIdentity, refresh]);

  useEffect(() => {
    if (!isAuthenticated || typeof window === 'undefined') return undefined;

    const handleOnline = () => {
      void refresh();
    };
    const handleOffline = () => {
      setStatus('offline');
    };
    window.addEventListener('online', handleOnline);
    window.addEventListener('offline', handleOffline);
    return () => {
      window.removeEventListener('online', handleOnline);
      window.removeEventListener('offline', handleOffline);
    };
  }, [isAuthenticated, refresh]);

  const value = useMemo<ReferenceDataContextValue>(
    () => ({
      status,
      playersAvailable,
      productsAvailable,
      productsRequired,
      lastRefreshedAt,
      revision,
      cacheScope,
      owner: localDataOwner,
      playersCacheKey,
      productsCacheKey,
      refresh,
      refreshPlayerEntitlements,
    }),
    [
      status,
      playersAvailable,
      productsAvailable,
      productsRequired,
      lastRefreshedAt,
      revision,
      cacheScope,
      localDataOwner,
      playersCacheKey,
      productsCacheKey,
      refresh,
      refreshPlayerEntitlements,
    ],
  );

  return (
    <ReferenceDataContext.Provider value={value}>
      {children}
    </ReferenceDataContext.Provider>
  );
}

export function useReferenceData(): ReferenceDataContextValue {
  return useContext(ReferenceDataContext);
}
