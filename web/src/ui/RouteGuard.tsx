/**
 * Route guard (Req 2). See design.md "Role-gated navigation".
 *
 * Wraps a screen and enforces the role → navigable-screen mapping from
 * `domain/navigation`. When the current auth state may not access the wrapped
 * screen, it redirects to the appropriate home:
 *  - unauthenticated + protected screen → `/login` (Req 2.3);
 *  - authenticated + a screen outside the role → the role's home (Req 2.4);
 *  - authenticated + `/login` → the role's home (already signed in).
 */
import type { ReactNode } from 'react';
import { Navigate } from 'react-router-dom';
import { useAuth } from '../state/authState';
import { canNavigate, defaultPathFor, type NavAuthState, type ScreenId } from '../domain/navigation';

export interface RouteGuardProps {
  screen: ScreenId;
  children: ReactNode;
}

export function RouteGuard({ screen, children }: RouteGuardProps) {
  const { isAuthenticated, role } = useAuth();
  const state: NavAuthState = { authenticated: isAuthenticated, role };

  if (!canNavigate(state, screen)) {
    return <Navigate to={defaultPathFor(state)} replace />;
  }
  return <>{children}</>;
}

export default RouteGuard;
