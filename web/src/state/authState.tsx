/**
 * Auth state — the React binding around the {@link AuthManager} (Req 1, 2, 17).
 *
 * Provides the authenticated session to the component tree, exposes `login` /
 * `logout`, and constructs the Container_API client with its bearer-token
 * provider wired to the Auth_Manager (Req 1.5). Screens read `isAuthenticated`
 * + `role` from here; the route guard and nav shell derive their behaviour from
 * the same source of truth.
 *
 * For tests, an `authManager` and/or `client` may be injected so no real
 * network or environment configuration is required.
 */
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import type { Session } from '../domain/types';
import { AuthManager, type LoginOutcome, type Role } from '../domain/authManager';
import { ContainerApiClient } from '../api/client';
import { clearAuthenticatedResponseCaches } from '../pwa/authenticatedCaches';

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
  /** Clear the session/JWT (retains the Sync_Queue). */
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
  // Build the client + Auth_Manager exactly once and keep them stable.
  const ref = useRef<{ client: ContainerApiClient; authManager: AuthManager } | null>(null);
  if (ref.current === null) {
    let manager: AuthManager | undefined = injectedManager;
    const client =
      injectedClient ??
      new ContainerApiClient({
        baseUrl: baseUrl ?? getDefaultBaseUrl(),
        // Wire the bearer token to the Auth_Manager (Req 1.5).
        getToken: () => manager?.getToken() ?? null,
      });
    if (!manager) {
      manager = new AuthManager({ loginFn: (id, pw) => client.login(id, pw) });
    }
    ref.current = { client, authManager: manager };
  }
  const { client, authManager } = ref.current;

  const [session, setSession] = useState<Session | null>(() => authManager.getSession());
  const [submitting, setSubmitting] = useState(false);

  const login = useCallback(
    async (identifier: string, password: string): Promise<LoginOutcome> => {
      setSubmitting(true);
      try {
        // Clear bearer-authorized response caches before a new token can be
        // activated. If this mandatory account-isolation boundary fails, the
        // login aborts while AuthManager is still unauthenticated.
        await clearAuthenticatedResponseCaches();
        const outcome = await authManager.login(identifier, password);
        if (outcome.ok) {
          setSession(outcome.session);
        }
        return outcome;
      } finally {
        setSubmitting(false);
      }
    },
    [authManager],
  );

  const logout = useCallback(() => {
    authManager.handleUnauthorized();
    setSession(null);
    void clearAuthenticatedResponseCaches().catch(() => undefined);
  }, [authManager]);

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

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

/** Access the auth context; throws if used outside an {@link AuthProvider}. */
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return ctx;
}
