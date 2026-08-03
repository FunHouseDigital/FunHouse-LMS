/**
 * Today screen (Req 11). See design.md "Today".
 *
 * Shows the running cash total captured today, the count of today's sessions,
 * the cash total against the R550 monthly pace target, and the current unsynced
 * count (zero when none). Everything is computed from Local_Store records so the
 * screen renders offline (Req 11.5); the unsynced count comes from the sync
 * status state (Req 11.4).
 */
import { useEffect, useMemo, useState } from 'react';
import { useSyncStatus } from '../state/syncState';
import { useReferenceData } from '../state/referenceDataState';
import { getAllLocalRecords, type LocalRecord } from '../store/localStore';
import {
  MONTHLY_PACE_TARGET_RAND,
  computeTodayTotals,
  formatRand,
  paceFraction,
  type TodayTotals,
} from '../domain/today';

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

export function Today() {
  const { unsyncedCount } = useSyncStatus();
  const { cacheScope } = useReferenceData();
  const [totals, setTotals] = useState<TodayTotals>({ cashTotalCents: 0, sessionCount: 0 });
  const day = useMemo(() => todayIso(), []);

  useEffect(() => {
    let alive = true;
    void (async () => {
      const payments: LocalRecord[] = await getAllLocalRecords('payments', cacheScope);
      const sessions: LocalRecord[] = await getAllLocalRecords('sessions', cacheScope);
      const next = computeTodayTotals(payments, sessions, day);
      if (alive) setTotals(next);
    })();
    return () => {
      alive = false;
    };
  }, [cacheScope, day, unsyncedCount]);

  const pace = paceFraction(totals.cashTotalCents);

  return (
    <section aria-label="Today" data-screen-body="today">
      <h1>Today</h1>

      <p data-field="cash-total">
        Cash today: <strong>{formatRand(totals.cashTotalCents)}</strong>
      </p>
      <p data-field="session-count">
        Sessions today: <strong>{totals.sessionCount}</strong>
      </p>
      <p data-field="pace">
        Pace vs R{MONTHLY_PACE_TARGET_RAND} target: <strong>{Math.round(pace * 100)}%</strong>
      </p>
      <p data-field="unsynced">
        Unsynced: <strong>{unsyncedCount}</strong>
      </p>
    </section>
  );
}

export default Today;
