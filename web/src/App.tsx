/**
 * Application shell.
 *
 * Normal operational routes compose account-scoped reference-data hydration,
 * automatic sync services, role-gated navigation, and guarded routes. The
 * field-acceptance route deliberately uses a read-only provider boundary: it
 * can inspect payload-free sync status but cannot mount automatic flush,
 * retry, protected reference-data reads, or cache writes.
 */
import { BrowserRouter, matchPath, useLocation } from 'react-router-dom';
import { AuthProvider, useAuth } from './state/authState';
import { SyncStatusProvider } from './state/syncState';
import { ServicesProvider } from './state/servicesState';
import { ReferenceDataProvider } from './state/referenceDataState';
import { AppShellNav } from './ui/AppShellNav';
import { ReferenceDataStatus } from './ui/ReferenceDataStatus';
import { AppRoutes } from './ui/AppRoutes';
import { APP_RELEASE_ID } from './domain/release';

function ApplicationFrame({ readOnlyFieldAcceptance }: { readOnlyFieldAcceptance: boolean }) {
  const { isAuthenticated } = useAuth();

  return (
    <>
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <div className={`app-shell ${isAuthenticated ? 'is-authenticated' : 'is-guest'}`}>
        <AppShellNav readOnlySync={readOnlyFieldAcceptance} />
        <div className="app-workspace">
          {isAuthenticated && !readOnlyFieldAcceptance && (
            <div className="app-statusbar">
              <ReferenceDataStatus />
            </div>
          )}
          <main id="main-content" tabIndex={-1}>
            <AppRoutes />
          </main>
        </div>
        <footer className="release-identifier" aria-label="Application release">
          Release <code>{APP_RELEASE_ID}</code>
        </footer>
      </div>
    </>
  );
}

export function AppShell() {
  const location = useLocation();
  const readOnlyFieldAcceptance = matchPath(
    { path: '/field-acceptance', end: true },
    location.pathname,
  ) !== null;

  return (
    <SyncStatusProvider>
      {readOnlyFieldAcceptance ? (
        <ApplicationFrame readOnlyFieldAcceptance />
      ) : (
        <ServicesProvider>
          <ReferenceDataProvider>
            <ApplicationFrame readOnlyFieldAcceptance={false} />
          </ReferenceDataProvider>
        </ServicesProvider>
      )}
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
