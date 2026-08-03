/**
 * Route configuration (Req 2). See design.md "Role-gated navigation".
 *
 * Every protected screen is registered as a real route wrapped in
 * {@link RouteGuard}, which enforces the role → screen mapping and redirects
 * disallowed access. The capture screens (task 11) and the read views
 * (Revenue Dashboard + Alerts, task 12) are all real components now.
 */
import { Navigate, Route, Routes } from 'react-router-dom';
import { useAuth } from '../state/authState';
import { RouteGuard } from './RouteGuard';
import { Login } from './Login';
import { PlaceholderScreen } from './PlaceholderScreen';
import { LogSession } from './LogSession';
import { Players } from './Players';
import { Learners } from './Learners';
import { PlayerDetail } from './PlayerDetail';
import { Today } from './Today';
import { Sell } from './Sell';
import { Registration } from './Registration';
import { Attendance } from './Attendance';
import { Metrics } from './Metrics';
import { RevenueDashboard } from './RevenueDashboard';
import { Alerts } from './Alerts';
import {
  ALL_PROTECTED_SCREENS,
  defaultPathFor,
  type NavAuthState,
  type ScreenId,
} from '../domain/navigation';

/** Real screen component for each protected screen id (task 11+). */
function ScreenBody({ id, label }: { id: ScreenId; label: string }) {
  switch (id) {
    case 'log-session':
      return <LogSession />;
    case 'players':
      return <Players />;
    case 'learners':
      return <Learners />;
    case 'today':
      return <Today />;
    case 'sell':
      return <Sell />;
    case 'attendance':
      return <Attendance />;
    case 'metrics':
      return <Metrics />;
    case 'revenue':
      return <RevenueDashboard />;
    case 'alerts':
      return <Alerts />;
    default:
      return <PlaceholderScreen title={label} screenId={id} />;
  }
}

export function AppRoutes() {
  const { isAuthenticated, role } = useAuth();
  const state: NavAuthState = { authenticated: isAuthenticated, role };
  const home = defaultPathFor(state);

  return (
    <Routes>
      <Route
        path="/login"
        element={
          <RouteGuard screen="login">
            <Login />
          </RouteGuard>
        }
      />

      {ALL_PROTECTED_SCREENS.map((screen) => (
        <Route
          key={screen.id}
          path={screen.path}
          element={
            <RouteGuard screen={screen.id}>
              <ScreenBody id={screen.id} label={screen.label} />
            </RouteGuard>
          }
        />
      ))}

      {/* Player detail is a manager screen nested under the Players section. */}
      <Route
        path="/players/:id"
        element={
          <RouteGuard screen="players">
            <PlayerDetail />
          </RouteGuard>
        }
      />

      {/* Registration is reachable from the Players section (manager). */}
      <Route
        path="/register"
        element={
          <RouteGuard screen="players">
            <Registration />
          </RouteGuard>
        }
      />

      {/* Anything else routes to the role's home (or /login when unauthenticated). */}
      <Route path="*" element={<Navigate to={home} replace />} />
    </Routes>
  );
}

export default AppRoutes;
