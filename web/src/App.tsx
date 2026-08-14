/**
 * Application shell.
 *
 * Composes authentication, account-scoped reference-data hydration, sync status,
 * capture services, role-gated navigation, and guarded application routes.
 * Players and products are refreshed after login and retained in IndexedDB for
 * offline capture on subsequent launches.
 *
 * The sync-status and capture-services providers live INSIDE {@link AppShell} so
 * the shell is self-contained given an {@link AuthProvider} (and so component
 * tests can render `AppShell` under a `MemoryRouter` without re-wiring them).
 */
import { BrowserRouter } from 'react-router-dom';
import { AuthProvider } from './state/authState';
import { SyncStatusProvider } from './state/syncState';
import { ServicesProvider } from './state/servicesState';
import { ReferenceDataProvider } from './state/referenceDataState';
import { AppShellNav } from './ui/AppShellNav';
import { ReferenceDataStatus } from './ui/ReferenceDataStatus';
import { AppRoutes } from './ui/AppRoutes';
import { useAuth } from './state/authState';

/** Identifies the exact app shell executing on this device. */
const RELEASE_ID =
  typeof __APP_RELEASE_ID__ === 'string' ? __APP_RELEASE_ID__ : 'local';

export function AppShell() {
  const { isAuthenticated } = useAuth();

  return (
    <SyncStatusProvider>
      <ServicesProvider>
        <ReferenceDataProvider>
          <a className="skip-link" href="#main-content">
            Skip to content
          </a>
          <div className={`app-shell ${isAuthenticated ? 'is-authenticated' : 'is-guest'}`}>
            <AppShellNav />
            <div className="app-workspace">
              {isAuthenticated && (
                <div className="app-statusbar">
                  <ReferenceDataStatus />
                </div>
              )}
              <main id="main-content" tabIndex={-1}>
                <AppRoutes />
              </main>
            </div>
            <footer className="release-identifier" aria-label="Application release">
              Release <code>{RELEASE_ID}</code>
            </footer>
          </div>
        </ReferenceDataProvider>
      </ServicesProvider>
    </SyncStatusProvider>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <AppShell />
      </BrowserRouter>
    </AuthProvider>
  );
}
