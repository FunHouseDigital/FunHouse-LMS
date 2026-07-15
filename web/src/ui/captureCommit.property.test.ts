import { describe, it, expect, afterEach } from 'vitest';
import fc from 'fast-check';
import { commitCapture } from './captureCommit';
import { SyncEngine, SyncScheduler } from '../domain/syncEngine';
import { DB_NAME, closeDb, countUnsynced } from '../store/localStore';
import { buildSessionActions } from '../domain/captures/session';
import { buildRegistrationActions } from '../domain/captures/registration';
import { buildSellActions } from '../domain/captures/sell';
import { buildAttendanceActions } from '../domain/captures/attendance';
import { buildMetricsActions } from '../domain/captures/metrics';
import type { CaptureContext, CaptureResult } from '../domain/captures/types';
import type { SyncAction, SyncResult } from '../domain/types';

async function resetDb(): Promise<void> {
  await closeDb();
  await new Promise<void>((resolve, reject) => {
    const req = indexedDB.deleteDatabase(DB_NAME);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
    req.onblocked = () => resolve();
  });
}

function ctx(): CaptureContext {
  let n = 0;
  return { now: '2024-06-15T10:00:00.000Z', newId: () => globalThis.crypto.randomUUID() + `-${n++}` };
}

/** A spying API client that records any `sync` invocation. */
function makeSpyClient() {
  let calls = 0;
  const client = {
    sync(_actions: SyncAction[]): Promise<SyncResult> {
      calls += 1;
      return Promise.resolve({ results: [] });
    },
  };
  return { client, calls: () => calls };
}

const captureArb: fc.Arbitrary<(c: CaptureContext) => CaptureResult> = fc.oneof(
  fc.constant((c: CaptureContext) =>
    buildSessionActions(
      { playerId: 'p1', console: 'PS5', durationMinutes: 60, payment: { method: 'cash', amountCents: 3000 } },
      c,
    ),
  ),
  fc.constant((c: CaptureContext) =>
    buildRegistrationActions(
      { name: 'Ada', guardianConfirmed: true, consents: { media: true, data_processing: true, participation: false, communications: false } },
      c,
    ),
  ),
  fc.constant((c: CaptureContext) =>
    buildSellActions({ kind: 'subscription', playerId: 'p1', priceCents: 35_000, productId: 'prod', memberIds: ['p1', 'p2'] }, c),
  ),
  fc.constant((c: CaptureContext) =>
    buildAttendanceActions(
      { sessionType: 'lesson', roster: [{ playerId: 'p1', present: true }, { playerId: 'p2', present: false }] },
      c,
    ),
  ),
  fc.constant((c: CaptureContext) => buildMetricsActions({ studentName: 'S', wpm: 40, accuracy: 95 }, c)),
);

describe('Offline capture performs no network call (Property 4)', () => {
  // Feature: revenue-pwa, Property 4: Offline capture performs no network call. For any
  // capture executed while offline, the capture completes successfully and the Container_API
  // client is never invoked.
  // Validates: Requirements 4.4, 7.8, 10.6, 12.5, 14.5, 15.4
  it('Property 4: committing a capture offline never invokes the API client', async () => {
    const originalOnLine = Object.getOwnPropertyDescriptor(navigator, 'onLine');
    Object.defineProperty(navigator, 'onLine', { value: false, configurable: true });
    try {
      await fc.assert(
        fc.asyncProperty(captureArb, async (buildFn) => {
          await resetDb();
          const spy = makeSpyClient();
          const engine = new SyncEngine({ client: spy.client });
          const scheduler = new SyncScheduler({ flush: () => engine.flush(), countUnsynced });

          const result = buildFn(ctx());
          // Pass an explicit null session key so we exercise the no-crypto path deterministically.
          await commitCapture(result, { scheduler, sessionKey: null });

          // The capture completed: its actions are queued locally...
          const queued = await countUnsynced();
          const nonBlocked = result.actions.filter((a) => a.status !== 'blocked').length;
          expect(queued).toBe(nonBlocked);
          // ...and no network call was made on the capture path (Req 4.4).
          expect(spy.calls()).toBe(0);
          return true;
        }),
        { numRuns: 100 },
      );
    } finally {
      if (originalOnLine) Object.defineProperty(navigator, 'onLine', originalOnLine);
    }
  });

  afterEach(async () => {
    await closeDb();
  });
});
