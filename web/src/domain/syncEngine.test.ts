import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
  SyncEngine,
  SyncScheduler,
  BACKGROUND_SYNC_TAG,
  isBackgroundSyncSupported,
  registerBackgroundSync,
} from './syncEngine';
import {
  DB_NAME,
  closeDb,
  enqueueAction,
  getAction,
  countUnsynced,
  getLastSuccessfulSync,
} from '../store/localStore';
import type { ActionResult, SyncAction, SyncResult } from './types';

async function resetDb(): Promise<void> {
  await closeDb();
  await new Promise<void>((resolve, reject) => {
    const req = indexedDB.deleteDatabase(DB_NAME);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
    req.onblocked = () => resolve();
  });
}

function makeMock(handler: (actions: SyncAction[]) => SyncResult | Promise<SyncResult>) {
  const batches: SyncAction[][] = [];
  const client = {
    async sync(actions: SyncAction[]): Promise<SyncResult> {
      batches.push(actions.map((a) => ({ ...a, payload: { ...(a.payload as object) } })));
      return handler(actions);
    },
  };
  return { batches, client };
}

beforeEach(async () => {
  await resetDb();
});

describe('Sync_Engine — integration capture→queue→sync→reconcile (task 8.11)', () => {
  it('reconciles a mixed batch: applied/skipped removed, rejected retained with reason', async () => {
    // Simulate captures having enqueued actions.
    await enqueueAction({ client_id: 'c1', entity: 'session', created_at: '2024-01-01T10:00:00.000Z', payload: { player_id: 'srv-1' } });
    await enqueueAction({ client_id: 'c2', entity: 'payment', created_at: '2024-01-01T10:01:00.000Z', payload: { player_id: 'srv-1', amount_cents: 3000 } });
    await enqueueAction({ client_id: 'c3', entity: 'consent', created_at: '2024-01-01T10:02:00.000Z', payload: { player_id: 'srv-1', consent_type: 'media' } });

    const mock = makeMock(() => ({
      results: [
        { client_id: 'c1', entity: 'session', status: 'applied', record_id: 'S1', reason: null },
        { client_id: 'c2', entity: 'payment', status: 'skipped', record_id: 'P1', reason: null },
        { client_id: 'c3', entity: 'consent', status: 'rejected', record_id: null, reason: 'duplicate consent' },
      ] satisfies ActionResult[],
    }));

    const engine = new SyncEngine({ client: mock.client });
    const result = await engine.flush();

    expect(result.outcome).toBe('ok');
    expect(result.applied).toBe(1);
    expect(result.skipped).toBe(1);
    expect(result.rejected).toBe(1);

    expect((await getAction('c1'))!.status).toBe('applied');
    expect((await getAction('c2'))!.status).toBe('skipped');
    const rejected = await getAction('c3');
    expect(rejected!.status).toBe('rejected');
    expect(rejected!.reason).toBe('duplicate consent');

    // Only the rejected action remains unsynced-excluded but retained; unsynced set empty.
    expect(await countUnsynced()).toBe(0);
    // A 200 advances the last-successful-sync marker.
    expect(await getLastSuccessfulSync()).not.toBeNull();
  });

  it('retains the queue and reports network-error when POST /sync throws', async () => {
    await enqueueAction({ client_id: 'n1', entity: 'session', created_at: '2024-01-01T10:00:00.000Z', payload: {} });
    const mock = makeMock(() => {
      throw new Error('offline');
    });
    const engine = new SyncEngine({ client: mock.client });
    const result = await engine.flush();
    expect(result.outcome).toBe('network-error');
    expect(await countUnsynced()).toBe(1);
    const stored = await getAction('n1');
    expect(stored!.status).toBe('unsynced');
    expect(stored!.attempt_count).toBe(1);
    expect(await getLastSuccessfulSync()).toBeNull();
  });

  it('includes live student_metrics actions in the batch and reconciles them (D1 resolved)', async () => {
    // D1 resolved: student_metrics is now a live /sync entity keyed on player_id.
    // Metrics enqueue as normal `unsynced` actions and must be transmitted and
    // reconciled like the other natural-key entities.
    await enqueueAction({ client_id: 'm1', entity: 'student_metrics', created_at: '2024-01-01T10:00:00.000Z', payload: { player_id: 'srv-1', metric_type: 'typing_wpm', value: 42, measured_at: '2024-01-01T10:00:00.000Z' } });
    await enqueueAction({ client_id: 's1', entity: 'session', created_at: '2024-01-01T10:00:00.000Z', payload: { player_id: 'srv-1' } });

    const mock = makeMock((batch) => ({
      results: batch.map<ActionResult>((a) => ({ client_id: a.client_id, entity: a.entity, status: 'applied', record_id: null, reason: null })),
    }));
    const engine = new SyncEngine({ client: mock.client });
    const result = await engine.flush();

    // The metrics action was transmitted alongside the session action...
    const sentIds = mock.batches.flat().map((a) => a.client_id);
    expect(sentIds).toContain('m1');
    expect(sentIds).toContain('s1');
    // ...carried its player_id natural key on the wire...
    const sentMetric = mock.batches.flat().find((a) => a.client_id === 'm1')!;
    expect((sentMetric.payload as { player_id: string }).player_id).toBe('srv-1');
    // ...and was reconciled applied like any other entity.
    expect(result.applied).toBe(2);
    expect((await getAction('m1'))!.status).toBe('applied');
    expect(await countUnsynced()).toBe(0);
  });

  it('resolves a metrics action captured for an offline-registered player (D2)', async () => {
    // A player registered offline; a metric captured for that local player id
    // must get the same D2 local-id rewrite session/payment get, ordered after
    // the player action.
    await enqueueAction({ client_id: 'PLAYER', entity: 'player', created_at: '2024-01-01T09:00:00.000Z', payload: { first_name: 'Zia' } });
    await enqueueAction({ client_id: 'METRIC', entity: 'student_metrics', created_at: '2024-01-01T09:00:05.000Z', payload: { player_id: 'PLAYER', metric_type: 'typing_accuracy', value: 96, measured_at: '2024-01-01T09:00:05.000Z' } });

    const mock = makeMock((batch) => ({
      results: batch.map<ActionResult>((a) => ({
        client_id: a.client_id,
        entity: a.entity,
        status: 'applied',
        record_id: a.entity === 'player' ? 'SERVER-PLAYER-9' : `srv-${a.client_id}`,
        reason: null,
      })),
    }));
    const engine = new SyncEngine({ client: mock.client });
    const result = await engine.flush();
    expect(result.outcome).toBe('ok');

    // Phase 1 sent the player; phase 2 sent the metric with the resolved id.
    expect(mock.batches[0].map((a) => a.client_id)).toEqual(['PLAYER']);
    const phase2 = mock.batches[1];
    expect(phase2.map((a) => a.client_id)).toEqual(['METRIC']);
    expect((phase2[0].payload as { player_id: string }).player_id).toBe('SERVER-PLAYER-9');
    expect(await countUnsynced()).toBe(0);
  });
});

