import { beforeEach, describe, it, expect, vi } from 'vitest';
import {
  DB_NAME,
  activateOwnerDataKey,
  closeDb,
  countActionsByStatus,
  countQuarantinedLegacyActions,
  countQuarantinedLegacyData,
  countQuarantinedLegacyRecords,
  countUnsynced,
  enqueueAction as persistAction,
  getAction,
  getActionsByStatus,
  getAllLocalRecords,
  getActionsByPlayer,
  getBalances,
  getCachedRead,
  getDb,
  getLastSuccessfulSync,
  getOrCreateDeviceId,
  getOwnerMetadataSnapshot,
  getUnsyncedActions,
  migrateScopedLegacyData,
  putAction,
  setOwnerMetadata,
  updateActionStatus,
  writeBalances,
  writeCachedRead,
  writeLocalRecord,
} from './localStore';
import type { BalanceOut, LocalDataOwner, SyncAction } from '../domain/types';
import { encryptPayload, generateDataKey } from '../domain/crypto';
import { activateTestOwner } from '../setupTests';

async function resetDb(): Promise<void> {
  await closeDb();
  await new Promise<void>((resolve, reject) => {
    const req = indexedDB.deleteDatabase(DB_NAME);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
    req.onblocked = () => resolve();
  });
}

async function enqueue(value: SyncAction): Promise<void> {
  await persistAction(value, { owner: testOwner });
}

function action(clientId: string, createdAt: string, playerId?: string): SyncAction {
  return {
    client_id: clientId,
    entity: 'session',
    created_at: createdAt,
    payload: playerId ? { player_id: playerId } : {},
  };
}

let testOwner: LocalDataOwner;

beforeEach(async () => {
  await resetDb();
  const activated = await activateOwnerDataKey('test-subject');
  testOwner = {
    subject: 'test-subject',
    scope: 'test-owner',
    keyId: activated.keyId,
    key: activated.key,
    generation: 1,
    isCurrent: () => true,
  };
});

