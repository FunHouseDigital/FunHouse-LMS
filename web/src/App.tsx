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

export function AppShell() {
  return (
    <SyncStatusProvider>
      <ServicesProvider>
        <ReferenceDataProvider>
          <AppShellNav />
          <ReferenceDataStatus />
          <AppRoutes />
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