describe('Sync_Engine — Dependency D2 local-id resolution (task 8.6)', () => {
  it('sends the player first, then resolves dependents player_id from the applied record_id', async () => {
    // A player registered offline: dependent actions reference the player action's client_id.
    await enqueueAction({ client_id: 'PLAYER', entity: 'player', created_at: '2024-01-01T09:00:00.000Z', payload: { first_name: 'Ada' } });
    await enqueueAction({ client_id: 'CONSENT', entity: 'consent', created_at: '2024-01-01T09:00:01.000Z', payload: { player_id: 'PLAYER', consent_type: 'media', granted: true } });
    await enqueueAction({ client_id: 'SESSION', entity: 'session', created_at: '2024-01-01T09:00:02.000Z', payload: { player_id: 'PLAYER', session_type: 'lounge' } });

    const mock = makeMock((batch) => ({
      results: batch.map<ActionResult>((a) => ({
        client_id: a.client_id,
        entity: a.entity,
        status: 'applied',
        record_id: a.entity === 'player' ? 'SERVER-PLAYER-1' : `srv-${a.client_id}`,
        reason: null,
      })),
    }));

    const engine = new SyncEngine({ client: mock.client });
    const result = await engine.flush();
    expect(result.outcome).toBe('ok');

    // Phase 1 carried only the player (dependents deferred until it resolved).
    expect(mock.batches[0].map((a) => a.client_id)).toEqual(['PLAYER']);
    // Phase 2 carried the dependents with the resolved server player_id.
    const phase2 = mock.batches[1];
    expect(phase2.map((a) => a.client_id).sort()).toEqual(['CONSENT', 'SESSION']);
    for (const sent of phase2) {
      expect((sent.payload as { player_id: string }).player_id).toBe('SERVER-PLAYER-1');
    }

    expect(await countUnsynced()).toBe(0);
  });

  it('resolves a dependent captured after the player already synced (cross-flush, via stored mapping)', async () => {
    await enqueueAction({ client_id: 'PLAYER', entity: 'player', created_at: '2024-01-01T09:00:00.000Z', payload: { first_name: 'Ben' } });
    const mock = makeMock((batch) => ({
      results: batch.map<ActionResult>((a) => ({ client_id: a.client_id, entity: a.entity, status: 'applied', record_id: 'SERVER-PLAYER-2', reason: null })),
    }));
    const engine = new SyncEngine({ client: mock.client });
    await engine.flush(); // player applied, mapping persisted

    // A payment captured later still referencing the local player id.
    await enqueueAction({ client_id: 'PAY', entity: 'payment', created_at: '2024-01-01T09:05:00.000Z', payload: { player_id: 'PLAYER', amount_cents: 5000 } });
    await engine.flush();

    const payBatch = mock.batches.at(-1)!;
    expect((payBatch[0].payload as { player_id: string }).player_id).toBe('SERVER-PLAYER-2');
    expect((await getAction('PAY'))!.status).toBe('applied');
  });
});