describe('Local_Store indexes and helpers', () => {
  it('by_status: countUnsynced reflects only unsynced actions (Req 6.1)', async () => {
    await enqueue(action('a', '2024-01-01T00:00:00.000Z'));
    await enqueue(action('b', '2024-01-02T00:00:00.000Z'));
    await enqueue(action('c', '2024-01-03T00:00:00.000Z'));
    expect(await countUnsynced(testOwner)).toBe(3);

    await updateActionStatus((await getAction('b', testOwner))!, 'applied', testOwner);
    await updateActionStatus((await getAction('c', testOwner))!, 'rejected', testOwner, 'duplicate');
    expect(await countUnsynced(testOwner)).toBe(1);
  });

  it('by_player: lookup returns only that player\'s actions (Req 8.2)', async () => {
    await enqueue(action('a', '2024-01-01T00:00:00.000Z', 'p1'));
    await enqueue(action('b', '2024-01-02T00:00:00.000Z', 'p1'));
    await enqueue(action('c', '2024-01-03T00:00:00.000Z', 'p2'));
    await enqueue(action('d', '2024-01-04T00:00:00.000Z')); // no player

    const p1 = await getActionsByPlayer('p1', testOwner);
    expect(p1.map((a) => a.client_id).sort()).toEqual(['a', 'b']);

    const p2 = await getActionsByPlayer('p2', testOwner);
    expect(p2.map((a) => a.client_id)).toEqual(['c']);
  });

  it('by_created_at: getUnsyncedActions returns actions ordered by created_at (Req 5.1)', async () => {
    // Insert out of order.
    await enqueue(action('c', '2024-03-03T00:00:00.000Z'));
    await enqueue(action('a', '2024-01-01T00:00:00.000Z'));
    await enqueue(action('b', '2024-02-02T00:00:00.000Z'));

    const unsynced = await getUnsyncedActions(testOwner);
    expect(unsynced.map((a) => a.client_id)).toEqual(['a', 'b', 'c']);
  });

  it('cached_reads: writing the same key overwrites the previous value', async () => {
    await writeCachedRead('players', [{ id: '1' }], testOwner, '2024-01-01T00:00:00.000Z');
    await writeCachedRead('players', [{ id: '1' }, { id: '2' }], testOwner, '2024-01-02T00:00:00.000Z');

    const cached = await getCachedRead<Array<{ id: string }>>('players', testOwner);
    expect(cached?.data).toEqual([{ id: '1' }, { id: '2' }]);
    expect(cached?.cached_at).toBe('2024-01-02T00:00:00.000Z');
  });

  it('cached_reads: physically isolates the same logical key by exact owner scope', async () => {
    const shared = await activateOwnerDataKey('shared-subject');
    const ownerA: LocalDataOwner = {
      subject: 'shared-subject',
      scope: 'v2:opaque-scope-a',
      keyId: shared.keyId,
      key: shared.key,
      generation: 1,
      isCurrent: () => true,
    };
    const ownerB: LocalDataOwner = {
      ...ownerA,
      scope: 'v2:opaque-scope-b',
      generation: 2,
      isCurrent: () => true,
    };

    await writeCachedRead('player:private-id', { value: 'A' }, ownerA);
    await writeCachedRead('player:private-id', { value: 'B' }, ownerB);

    expect((await getCachedRead<{ value: string }>('player:private-id', ownerA))?.data.value).toBe('A');
    expect((await getCachedRead<{ value: string }>('player:private-id', ownerB))?.data.value).toBe('B');

    const rows = await (await getDb()).getAll('cached_reads');
    expect(rows).toHaveLength(2);
    expect(new Set(rows.map((row) => row.key)).size).toBe(2);
    expect(JSON.stringify(rows)).not.toContain('player:private-id');
  });

  it('owner data keys are stable per subject while scope remains mandatory for access', async () => {
    const first = await activateOwnerDataKey('stable-subject');
    const second = await activateOwnerDataKey('stable-subject');
    expect(second.keyId).toBe(first.keyId);

    const oldScope: LocalDataOwner = {
      subject: 'stable-subject',
      scope: 'v2:opaque-old-scope',
      keyId: first.keyId,
      key: first.key,
      generation: 1,
      isCurrent: () => true,
    };
    const newScope: LocalDataOwner = {
      ...oldScope,
      scope: 'v2:opaque-new-scope',
      generation: 2,
      isCurrent: () => true,
    };
    await writeCachedRead('players', [{ id: 'old-scope-only' }], oldScope);

    await expect(getCachedRead('players', newScope)).resolves.toBeUndefined();
    expect((await getCachedRead<Array<{ id: string }>>('players', oldScope))?.data).toEqual([
      { id: 'old-scope-only' },
    ]);
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

    await writeBalances('p1', first, testOwner);
    await writeBalances('p1', second, testOwner);

    const cached = await getBalances('p1', testOwner);
    expect(cached?.balances).toEqual(second);
  });

  it('persists queue bodies, cached datasets, and balances only as owner-bound ciphertext', async () => {
    await enqueue({
      client_id: 'secret-action',
      entity: 'payment',
      created_at: '2024-01-01T00:00:00.000Z',
      payload: { player_id: 'private-player', amount_cents: 1234 },
    });
    await writeCachedRead('private-cache', [{ first_name: 'Private' }], testOwner);
    await writeBalances('private-player', [{
      entitlement_id: 'private-entitlement',
      product_id: 'private-product',
      remaining_units: 60,
      valid_from: null,
      valid_to: null,
      status: 'active',
    }], testOwner);

    const db = await getDb();
    const persisted = JSON.stringify({
      queue: await db.get('sync_queue', 'secret-action'),
      cache: await db.getAll('cached_reads'),
      balances: await db.getAll('entitlement_balances'),
    });
    expect(persisted).not.toContain('private-player');
    expect(persisted).not.toContain('Private');
    expect(persisted).not.toContain('private-entitlement');

    const other = await activateOwnerDataKey('other-subject');
    const wrongOwner: LocalDataOwner = {
      subject: 'other-subject',
      scope: 'other-owner',
      keyId: other.keyId,
      key: other.key,
      generation: 2,
      isCurrent: () => true,
    };
    await expect(getCachedRead('private-cache', wrongOwner)).resolves.toBeUndefined();
  });

  it('meta: device_id is generated once and stable across calls (Req 4.3)', async () => {
    const first = await getOrCreateDeviceId();
    const second = await getOrCreateDeviceId();
    expect(first).toBe(second);
    expect(first).toMatch(/[0-9a-f-]{36}/i);
  });
});



