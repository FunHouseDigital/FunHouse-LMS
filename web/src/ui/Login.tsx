/**
 * Login screen (Req 1.1, 1.3, 1.4). See design.md "Auth_Manager (Login)".
 *
 * Renders identifier + password fields with field-level validation that blocks
 * submission on empty input (Req 1.4), and wires submission to the Auth_Manager
 * via the auth context. A `401` surfaces as a generic "invalid credentials"
 * message (Req 1.3); on success the auth state updates and the route guard
 * redirects the user to their role's home screen.
 */
import { useState, type FormEvent } from 'react';
import { useAuth } from '../state/authState';
import { validateCredentials, type FieldErrors } from '../domain/authManager';

export function Login() {
  const { login, submitting } = useAuth();
  const [identifier, setIdentifier] = useState('');
  const [password, setPassword] = useState('');
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);

    // Client-side non-empty validation blocks submission (Req 1.4).
    const normalisedIdentifier = identifier.trim();
    const errors = validateCredentials(normalisedIdentifier, password);
    if (errors.identifier || errors.password) {
      setFieldErrors(errors);
      return;
    }
    setFieldErrors({});

    try {
      const outcome = await login(normalisedIdentifier, password);
      if (!outcome.ok) {
        if (outcome.kind === 'validation') {
          setFieldErrors(outcome.fieldErrors ?? {});
        } else {
          // Generic message for a 401 — no user enumeration (Req 1.3).
          setFormError('Invalid credentials');
        }
      }
    } catch {
      // Network, API, storage, and crypto failures must never look like a
      // successful no-op. Keep details out of the UI but give a useful retry.
      setFormError('Unable to sign in. Check your connection and try again.');
    }
  }

  return (
    <section className="login-screen" aria-labelledby="login-title">
      <h1 id="login-title">Log in</h1>
      <p className="screen-intro">Secure access to your FunHouse operations workspace.</p>
      <form onSubmit={onSubmit} noValidate aria-label="Login form">
        <div>
          <label htmlFor="identifier">Identifier</label>
          <input
            id="identifier"
            name="identifier"
            type="text"
            autoComplete="username"
            value={identifier}
            onChange={(e) => setIdentifier(e.target.value)}
            aria-invalid={fieldErrors.identifier ? true : undefined}
            aria-describedby={fieldErrors.identifier ? 'identifier-error' : undefined}
          />
          {fieldErrors.identifier && (
            <p id="identifier-error" role="alert">
              {fieldErrors.identifier}
            </p>
          )}
        </div>

        <div>
          <label htmlFor="password">Password</label>
          <input
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            aria-invalid={fieldErrors.password ? true : undefined}
            aria-describedby={fieldErrors.password ? 'password-error' : undefined}
          />
          {fieldErrors.password && (
            <p id="password-error" role="alert">
              {fieldErrors.password}
            </p>
          )}
        </div>

        {formError && (
          <p role="alert" data-testid="form-error">
            {formError}
          </p>
        )}

        <button type="submit" disabled={submitting}>
          {submitting ? 'Signing in…' : 'Log in'}
        </button>
      </form>
    </section>
  );
}

export default Login;