describe('Sync_Engine — background sync + fallback triggers (task 8.10)', () => {
  it('reports Background Sync as unsupported under jsdom and never throws when registering', async () => {
    expect(isBackgroundSyncSupported()).toBe(false);
    await expect(registerBackgroundSync()).resolves.toBe(false);
    expect(BACKGROUND_SYNC_TAG).toBe('funhouse-sync');
  });

  it('flushes on the window online event and stops cleanly', async () => {
    const flush = vi.fn().mockResolvedValue({ outcome: 'ok' });
    const scheduler = new SyncScheduler({ flush, countUnsynced: async () => 0, intervalMs: 10_000 });
    scheduler.start();
    window.dispatchEvent(new Event('online'));
    expect(flush).toHaveBeenCalledTimes(1);
    scheduler.stop();
    window.dispatchEvent(new Event('online'));
    expect(flush).toHaveBeenCalledTimes(1); // no further calls after stop
  });

  it('onEnqueue triggers a flush while online', async () => {
    const flush = vi.fn().mockResolvedValue({ outcome: 'ok' });
    const scheduler = new SyncScheduler({ flush, countUnsynced: async () => 1 });
    await scheduler.onEnqueue();
    expect(flush).toHaveBeenCalledTimes(1);
  });

  it('the fallback interval flushes only while unsynced items remain', async () => {
    vi.useFakeTimers();
    try {
      const flush = vi.fn().mockResolvedValue({ outcome: 'ok' });
      let pending = 2;
      const scheduler = new SyncScheduler({
        flush,
        countUnsynced: async () => pending,
        intervalMs: 1000,
      });
      scheduler.start();
      await vi.advanceTimersByTimeAsync(1000);
      expect(flush).toHaveBeenCalledTimes(1);
      pending = 0;
      await vi.advanceTimersByTimeAsync(1000);
      expect(flush).toHaveBeenCalledTimes(1); // no flush when nothing pending
      scheduler.stop();
    } finally {
      vi.useRealTimers();
    }
  });
});

afterEach(async () => {
  await closeDb();
});
