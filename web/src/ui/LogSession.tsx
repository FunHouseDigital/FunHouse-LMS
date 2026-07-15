/**
 * Log Session screen — `Session_Logger` (Req 7, 8.1). See design.md "Log
 * Session — Session_Logger".
 *
 * Controls: player (search or recent-players list from the Local_Store), console
 * (PS5|PS4), duration presets (20|60|120) + custom minutes, and a payment method
 * (cash amount or entitlement draw). The entitlement option is gated by the
 * Entitlement_Calculator: it is disabled when the optimistic remaining is less
 * than the requested duration or is negative (Req 8.4, 8.6). The confirm control
 * is enabled only when a player, a duration, and a payment method are chosen
 * (Req 7.6). Confirming builds the actions with a pure builder and commits them
 * locally — no network on the capture path (Req 7.8).
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useServices } from '../state/servicesState';
import { getEntitlementDisplays } from '../domain/entitlementCalculator';
import type { EntitlementDisplay } from '../domain/entitlementCalculator';
import {
  DURATION_PRESETS,
  buildSessionActions,
  canConfirmSession,
  type ConsoleOption,
  type SessionInput,
  type SessionPayment,
} from '../domain/captures/session';
import { useKnownPlayers } from './useKnownPlayers';

export function LogSession() {
  const { commit } = useServices();
  const players = useKnownPlayers();

  const [search, setSearch] = useState('');
  const [playerId, setPlayerId] = useState<string | null>(null);
  const [consoleChoice, setConsoleChoice] = useState<ConsoleOption>('PS5');
  const [durationMinutes, setDurationMinutes] = useState<number | null>(null);
  const [customMinutes, setCustomMinutes] = useState('');
  const [paymentMethod, setPaymentMethod] = useState<'cash' | 'entitlement' | null>(null);
  const [cashRand, setCashRand] = useState('');
  const [entitlementId, setEntitlementId] = useState<string | null>(null);
  const [displays, setDisplays] = useState<EntitlementDisplay[]>([]);
  const [confirmed, setConfirmed] = useState(false);

  // Load the selected player's optimistic entitlement displays (Req 8.1, 8.2).
  useEffect(() => {
    let alive = true;
    if (!playerId) {
      setDisplays([]);
      return;
    }
    void (async () => {
      const next = await getEntitlementDisplays(playerId);
      if (alive) setDisplays(next);
    })();
    return () => {
      alive = false;
    };
  }, [playerId]);

  const recent = useMemo(() => players.slice(0, 5), [players]);
  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (needle === '') return recent;
    return players.filter((p) => p.name.toLowerCase().includes(needle));
  }, [players, recent, search]);

  const effectiveDuration = useMemo(() => {
    if (durationMinutes !== null) return durationMinutes;
    const n = Number(customMinutes.trim());
    return Number.isFinite(n) && n > 0 ? n : null;
  }, [durationMinutes, customMinutes]);

  /** Optimistic remaining for a display, as a number (unlimited → +Infinity). */
  const remainingOf = (d: EntitlementDisplay): number =>
    d.remaining === 'unlimited' ? Number.POSITIVE_INFINITY : d.remaining;

  const buildPayment = useCallback((): SessionPayment | null => {
    if (paymentMethod === 'cash') {
      const rand = Number(cashRand.trim());
      if (!Number.isFinite(rand) || rand < 0) return null;
      return { method: 'cash', amountCents: Math.round(rand * 100) };
    }
    if (paymentMethod === 'entitlement' && entitlementId) {
      return { method: 'entitlement', entitlementId };
    }
    return null;
  }, [paymentMethod, cashRand, entitlementId]);

  const draft: Partial<SessionInput> | null = useMemo(() => {
    if (!playerId || effectiveDuration === null) return null;
    const payment = buildPayment();
    return payment ? { playerId, console: consoleChoice, durationMinutes: effectiveDuration, payment } : { playerId, durationMinutes: effectiveDuration };
  }, [playerId, effectiveDuration, consoleChoice, buildPayment]);

  const confirmEnabled = canConfirmSession(draft) && buildPayment() !== null;

  const onConfirm = useCallback(async () => {
    if (!playerId || effectiveDuration === null) return;
    const payment = buildPayment();
    if (!payment) return;
    const input: SessionInput = {
      playerId,
      console: consoleChoice,
      durationMinutes: effectiveDuration,
      payment,
    };
    const result = buildSessionActions(input, {
      now: new Date().toISOString(),
      newId: () => globalThis.crypto.randomUUID(),
    });
    await commit(result);
    setConfirmed(true);
    // Reset the payment leg for the next capture.
    setPaymentMethod(null);
    setCashRand('');
    setEntitlementId(null);
    setDurationMinutes(null);
    setCustomMinutes('');
  }, [playerId, effectiveDuration, consoleChoice, buildPayment, commit]);

  return (
    <section aria-label="Log Session" data-screen-body="log-session">
      <h1>Log Session</h1>

      <fieldset>
        <legend>Player</legend>
        <input
          type="search"
          aria-label="Search players"
          placeholder="Search players"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <ul aria-label="Players">
          {filtered.map((p) => (
            <li key={p.id}>
              <button
                type="button"
                aria-pressed={playerId === p.id}
                onClick={() => setPlayerId(p.id)}
              >
                {p.name}
              </button>
            </li>
          ))}
        </ul>
      </fieldset>

      <fieldset>
        <legend>Console</legend>
        {(['PS5', 'PS4'] as ConsoleOption[]).map((c) => (
          <label key={c}>
            <input
              type="radio"
              name="console"
              value={c}
              checked={consoleChoice === c}
              onChange={() => setConsoleChoice(c)}
            />
            {c}
          </label>
        ))}
      </fieldset>

      <fieldset>
        <legend>Duration</legend>
        {DURATION_PRESETS.map((preset) => (
          <button
            key={preset}
            type="button"
            aria-pressed={durationMinutes === preset}
            onClick={() => {
              setDurationMinutes(preset);
              setCustomMinutes('');
            }}
          >
            {preset} min
          </button>
        ))}
        <label>
          Custom minutes
          <input
            type="number"
            min={1}
            aria-label="Custom minutes"
            value={customMinutes}
            onChange={(e) => {
              setCustomMinutes(e.target.value);
              setDurationMinutes(null);
            }}
          />
        </label>
      </fieldset>

      <fieldset>
        <legend>Payment</legend>
        <label>
          <input
            type="radio"
            name="payment"
            value="cash"
            checked={paymentMethod === 'cash'}
            onChange={() => setPaymentMethod('cash')}
          />
          Cash
        </label>
        {paymentMethod === 'cash' && (
          <label>
            Amount (R)
            <input
              type="number"
              min={0}
              step="0.01"
              aria-label="Cash amount"
              value={cashRand}
              onChange={(e) => setCashRand(e.target.value)}
            />
          </label>
        )}

        <label>
          <input
            type="radio"
            name="payment"
            value="entitlement"
            checked={paymentMethod === 'entitlement'}
            onChange={() => setPaymentMethod('entitlement')}
            disabled={displays.length === 0}
          />
          Entitlement draw
        </label>

        {paymentMethod === 'entitlement' && (
          <ul aria-label="Entitlements">
            {displays.map((d) => {
              const remaining = remainingOf(d);
              const blocked =
                remaining < 0 || (effectiveDuration !== null && effectiveDuration > remaining);
              return (
                <li key={d.entitlement_id}>
                  <button
                    type="button"
                    aria-pressed={entitlementId === d.entitlement_id}
                    disabled={blocked}
                    onClick={() => setEntitlementId(d.entitlement_id)}
                  >
                    {d.product_id} — remaining{' '}
                    {d.remaining === 'unlimited' ? 'unlimited' : `${d.remaining} min`}
                    {d.valid_to ? ` (until ${d.valid_to})` : ''}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </fieldset>

      {/* Pre-confirm balance display for the selected player (Req 8.1). */}
      {playerId && displays.length > 0 && (
        <section aria-label="Entitlement balance">
          {displays.map((d) => (
            <p key={d.entitlement_id} data-entitlement={d.entitlement_id}>
              {d.product_id}: {d.remaining === 'unlimited' ? 'unlimited' : `${d.remaining} min`} remaining
              {d.valid_to ? `, valid to ${d.valid_to}` : ''} ({d.status})
            </p>
          ))}
        </section>
      )}

      <button type="button" disabled={!confirmEnabled} onClick={onConfirm}>
        Confirm session
      </button>

      {confirmed && <p role="status">Session logged</p>}
    </section>
  );
}

export default LogSession;
