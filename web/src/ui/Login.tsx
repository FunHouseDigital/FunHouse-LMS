/**
 * Login screen (Req 1.1, 1.3, 1.4). See design.md "Auth_Manager (Login)".
 *
 * Renders identifier + password fields with field-level validation that blocks
 * submission on empty input (Req 1.4), and wires submission to the Auth_Manager
 * via the auth context. A `401` surfaces as a generic "invalid credentials"
 * message (Req 1.3); on success the auth state updates and the route guard
 * redirects the user to their role's home screen.
 *
 * The screen also offers a guided "Founder password reset" sub-view. It is
 * purely informational: the founder (Aya) credential can only be reset through
 * the owner-role "Rotate Live Founder Password" GitHub workflow, so this app
 * never sends, stores, or asks for a password/token during reset. It just
 * walks the founder through the safe out-of-band steps and dismisses back to
 * login. It deliberately triggers no auth calls, so the session-restoration
 * lifecycle is untouched.
 */
import { useState, type FormEvent } from 'react';
import { useAuth } from '../state/authState';
import {
  SecureStorageUnavailableError,
  validateCredentials,
  type FieldErrors,
} from '../domain/authManager';

const GITHUB_ENVIRONMENT_SECRETS_URL =
  'https://github.com/FunHouseDigital/FunHouse-LMS/settings/environments';
const ROTATE_FOUNDER_WORKFLOW_URL =
  'https://github.com/FunHouseDigital/FunHouse-LMS/actions/workflows/rotate-founder-password.yml';

/**
 * Guided, credential-free founder reset help. No password or token is ever
 * entered here; the actual reset happens in the owner-role GitHub workflow.
 */
function FounderResetHelp({ onBack }: { onBack: () => void }) {
  return (
    <div className="founder-reset-help">
      <h1 id="founder-reset-title">Founder password reset</h1>
      <p className="screen-intro">
        If the founder (Aya) password is lost, reset it from your project&rsquo;s GitHub. Never type
        a password or token on this screen.
      </p>
      <ol>
        <li>
          Open{' '}
          <a href={GITHUB_ENVIRONMENT_SECRETS_URL} target="_blank" rel="noreferrer noopener">
            GitHub &rarr; Environments &rarr; <code>production</code> &rarr; Environment secrets
          </a>{' '}
          and set <code>BOOTSTRAP_USER_PASSWORD</code> to a new password you have saved in your
          password manager.
        </li>
        <li>
          From the <code>main</code> branch, run the{' '}
          <a href={ROTATE_FOUNDER_WORKFLOW_URL} target="_blank" rel="noreferrer noopener">
            Rotate Live Founder Password
          </a>{' '}
          workflow and confirm with <code>rotate-live-founder-password</code>.
        </li>
        <li>
          Wait for the workflow&rsquo;s live founder login check to pass, then run{' '}
          <strong>Verify Live API Role Access</strong>.
        </li>
        <li>
          Return here and sign in as <strong>Aya</strong> with the new password.
        </li>
      </ol>
      <p role="note" className="founder-reset-note">
        Only someone with access to the GitHub <code>production</code> environment (the founder) can
        complete this. This screen never sends or stores a password.
      </p>
      <button
        type="button"
        className="button-secondary"
        onClick={onBack}
        data-testid="founder-reset-back"
      >
        Back to login
      </button>
    </div>
  );
}

export function Login() {
  const { login, submitting } = useAuth();
  const [identifier, setIdentifier] = useState('');
  const [password, setPassword] = useState('');
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [mode, setMode] = useState<'login' | 'reset-help'>('login');

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
    } catch (error) {
      if (error instanceof SecureStorageUnavailableError) {
        setFormError(
          'Secure storage is unavailable. Turn off private browsing or use a standard browser window.',
        );
      } else {
        // Network and API failures must never look like a successful no-op.
        setFormError('Unable to sign in. Check your connection and try again.');
      }
    }
  }

  return (
    <section
      className="login-screen"
      aria-labelledby={mode === 'login' ? 'login-title' : 'founder-reset-title'}
    >
      {mode === 'reset-help' ? (
        <FounderResetHelp onBack={() => setMode('login')} />
      ) : (
        <>
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

          <p className="login-reset-hint">
            <button
              type="button"
              className="button-secondary"
              onClick={() => setMode('reset-help')}
              data-testid="founder-reset-open"
            >
              Founder password reset
            </button>
          </p>
        </>
      )}
    </section>
  );
}

export default Login;
