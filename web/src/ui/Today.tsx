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
import { localDataLifecycleIdentity } from '../domain/personalData';
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
  const { cacheScope, owner } = useReferenceData();
  const identity = localDataLifecycleIdentity(owner);
  const [snapshot, setSnapshot] = useState<{ identity: string | null; totals: TodayTotals }>({
    identity: null,
    totals: { cashTotalCents: 0, sessionCount: 0 },
  });
  const day = useMemo(() => todayIso(), []);

  useEffect(() => {
    let alive = true;
    void (async () => {
      if (!owner) return;
      const payments: LocalRecord[] = await getAllLocalRecords('payments', owner);
      const sessions: LocalRecord[] = await getAllLocalRecords('sessions', owner);
      const next = computeTodayTotals(payments, sessions, day);
      if (alive && owner.isCurrent()) setSnapshot({ identity, totals: next });
    })();
    return () => {
      alive = false;
    };
  }, [cacheScope, day, identity, owner, unsyncedCount]);

  const totals = snapshot.identity === identity && owner?.isCurrent()
    ? snapshot.totals
    : { cashTotalCents: 0, sessionCount: 0 };

  const pace = paceFraction(totals.cashTotalCents);

  return (
    <section aria-label="Today" data-screen-body="today">
      <h1>Today</h1>
      <p className="screen-intro">A live view of today’s lounge activity on this device.</p>

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
