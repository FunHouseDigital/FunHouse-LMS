import { beforeEach, describe, it, expect } from 'vitest';
import {
  DB_NAME,
  closeDb,
  countUnsynced,
  enqueueAction,
  getActionsByPlayer,
  getBalances,
  getCachedRead,
  getOrCreateDeviceId,
  getUnsyncedActions,
  updateActionStatus,
  writeBalances,
  writeCachedRead,
} from './localStore';
import type { BalanceOut, SyncAction } from '../domain/types';

async function resetDb(): Promise<void> {
  await closeDb();
  await new Promise<void>((resolve, reject) => {
    const req = indexedDB.deleteDatabase(DB_NAME);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
    req.onblocked = () => resolve();
  });
}

function action(clientId: string, createdAt: string, playerId?: string): SyncAction {
  return {
    client_id: clientId,
    entity: 'session',
    created_at: createdAt,
    payload: playerId ? { player_id: playerId } : {},
  };
}

beforeEach(async () => {
  await resetDb();
});

describe('Local_Store indexes and helpers', () => {
  it('by_status: countUnsynced reflects only unsynced actions (Req 6.1)', async () => {
    await enqueueAction(action('a', '2024-01-01T00:00:00.000Z'));
    await enqueueAction(action('b', '2024-01-02T00:00:00.000Z'));
    await enqueueAction(action('c', '2024-01-03T00:00:00.000Z'));
    expect(await countUnsynced()).toBe(3);

    await updateActionStatus('b', 'applied');
    await updateActionStatus('c', 'rejected', 'duplicate');
    expect(await countUnsynced()).toBe(1);
  });

  it('by_player: lookup returns only that player\'s actions (Req 8.2)', async () => {
    await enqueueAction(action('a', '2024-01-01T00:00:00.000Z', 'p1'));
    await enqueueAction(action('b', '2024-01-02T00:00:00.000Z', 'p1'));
    await enqueueAction(action('c', '2024-01-03T00:00:00.000Z', 'p2'));
    await enqueueAction(action('d', '2024-01-04T00:00:00.000Z')); // no player

    const p1 = await getActionsByPlayer('p1');
    expect(p1.map((a) => a.client_id).sort()).toEqual(['a', 'b']);

    const p2 = await getActionsByPlayer('p2');
    expect(p2.map((a) => a.client_id)).toEqual(['c']);
  });

  it('by_created_at: getUnsyncedActions returns actions ordered by created_at (Req 5.1)', async () => {
    // Insert out of order.
    await enqueueAction(action('c', '2024-03-03T00:00:00.000Z'));
    await enqueueAction(action('a', '2024-01-01T00:00:00.000Z'));
    await enqueueAction(action('b', '2024-02-02T00:00:00.000Z'));

    const unsynced = await getUnsyncedActions();
    expect(unsynced.map((a) => a.client_id)).toEqual(['a', 'b', 'c']);
  });

  it('cached_reads: writing the same key overwrites the previous value', async () => {
    await writeCachedRead('players', [{ id: '1' }], '2024-01-01T00:00:00.000Z');
    await writeCachedRead('players', [{ id: '1' }, { id: '2' }], '2024-01-02T00:00:00.000Z');

    const cached = await getCachedRead<Array<{ id: string }>>('players');
    expect(cached?.data).toEqual([{ id: '1' }, { id: '2' }]);
    expect(cached?.cached_at).toBe('2024-01-02T00:00:00.000Z');
  });

  it('entitlement_balances: writing replaces the cached balances for a player (Req 8.5)', async () => {
    const first: BalanceOut[] = [
      {
        entitlement_id: 'e1',
        product_id: 'prod',
        remaining_units: 120,
        valid_from: null,
        valid_to: null,
        status: 'active',
      },
    ];
    const second: BalanceOut[] = [{ ...first[0], remaining_units: 60 }];

    await writeBalances('p1', first);
    await writeBalances('p1', second);

    const cached = await getBalances('p1');
    expect(cached?.balances).toEqual(second);
  });

  it('meta: device_id is generated once and stable across calls (Req 4.3)', async () => {
    const first = await getOrCreateDeviceId();
    const second = await getOrCreateDeviceId();
    expect(first).toBe(second);
    expect(first).toMatch(/[0-9a-f-]{36}/i);
  });
});
