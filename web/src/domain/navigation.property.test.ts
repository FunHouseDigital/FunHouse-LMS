import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import {
  ALL_PROTECTED_SCREENS,
  FOUNDER_SCREENS,
  LOGIN_SCREEN,
  MANAGER_SCREENS,
  canNavigate,
  defaultPathFor,
  navScreensFor,
  type NavAuthState,
  type ScreenId,
} from './navigation';
import type { Role } from './authManager';

const ALL_SCREEN_IDS: ScreenId[] = [
  'login',
  ...ALL_PROTECTED_SCREENS.map((s) => s.id),
];

const roleArb: fc.Arbitrary<Role | null> = fc.constantFrom<Role | null>(
  'manager',
  'founder',
  'facilitator',
  null,
);

const stateArb: fc.Arbitrary<NavAuthState> = fc.record({
  authenticated: fc.boolean(),
  role: roleArb,
});

function expectedIds(state: NavAuthState): Set<ScreenId> {
  if (!state.authenticated || state.role === null) return new Set<ScreenId>(['login']);
  if (state.role === 'manager') return new Set(MANAGER_SCREENS.map((s) => s.id));
  if (state.role === 'founder') return new Set(FOUNDER_SCREENS.map((s) => s.id));
  return new Set<ScreenId>();
}

describe('Role-gated navigation model (Property 22)', () => {
  // Feature: revenue-pwa, Property 22: Role-gated navigation exposes exactly the
  // permitted screens. For any authentication state, the set of navigable
  // screens equals the exact set permitted for that role (manager, founder), or
  // only the login screen when no valid JWT is present; screens outside the role
  // are never navigable.
  // Validates: Requirements 2.1, 2.2, 2.3, 2.4
  it('Property 22: navigable set equals exactly the role-permitted set', () => {
    fc.assert(
      fc.property(stateArb, (state) => {
        const expected = expectedIds(state);
        const actualIds = new Set(navScreensFor(state).map((s) => s.id));

        // Exact set equality (no missing, no extra) (Req 2.1, 2.2, 2.3).
        expect(actualIds).toEqual(expected);

        // canNavigate agrees with the set for EVERY screen id (Req 2.4):
        // exactly the expected screens are navigable, nothing else.
        for (const id of ALL_SCREEN_IDS) {
          expect(canNavigate(state, id)).toBe(expected.has(id));
        }

        // Unauthenticated (or roleless): only login is reachable (Req 2.3).
        if (!state.authenticated || state.role === null) {
          expect(actualIds).toEqual(new Set<ScreenId>(['login']));
          expect(defaultPathFor(state)).toBe(LOGIN_SCREEN.path);
        } else {
          // Authenticated: login is never navigable; home is inside the role set
          // (or /login for a role with no screens).
          expect(canNavigate(state, 'login')).toBe(false);
          const home = defaultPathFor(state);
          if (expected.size > 0) {
            const paths = navScreensFor(state).map((s) => s.path);
            expect(paths).toContain(home);
          } else {
            expect(home).toBe(LOGIN_SCREEN.path);
          }
        }
        return true;
      }),
      { numRuns: 200 },
    );
  });
});
