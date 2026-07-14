/**
 * App shell navigation (Req 2). See design.md "Role-gated navigation".
 *
 * Renders navigation controls for exactly the screens the current role permits
 * (Req 2.1, 2.2, 2.4). While unauthenticated, no role navigation is shown — the
 * app restricts navigation to the login screen only (Req 2.3). A logout control
 * is shown while authenticated.
 */
import { NavLink } from 'react-router-dom';
import { useAuth } from '../state/authState';
import { navScreensFor, type NavAuthState } from '../domain/navigation';

export function AppShellNav() {
  const { isAuthenticated, role, logout } = useAuth();
  const state: NavAuthState = { authenticated: isAuthenticated, role };

  // Unauthenticated: navigation is restricted to login only (Req 2.3) — the
  // login screen carries no nav chrome, so render nothing here.
  if (!isAuthenticated) {
    return null;
  }

  const screens = navScreensFor(state);

  return (
    <nav aria-label="Primary">
      <ul>
        {screens.map((screen) => (
          <li key={screen.id}>
            <NavLink to={screen.path} data-screen={screen.id}>
              {screen.label}
            </NavLink>
          </li>
        ))}
      </ul>
      <button type="button" onClick={logout}>
        Log out
      </button>
    </nav>
  );
}

export default AppShellNav;
