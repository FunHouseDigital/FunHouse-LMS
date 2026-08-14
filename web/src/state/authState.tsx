/**
 * Auth state — the React binding around the {@link AuthManager} (Req 1, 2, 17).
 *
 * Provides the authenticated session to the component tree, exposes `login` /
 * `logout`, and constructs the Container_API client with immutable request auth
 * snapshots. Protected-request 401s, expiry, and cross-context marker removal
 * converge here so React state and legacy CacheStorage are cleared together.
 *
 * For tests, an `authManager` and/or `client` may be injected so no real
 * network or environment configuration is required.
 */
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
import type { Session } from '../domain/types';
import {
  AuthManager,
  SESSION_ACTIVE_STORAGE_KEY,
  SecureStorageUnavailableError,
  type AuthRequestSnapshot,
  type LoginOutcome,
  type Role,
} from '../domain/authManager';
import { ContainerApiClient } from '../api/client';
import { clearAuthenticatedResponseCaches } from '../pwa/authenticatedCaches';

const MAX_TIMEOUT_MS = 2_147_483_647;

export interface AuthContextValue {
  /** True while a valid, unexpired JWT is held (Req 1.5, 1.6). */
  isAuthenticated: boolean;
  /** The current session, or `null`. */
  session: Session | null;
  /** The current role for nav gating, or `null` (Req 2). */
  role: Role | null;
  /** True while a login request is in flight. */
  submitting: boolean;
  /** Attempt a login; returns the Auth_Manager outcome. */
  login: (identifier: string, password: string) => Promise<LoginOutcome>;
  /** Clear the session/JWT (retains the Sync_Queue and scoped records). */
  logout: () => void;
  /** The wired Container_API client (bearer token attached automatically). */
  client: ContainerApiClient;
  /** The underlying Auth_Manager (for advanced/imperative use). */
  authManager: AuthManager;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function getDefaultBaseUrl(): string {
  const configured = import.meta.env.VITE_API_BASE_URL;
  if (configured) return configured;

  // A standalone HTTPS PWA falls back to its own origin, which is valid only
  // when the API is intentionally served there too. The production split
  // Vercel deployment must set VITE_API_BASE_URL at build time to the separate
  // FastAPI origin. Local development keeps using the separate localhost API.
  if (typeof window !== 'undefined' && window.location.protocol === 'https:') {
    return window.location.origin;
  }
  return 'http://localhost:8000';
}

export interface AuthProviderProps {
  children: ReactNode;
  /** Inject an Auth_Manager (tests); a default is built otherwise. */
  authManager?: AuthManager;
  /** Inject a Container_API client (tests); a default is built otherwise. */
  client?: ContainerApiClient;
  /** Override the API base URL for the default client. */
  baseUrl?: string;
}

export function AuthProvider({
  children,
  authManager: injectedManager,
  client: injectedClient,
  baseUrl,
}: AuthProviderProps) {
  // The default client is constructed before React state callbacks exist. Its
  // stable bridge is populated later in this render, before children can issue
  // requests.
  const unauthorizedBridge = useRef<(snapshot: AuthRequestSnapshot) => void>(() => undefined);

  // Build the client + Auth_Manager exactly once and keep them stable.
  const ref = useRef<{ client: ContainerApiClient; authManager: AuthManager } | null>(null);
  if (ref.current === null) {
    let manager: AuthManager | undefined = injectedManager;
    const client =
      injectedClient ??
      new ContainerApiClient({
        baseUrl: baseUrl ?? getDefaultBaseUrl(),
        getAuthSnapshot: () =>
          manager?.getAuthRequestSnapshot() ?? { token: null, generation: 0 },
        onUnauthorized: (snapshot) => unauthorizedBridge.current(snapshot),
      });
    if (!manager) {
      manager = new AuthManager({ loginFn: (id, pw) => client.login(id, pw) });
    }
    ref.current = { client, authManager: manager };
  }
  const { client, authManager } = ref.current;

  const [session, setSession] = useState<Session | null>(() => authManager.getSession());
  const [restoring, setRestoring] = useState(injectedManager === undefined);
  const [submitting, setSubmitting] = useState(false);

  const clearAuthView = useCallback(() => {
    setSession(null);
    setRestoring(false);
    void clearAuthenticatedResponseCaches();
  }, []);

  const revokeCurrent = useCallback(() => {
    authManager.handleUnauthorized();
    clearAuthView();
  }, [authManager, clearAuthView]);

  const revokeRequestLifecycle = useCallback(
    (snapshot: AuthRequestSnapshot): boolean => {
      if (!authManager.handleUnauthorized(snapshot)) return false;
      clearAuthView();
      return true;
    },
    [authManager, clearAuthView],
  );
  unauthorizedBridge.current = revokeRequestLifecycle;

  useEffect(() => {
    if (injectedManager !== undefined) return;
    let active = true;
    void authManager.restoreSession().then((restored) => {
      if (!active) return;
      setSession(restored);
      setRestoring(false);
    });
    return () => {
      active = false;
    };
  }, [authManager, injectedManager]);

  const login = useCallback(
    async (identifier: string, password: string): Promise<LoginOutcome> => {
      setSubmitting(true);
      try {
        // Protected API responses are no longer runtime-cached. Remove any
        // legacy caches opportunistically, but never make CacheStorage support
        // a prerequisite for authentication.
        void clearAuthenticatedResponseCaches();
        const outcome = await authManager.login(identifier, password);
        if (outcome.ok) {
          setSession(outcome.session);
        }
        return outcome;
      } catch (error) {
        if (error instanceof SecureStorageUnavailableError) {
          // AuthManager has already revoked durable and in-memory credentials.
          clearAuthView();
        }
        throw error;
      } finally {
        setSubmitting(false);
      }
    },
    [authManager, clearAuthView],
  );

  const logout = useCallback(() => {
    revokeCurrent();
  }, [revokeCurrent]);

  useEffect(() => {
    if (!session) return undefined;

    const lifecycleSnapshot = authManager.getAuthRequestSnapshot();
    let timeout: ReturnType<typeof globalThis.setTimeout> | undefined;
    let settled = false;

    const clearForAcceptedExternalInvalidation = (accepted: boolean) => {
      if (!accepted) return false;
      settled = true;
      clearAuthView();
      return true;
    };

    const revokeExpiredLifecycle = () => {
      if (settled) return;
      settled = true;
      if (lifecycleSnapshot.token !== null) {
        revokeRequestLifecycle(lifecycleSnapshot);
        return;
      }
      // This can only occur when rendering was delayed past expiry. The check
      // and revoke are synchronous, so no replacement can interleave.
      if (
        authManager.getSession()?.access_token === session.access_token &&
        authManager.isExpired()
      ) {
        revokeCurrent();
      }
    };

    const revalidateForegroundState = () => {
      if (settled) return;
      if (
        clearForAcceptedExternalInvalidation(
          authManager.handleExternalMarkerInvalidation(),
        )
      ) {
        return;
      }
      if (authManager.getSession()?.access_token !== session.access_token) {
        // This effect belongs to an older rendered lifecycle. Its replacement
        // owns all further revocation decisions.
        settled = true;
        return;
      }
      if (authManager.isExpired()) revokeExpiredLifecycle();
    };

    const scheduleExpiry = () => {
      if (settled) return;
      if (timeout !== undefined) globalThis.clearTimeout(timeout);
      const remaining = new Date(session.expires_at).getTime() - Date.now();
      if (!Number.isFinite(remaining) || remaining <= 0) {
        revokeExpiredLifecycle();
        return;
      }
      timeout = globalThis.setTimeout(
        scheduleExpiry,
        Math.min(remaining, MAX_TIMEOUT_MS),
      );
    };

    const handleVisibility = () => {
      if (document.visibilityState === 'visible') revalidateForegroundState();
    };
    const handleStorage = (event: StorageEvent) => {
      if (
        event.key === SESSION_ACTIVE_STORAGE_KEY &&
        event.newValue === null &&
        authManager.handleExternalMarkerRemoval(event.oldValue)
      ) {
        settled = true;
        clearAuthView();
      }
    };

    scheduleExpiry();
    revalidateForegroundState();
    window.addEventListener('focus', revalidateForegroundState);
    window.addEventListener('pageshow', revalidateForegroundState);
    window.addEventListener('storage', handleStorage);
    document.addEventListener('visibilitychange', handleVisibility);

    return () => {
      if (timeout !== undefined) globalThis.clearTimeout(timeout);
      window.removeEventListener('focus', revalidateForegroundState);
      window.removeEventListener('pageshow', revalidateForegroundState);
      window.removeEventListener('storage', handleStorage);
      document.removeEventListener('visibilitychange', handleVisibility);
    };
  }, [
    authManager,
    clearAuthView,
    revokeCurrent,
    revokeRequestLifecycle,
    session,
  ]);

  const value = useMemo<AuthContextValue>(() => {
    const isAuthenticated = session !== null && authManager.isAuthenticated();
    return {
      isAuthenticated,
      session,
      role: isAuthenticated ? (session?.role ?? null) : null,
      submitting,
      login,
      logout,
      client,
      authManager,
    };
  }, [session, submitting, login, logout, client, authManager]);

  return (
    <AuthContext.Provider value={value}>
      {restoring ? (
        <main className="session-restoring" aria-live="polite">
          Checking secure session…
        </main>
      ) : (
        children
      )}
    </AuthContext.Provider>
  );
}

/** Access the auth context; throws if used outside an {@link AuthProvider}. */
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return ctx;
}
