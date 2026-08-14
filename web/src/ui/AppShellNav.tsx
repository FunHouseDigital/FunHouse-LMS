/**
 * App shell navigation (Req 2). See design.md "Role-gated navigation".
 *
 * Renders navigation controls for exactly the screens the current role permits
 * (Req 2.1, 2.2, 2.4). While unauthenticated, no role navigation is shown — the
 * app restricts navigation to the login screen only (Req 2.3). A logout control
 * is shown while authenticated.
 */
import { useState } from 'react';
import { NavLink } from 'react-router-dom';
import { useAuth } from '../state/authState';
import { navScreensFor, type NavAuthState } from '../domain/navigation';
import { SyncStatusSurface } from './SyncStatusSurface';

export function AppShellNav() {
  const { isAuthenticated, role, logout } = useAuth();
  const [confirmingLogout, setConfirmingLogout] = useState(false);
  const state: NavAuthState = { authenticated: isAuthenticated, role };

  // Unauthenticated: navigation is restricted to login only (Req 2.3) — the
  // login screen carries no nav chrome, so render nothing here.
  if (!isAuthenticated) {
    return null;
  }

  const screens = navScreensFor(state);

  return (
    <aside className="app-sidebar">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true">FH</span>
        <span className="brand-copy">
          <span className="brand-name">FunHouse</span>
          <span className="brand-tagline">Revenue workspace</span>
        </span>
      </div>
      <nav aria-label="Primary">
        <ul className="primary-links">
          {screens.map((screen) => (
            <li key={screen.id}>
              <NavLink to={screen.path} data-screen={screen.id}>
                {screen.label}
              </NavLink>
            </li>
          ))}
        </ul>
        <div className="sidebar-utility">
          {role && <span className="role-chip">{role}</span>}
          <SyncStatusSurface />
          {confirmingLogout ? (
            <div className="logout-confirmation" role="group" aria-label="Confirm logout">
              <p>Sign out of this device?</p>
              <button className="sidebar-logout" type="button" onClick={logout}>
                Yes, log out
              </button>
              <button type="button" onClick={() => setConfirmingLogout(false)}>
                Stay signed in
              </button>
            </div>
          ) : (
            <button
              className="sidebar-logout"
              type="button"
              onClick={() => setConfirmingLogout(true)}
            >
              Log out
            </button>
          )}
        </div>
      </nav>
    </aside>
  );
}

export default AppShellNav;
