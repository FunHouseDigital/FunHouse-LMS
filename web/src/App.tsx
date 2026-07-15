/**
 * Application shell.
 *
 * Composes the auth provider, the sync-status + capture-services providers, the
 * router, the role-gated navigation shell, and the guarded routes (Req 2, 4, 6).
 * The capture screens (task 11) are real; the read views (task 12) remain
 * placeholders until built.
 *
 * The sync-status and capture-services providers live INSIDE {@link AppShell} so
 * the shell is self-contained given an {@link AuthProvider} (and so component
 * tests can render `AppShell` under a `MemoryRouter` without re-wiring them).
 */
import { BrowserRouter } from 'react-router-dom';
import { AuthProvider } from './state/authState';
import { SyncStatusProvider } from './state/syncState';
import { ServicesProvider } from './state/servicesState';
import { AppShellNav } from './ui/AppShellNav';
import { AppRoutes } from './ui/AppRoutes';

export function AppShell() {
  return (
    <SyncStatusProvider>
      <ServicesProvider>
        <AppShellNav />
        <AppRoutes />
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
