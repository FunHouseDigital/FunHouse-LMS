/**
 * Registration + consent screen — `Registration_Module` (Req 10, 17.5). See
 * design.md "Registration + consent — Registration_Module".
 *
 * Fields: player name, guardian phone, and four Consent_Type toggles. Submission
 * is blocked until a non-empty name is entered AND the on-screen guardian
 * confirmation is given (Req 10.2, 10.3). On submit the pure builder produces a
 * `player` record + action and one `consent` record + action per type; the
 * commit step encrypts personal fields at rest (Req 17.1). No national ID or
 * residential address is ever collected (Req 10.5, 17.5).
 */
import { useCallback, useMemo, useState } from 'react';
import { useServices } from '../state/servicesState';
import {
  CONSENT_TYPES,
  buildRegistrationActions,
  canSubmitRegistration,
  type RegistrationInput,
} from '../domain/captures/registration';
import type { ConsentType } from '../domain/types';

const CONSENT_LABELS: Record<ConsentType, string> = {
  media: 'Media',
  data_processing: 'Data processing',
  participation: 'Participation',
  communications: 'Communications',
};

function emptyConsents(): Record<ConsentType, boolean> {
  return {
    media: false,
    data_processing: false,
    participation: false,
    communications: false,
  };
}

export function Registration() {
  const { commit } = useServices();
  const [name, setName] = useState('');
  const [guardianPhone, setGuardianPhone] = useState('');
  const [consents, setConsents] = useState<Record<ConsentType, boolean>>(emptyConsents);
  const [guardianConfirmed, setGuardianConfirmed] = useState(false);
  const [saved, setSaved] = useState(false);

  const draft: Partial<RegistrationInput> = useMemo(
    () => ({ name, guardianPhone, consents, guardianConfirmed }),
    [name, guardianPhone, consents, guardianConfirmed],
  );
  const canSubmit = canSubmitRegistration(draft);

  const onSubmit = useCallback(async () => {
    const input: RegistrationInput = { name, guardianPhone, consents, guardianConfirmed };
    if (!canSubmitRegistration(input)) return;
    const result = buildRegistrationActions(input, {
      now: new Date().toISOString(),
      newId: () => globalThis.crypto.randomUUID(),
    });
    await commit(result);
    setSaved(true);
    setName('');
    setGuardianPhone('');
    setConsents(emptyConsents());
    setGuardianConfirmed(false);
  }, [name, guardianPhone, consents, guardianConfirmed, commit]);

  return (
    <section aria-label="Registration" data-screen-body="registration">
      <h1>Add player</h1>

      <label>
        Player name
        <input
          type="text"
          aria-label="Player name"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </label>

      <label>
        Guardian phone
        <input
          type="tel"
          aria-label="Guardian phone"
          value={guardianPhone}
          onChange={(e) => setGuardianPhone(e.target.value)}
        />
      </label>

      <fieldset>
        <legend>Guardian consent</legend>
        {CONSENT_TYPES.map((type) => (
          <label key={type}>
            <input
              type="checkbox"
              aria-label={CONSENT_LABELS[type]}
              checked={consents[type]}
              onChange={(e) => setConsents((prev) => ({ ...prev, [type]: e.target.checked }))}
            />
            {CONSENT_LABELS[type]}
          </label>
        ))}
      </fieldset>

      <label>
        <input
          type="checkbox"
          aria-label="Guardian confirmation"
          checked={guardianConfirmed}
          onChange={(e) => setGuardianConfirmed(e.target.checked)}
        />
        Guardian is present and confirms consent
      </label>

      <button type="button" disabled={!canSubmit} onClick={onSubmit}>
        Register player
      </button>

      {saved && <p role="status">Player registered</p>}
    </section>
  );
}

export default Registration;
