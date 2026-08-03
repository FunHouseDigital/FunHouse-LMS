/**
 * Role-gated navigation model (Req 2). See design.md "Role-gated navigation".
 *
 * The role → navigable-screen mapping is modelled here as a pure function so it
 * can be exhaustively property-tested (Property 22) and reused by the nav shell
 * (`AppShellNav`) and the route guard (`RouteGuard`) without duplication.
 *
 * Rules:
 *  - no valid JWT            → only the login screen is reachable (Req 2.3);
 *  - authenticated `manager` → Log Session, Players, Today, Sell (Req 2.1);
 *  - authenticated `founder` → Revenue Dashboard, Attendance & Sessions,
 *                              Metrics Entry, Alerts (Req 2.2);
 *  - any screen outside the role is never navigable (Req 2.4).
 */
import type { Role } from './authManager';

/** Stable identifiers for every screen in the app. */
export type ScreenId =
  | 'login'
  | 'log-session'
  | 'players'
  | 'learners'
  | 'today'
  | 'sell'
  | 'revenue'
  | 'attendance'
  | 'metrics'
  | 'alerts';

export interface NavScreen {
  id: ScreenId;
  path: string;
  label: string;
}

/** The authentication facts the nav model needs. */
export interface NavAuthState {
  authenticated: boolean;
  role: Role | null;
}

export const LOGIN_SCREEN: NavScreen = { id: 'login', path: '/login', label: 'Log in' };

/** Manager screens, in nav order (Req 2.1). */
export const MANAGER_SCREENS: readonly NavScreen[] = [
  { id: 'log-session', path: '/log-session', label: 'Log Session' },
  { id: 'players', path: '/players', label: 'Players' },
  { id: 'today', path: '/today', label: 'Today' },
  { id: 'sell', path: '/sell', label: 'Sell' },
];

/** Founder screens, in nav order (Req 2.2). */
export const FOUNDER_SCREENS: readonly NavScreen[] = [
  { id: 'revenue', path: '/revenue', label: 'Revenue Dashboard' },
  { id: 'attendance', path: '/attendance', label: 'Attendance & Sessions' },
  { id: 'metrics', path: '/metrics', label: 'Metrics Entry' },
  { id: 'alerts', path: '/alerts', label: 'Alerts' },
];

/** Facilitator screens, in nav order. Attendance is the facilitator home. */
export const FACILITATOR_SCREENS: readonly NavScreen[] = [
  { id: 'attendance', path: '/attendance', label: 'Attendance & Sessions' },
  { id: 'learners', path: '/learners', label: 'Learners' },
  { id: 'metrics', path: '/metrics', label: 'Metrics Entry' },
];

/** Every protected screen (used for exhaustive route generation and tests). */
export const ALL_PROTECTED_SCREENS: readonly NavScreen[] = [
  ...MANAGER_SCREENS,
  ...FOUNDER_SCREENS,
  FACILITATOR_SCREENS[1],
];

/**
 * The exact set of navigable screens for an auth state.
 *
 * - Unauthenticated → `[login]` only (Req 2.3).
 * - `manager` → the manager set (Req 2.1).
 * - `founder` → the founder set (Req 2.2).
 * - Any other authenticated role (e.g. `facilitator`, which has no Phase-1
 *   screens) → an empty set; nothing outside the role is ever exposed (Req 2.4).
 */
export function navScreensFor(state: NavAuthState): NavScreen[] {
  if (!state.authenticated || state.role === null) {
    return [LOGIN_SCREEN];
  }
  if (state.role === 'manager') return [...MANAGER_SCREENS];
  if (state.role === 'founder') return [...FOUNDER_SCREENS];
  if (state.role === 'facilitator') return [...FACILITATOR_SCREENS];
  return [];
}

/** True iff the given screen is navigable in the given auth state. */
export function canNavigate(state: NavAuthState, screenId: ScreenId): boolean {
  if (screenId === 'login') {
    // The login screen is reachable only while unauthenticated.
    return !state.authenticated || state.role === null;
  }
  return navScreensFor(state).some((s) => s.id === screenId);
}

/**
 * The path a user should be redirected to when they hit a route they may not
 * access: the login screen when unauthenticated, otherwise the first screen of
 * their role (their "home"). Falls back to `/login` for roles with no screens.
 */
export function defaultPathFor(state: NavAuthState): string {
  if (!state.authenticated || state.role === null) return LOGIN_SCREEN.path;
  const screens = navScreensFor(state);
  return screens.length > 0 ? screens[0].path : LOGIN_SCREEN.path;
}