describe('Local_Store lifecycle, integrity, and migration guards', () => {
  it('creates the complete v2 schema safely on a fresh 0 to 2 open', async () => {
    await resetDb();
    const db = await getDb();
    expect(db.version).toBe(2);
    expect(Array.from(db.objectStoreNames)).toEqual(expect.arrayContaining([
      'sync_queue', 'players', 'sessions', 'payments', 'entitlements',
      'consents', 'attendance', 'student_metrics', 'cached_reads',
      'entitlement_balances', 'owner_data_keys', 'legacy_quarantine', 'meta',
    ]));
    const keyring = db.transaction('owner_data_keys').store;
    expect(keyring.keyPath).toBe('owner_key_id');
    const quarantine = db.transaction('legacy_quarantine').store;
    expect(quarantine.keyPath).toBe('quarantine_id');
    expect(Array.from(quarantine.indexNames)).toContain('by_source_store');
  });

  it('rescans source revisions and never resolves open with plaintext legacy rows', async () => {
    await resetDb();
    await closeDb();
    await new Promise<void>((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, 1);
      request.onupgradeneeded = () => {
        const db = request.result;
        const queue = db.createObjectStore('sync_queue', { keyPath: 'client_id' });
        queue.createIndex('by_status', 'status');
        queue.createIndex('by_entity', 'entity');
        queue.createIndex('by_created_at', 'created_at');
        queue.createIndex('by_player', 'player_id');
        db.createObjectStore('players', { keyPath: 'local_id' });
        db.createObjectStore('sessions', { keyPath: 'local_id' });
        db.createObjectStore('payments', { keyPath: 'local_id' });
        db.createObjectStore('entitlements', { keyPath: 'local_id' });
        db.createObjectStore('consents', { keyPath: 'local_id' });
        db.createObjectStore('attendance', { keyPath: 'local_id' });
        db.createObjectStore('student_metrics', { keyPath: 'local_id' });
        db.createObjectStore('cached_reads', { keyPath: 'key' });
        db.createObjectStore('entitlement_balances', { keyPath: 'player_id' });
        db.createObjectStore('meta', { keyPath: 'key' });
        request.transaction!.objectStore('sync_queue').put({
          client_id: 'revision-race', entity: 'session', created_at: '2024-01-01T00:00:00.000Z',
          payload: { player_id: 'before-change' }, status: 'unsynced', attempt_count: 0,
          sync_scope: 'unknown-race-scope',
        });
        request.transaction!.objectStore('sessions').put({
          local_id: 'local-race', sync_scope: 'unknown-race-scope', player_id: 'private-player',
        });
        request.transaction!.objectStore('meta').put({
          key: 'player_id_resolutions:v1:race-subject:manager:race-location:no-school',
          value: { 'before-player': 'before-server-id' },
        });
      };
      request.onsuccess = () => { request.result.close(); resolve(); };
      request.onerror = () => reject(request.error);
    });

    const actualEncrypt = globalThis.crypto.subtle.encrypt.bind(globalThis.crypto.subtle);
    let changed = false;
    vi.spyOn(globalThis.crypto.subtle, 'encrypt').mockImplementation(async (...args) => {
      const result = await actualEncrypt(...args);
      if (!changed) {
        changed = true;
        const connection = await new Promise<IDBDatabase>((resolve, reject) => {
          const open = indexedDB.open(DB_NAME, 2);
          open.onsuccess = () => resolve(open.result);
          open.onerror = () => reject(open.error);
        });
        await new Promise<void>((resolve, reject) => {
          const tx = connection.transaction(['sync_queue', 'meta'], 'readwrite');
          tx.objectStore('sync_queue').put({
            client_id: 'revision-race', entity: 'session', created_at: '2024-01-01T00:00:00.000Z',
            payload: { player_id: 'after-change' }, status: 'unsynced', attempt_count: 1,
            sync_scope: 'unknown-race-scope',
          });
          tx.objectStore('meta').put({
            key: 'player_id_resolutions:v1:race-subject:manager:race-location:no-school',
            value: { 'after-player': 'after-server-id' },
          });
          tx.oncomplete = () => resolve();
          tx.onerror = () => reject(tx.error);
        });
        connection.close();
      }
      return result;
    });

    const db = await getDb();
    vi.restoreAllMocks();
    expect(await db.get('sync_queue', 'revision-race')).toBeUndefined();
    expect(await db.get('sessions', 'local-race')).toBeUndefined();
    expect(await db.get('meta', 'player_id_resolutions:v1:race-subject:manager:race-location:no-school')).toBeUndefined();
    expect(await countQuarantinedLegacyActions()).toBe(1);
    expect(await countQuarantinedLegacyRecords()).toBe(1);
    expect(await countQuarantinedLegacyData()).toBe(3);
    const rawQuarantine = JSON.stringify(await db.getAll('legacy_quarantine'));
    expect(rawQuarantine).not.toContain('race-subject');
    expect(rawQuarantine).not.toContain('after-player');
    expect(rawQuarantine).not.toContain('after-server-id');
  });

  it('rejects a same-scope stale capability and aborts a cache write after its IDB request', async () => {
    await resetDb();
    const activated = await activateOwnerDataKey('race-subject');
    let checks = 0;
    const staleDuringPut: LocalDataOwner = {
      subject: 'race-subject',
      scope: 'v2:opaque-race-scope',
      keyId: activated.keyId,
      key: activated.key,
      generation: 1,
      // writeCachedRead reaches check 9 after the put request has resolved but
      // while the explicit readwrite transaction can still be aborted.
      isCurrent: () => ++checks < 9,
    };

    await expect(writeCachedRead('players', [{ id: 'must-not-land' }], staleDuringPut)).rejects.toThrow('stale');
    expect(await (await getDb()).count('cached_reads')).toBe(0);

    const replacement: LocalDataOwner = {
      ...staleDuringPut,
      generation: 2,
      isCurrent: () => true,
    };
    await writeCachedRead('players', [{ id: 'replacement' }], replacement);
    await expect(getCachedRead('players', staleDuringPut)).rejects.toThrow('stale');
    expect((await getCachedRead<Array<{ id: string }>>('players', replacement))?.data).toEqual([{ id: 'replacement' }]);
  });

  it('rejects every stale same-subject write family before durable mutation', async () => {
    await resetDb();
    const activated = await activateOwnerDataKey('stale-family-subject');
    const stale: LocalDataOwner = {
      subject: 'stale-family-subject', scope: 'v2:opaque-stale-family-scope',
      keyId: activated.keyId, key: activated.key, generation: 1, isCurrent: () => false,
    };

    await expect(writeBalances('p1', [], stale)).rejects.toThrow('stale');
    await expect(persistAction(action('stale-queue', '2024-01-01T00:00:00.000Z'), { owner: stale })).rejects.toThrow('stale');
    await expect(setOwnerMetadata('player_id_resolutions:' + stale.scope, { local: 'server' }, stale, 0)).rejects.toThrow('stale');
    await expect(writeLocalRecord('sessions', { local_id: 'stale-local', sync_scope: stale.scope, player_id: 'p1' }, stale)).rejects.toThrow('stale');

    const db = await getDb();
    expect(await db.count('entitlement_balances')).toBe(0);
    expect(await db.count('sync_queue')).toBe(0);
    expect(await db.get('meta', 'player_id_resolutions:' + stale.scope)).toBeUndefined();
    expect(await db.count('sessions')).toBe(0);
  });

  it('authenticates queue mirrors so clear terminal-status corruption stays pending and untransmittable', async () => {
    await resetDb();
    const owner = await activateTestOwner('integrity-subject');
    await persistAction(action('corrupt-status', '2024-01-01T00:00:00.000Z'), { owner });
    const db = await getDb();
    const row = await db.get('sync_queue', 'corrupt-status');
    expect(row).toBeDefined();
    await db.put('sync_queue', { ...row!, status: 'applied' });

    expect(await getUnsyncedActions(owner)).toEqual([]);
    expect(await getActionsByStatus('applied', owner)).toEqual([]);
    expect(await countUnsynced(owner)).toBe(1);
    expect(await countActionsByStatus(owner, 'applied')).toBe(0);
  });

  it('uses guarded storage revisions so an older D2/status view cannot overwrite a newer row', async () => {
    await resetDb();
    const owner = await activateTestOwner('revision-subject');
    await persistAction(action('revisioned', '2024-01-01T00:00:00.000Z', 'local-player'), { owner });
    const first = (await getAction('revisioned', owner))!;
    const stale = (await getAction('revisioned', owner))!;

    await putAction({ ...first, payload: { player_id: 'server-player' }, player_id: 'server-player' }, owner);
    expect(await updateActionStatus(stale, 'applied', owner)).toBe(false);
    await expect(putAction({ ...stale, status: 'applied' }, owner)).rejects.toThrow('changed before guarded update');

    const stored = (await getAction('revisioned', owner))!;
    expect(stored.payload).toEqual({ player_id: 'server-player' });
    expect(stored.status).toBe('unsynced');
    expect(stored.storage_revision).toBe(2);
  });

  it('guards D2 metadata against stale map overwrites using the revision originally read', async () => {
    await resetDb();
    const owner = await activateTestOwner('metadata-revision-subject');
    const key = `player_id_resolutions:${owner.scope}`;
    await setOwnerMetadata(key, { first: 'server-1' }, owner, 0);
    const firstReader = (await getOwnerMetadataSnapshot<Record<string, string>>(key, owner))!;
    const staleReader = (await getOwnerMetadataSnapshot<Record<string, string>>(key, owner))!;

    await setOwnerMetadata(key, { ...firstReader.value, second: 'server-2' }, owner, firstReader.storage_revision);
    await expect(setOwnerMetadata(key, { ...staleReader.value, third: 'server-3' }, owner, staleReader.storage_revision))
      .rejects.toThrow('changed before guarded update');
    expect((await getOwnerMetadataSnapshot<Record<string, string>>(key, owner))?.value).toEqual({
      first: 'server-1', second: 'server-2',
    });
  });

  it('stores complete local activity bodies as ciphertext while authorised getters retain logical behavior', async () => {
    await resetDb();
    const owner = await activateTestOwner('local-record-subject');
    await writeLocalRecord('payments', {
      local_id: 'payment-local',
      client_id: 'payment-client',
      sync_scope: owner.scope,
      player_id: 'private-player',
      day: '2024-06-15',
      amount_cents: 35000,
      method: 'cash',
      product_id: 'private-product',
    }, owner);

    const raw = await (await getDb()).get('payments', 'payment-local');
    expect(Object.keys(raw!).sort()).toEqual(['client_id', 'envelope', 'local_id', 'sync_scope']);
    const serialized = JSON.stringify(raw);
    expect(serialized).not.toContain('private-player');
    expect(serialized).not.toContain('35000');
    expect(serialized).not.toContain('private-product');

    expect(await getAllLocalRecords('payments', owner)).toEqual([
      expect.objectContaining({
        local_id: 'payment-local',
        player_id: 'private-player',
        amount_cents: 35000,
        product_id: 'private-product',
      }),
    ]);
  });

  it('upgrades v1 without ConstraintError, migrates exact-scope queue/local rows, and quarantines unknown scope', async () => {
    await resetDb();
    const scope = 'v1:legacy-subject:manager:legacy-location:no-school';
    const legacyKey = await generateDataKey();
    const personal = await encryptPayload(legacyKey, { note: 'legacy-private-fragment' });

    await closeDb();
    await new Promise<void>((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, 1);
      request.onupgradeneeded = () => {
        const db = request.result;
        const queue = db.createObjectStore('sync_queue', { keyPath: 'client_id' });
        queue.createIndex('by_status', 'status');
        queue.createIndex('by_entity', 'entity');
        queue.createIndex('by_created_at', 'created_at');
        queue.createIndex('by_player', 'player_id');
        db.createObjectStore('players', { keyPath: 'local_id' });
        db.createObjectStore('sessions', { keyPath: 'local_id' });
        db.createObjectStore('payments', { keyPath: 'local_id' });
        db.createObjectStore('entitlements', { keyPath: 'local_id' });
        db.createObjectStore('consents', { keyPath: 'local_id' });
        db.createObjectStore('attendance', { keyPath: 'local_id' });
        db.createObjectStore('student_metrics', { keyPath: 'local_id' });
        db.createObjectStore('cached_reads', { keyPath: 'key' });
        db.createObjectStore('entitlement_balances', { keyPath: 'player_id' });
        db.createObjectStore('meta', { keyPath: 'key' });
        request.transaction!.objectStore('sync_queue').put({
          client_id: 'legacy-exact', entity: 'session', created_at: '2024-01-01T00:00:00.000Z',
          payload: { player_id: 'legacy-player', duration_minutes: 60 }, status: 'unsynced', attempt_count: 2, sync_scope: scope,
        });
        request.transaction!.objectStore('sync_queue').put({
          client_id: 'legacy-unknown', entity: 'payment', created_at: '2024-01-02T00:00:00.000Z',
          payload: { amount_cents: 1234 }, status: 'unsynced', attempt_count: 0, sync_scope: 'unknown-scope',
        });
        request.transaction!.objectStore('sessions').put({
          local_id: 'legacy-session', sync_scope: scope, player_id: 'legacy-player', day: '2024-01-01',
          session_type: 'lounge', duration_minutes: 60, enc: personal,
        });
        request.transaction!.objectStore('payments').put({
          local_id: 'legacy-payment', sync_scope: scope, player_id: 'legacy-player', day: '2024-01-01',
          amount_cents: 2500, method: 'cash',
        });
        request.transaction!.objectStore('attendance').put({
          local_id: 'legacy-unknown-attendance', sync_scope: 'unknown-scope',
          player_id: 'unknown-player', session_id: 'unknown-session', present: true,
        });
        request.transaction!.objectStore('meta').put({
          key: `player_id_resolutions:${scope}`,
          value: { 'legacy-player': 'server-player' },
        });
        request.transaction!.objectStore('meta').put({
          key: `session_id_resolutions:${scope}`,
          value: { 'legacy-session': 'server-session' },
        });
        request.transaction!.objectStore('meta').put({
          key: `last_successful_sync:${scope}`,
          value: '2024-01-03T00:00:00.000Z',
        });
      };
      request.onsuccess = () => { request.result.close(); resolve(); };
      request.onerror = () => reject(request.error);
    });

    // Opening the runtime schema exercises the real 1 -> 2 upgrade and moves
    // every legacy queue/local source into encrypted device quarantine.
    await expect(getDb()).resolves.toBeDefined();
    const dbAfterOpen = await getDb();
    expect(await dbAfterOpen.get('sync_queue', 'legacy-exact')).toBeUndefined();
    expect(await dbAfterOpen.get('sessions', 'legacy-session')).toBeUndefined();
    expect(await dbAfterOpen.get('attendance', 'legacy-unknown-attendance')).toBeUndefined();
    expect(await dbAfterOpen.get('meta', `player_id_resolutions:${scope}`)).toBeUndefined();
    expect(await dbAfterOpen.get('meta', `session_id_resolutions:${scope}`)).toBeUndefined();
    expect(await dbAfterOpen.get('meta', `last_successful_sync:${scope}`)).toBeUndefined();
    expect(await countQuarantinedLegacyActions()).toBe(2);
    expect(await countQuarantinedLegacyRecords()).toBe(3);
    expect(await countQuarantinedLegacyData()).toBe(8);
    const quarantineKey = (await dbAfterOpen.get('meta', 'legacy_quarantine_device_key_v2'))?.value as CryptoKey;
    expect(quarantineKey.extractable).toBe(false);
    const rawQuarantine = JSON.stringify(await dbAfterOpen.getAll('legacy_quarantine'));
    expect(rawQuarantine).not.toContain('legacy-player');
    expect(rawQuarantine).not.toContain('server-player');
    expect(rawQuarantine).not.toContain('server-session');
    expect(rawQuarantine).not.toContain('2024-01-03T00:00:00.000Z');
    expect(rawQuarantine).not.toContain('legacy-session');
    expect(rawQuarantine).not.toContain('legacy-exact');
    expect(rawQuarantine).not.toContain('legacy-unknown-attendance');
    expect(rawQuarantine).not.toContain('unknown-player');
    expect(rawQuarantine).not.toContain('2500');
    expect(rawQuarantine).not.toContain(scope);

    const activated = await activateOwnerDataKey('legacy-subject');
    const owner: LocalDataOwner = {
      subject: 'legacy-subject', scope: 'v2:opaque-legacy-owner', keyId: activated.keyId, key: activated.key,
      generation: 1, isCurrent: () => true,
    };
    const wrongLegacyKey = await generateDataKey();
    await migrateScopedLegacyData(owner, wrongLegacyKey, { legacyScope: scope });

    // Queue and non-personal local sources migrate, but a wrong personal key
    // leaves that exact source encrypted and resumable in quarantine.
    expect((await getAction('legacy-exact', owner))!).toMatchObject({
      client_id: 'legacy-exact', attempt_count: 2, created_at: '2024-01-01T00:00:00.000Z',
    });
    expect((await getAllLocalRecords('payments', owner))[0]).toMatchObject({
      player_id: 'legacy-player', amount_cents: 2500, method: 'cash',
    });
    expect(await countQuarantinedLegacyActions()).toBe(1);
    expect(await countQuarantinedLegacyRecords()).toBe(2);
    expect(await countQuarantinedLegacyData()).toBe(3);
    expect(JSON.stringify(await dbAfterOpen.getAll('legacy_quarantine'))).not.toContain('legacy-private-fragment');

    // Supplying the correct restored key completes the exact-scope personal row.
    await migrateScopedLegacyData(owner, legacyKey, { legacyScope: scope });
    const local = (await getAllLocalRecords('sessions', owner))[0];
    expect(local).toMatchObject({ player_id: 'legacy-player', day: '2024-01-01', duration_minutes: 60 });
    const rawLocal = await dbAfterOpen.get('sessions', 'legacy-session');
    expect(rawLocal?.sync_scope).toBe(owner.scope);
    expect(JSON.stringify(rawLocal)).not.toContain('legacy-player');
    expect((await getOwnerMetadataSnapshot<Record<string, string>>(
      `player_id_resolutions:${owner.scope}`,
      owner,
    ))?.value).toEqual({ 'legacy-player': 'server-player' });
    expect((await getOwnerMetadataSnapshot<Record<string, string>>(
      `session_id_resolutions:${owner.scope}`,
      owner,
    ))?.value).toEqual({ 'legacy-session': 'server-session' });
    expect(await dbAfterOpen.get('meta', `player_id_resolutions:${scope}`)).toBeUndefined();
    expect(await dbAfterOpen.get('meta', `session_id_resolutions:${scope}`)).toBeUndefined();
    expect(await dbAfterOpen.get('meta', `last_successful_sync:${scope}`)).toBeUndefined();
    expect(await getLastSuccessfulSync(owner)).toBeNull();
    expect(await countQuarantinedLegacyData()).toBe(2);

    // A later exact owner can prove attribution for formerly unknown queue and
    // local rows; only then are they decrypted and moved out of quarantine.
    const unknownKey = await activateOwnerDataKey('later-exact-subject');
    const unknownOwner: LocalDataOwner = {
      subject: 'later-exact-subject', scope: 'v2:later-exact-owner',
      keyId: unknownKey.keyId, key: unknownKey.key, generation: 2, isCurrent: () => true,
    };
    await migrateScopedLegacyData(unknownOwner, wrongLegacyKey, { legacyScope: 'unknown-scope' });
    expect(await getAction('legacy-unknown', unknownOwner)).toMatchObject({ client_id: 'legacy-unknown' });
    expect((await getAllLocalRecords('attendance', unknownOwner))[0]).toMatchObject({
      player_id: 'unknown-player', session_id: 'unknown-session', present: true,
    });
    expect(await countQuarantinedLegacyData()).toBe(0);
  });
});
