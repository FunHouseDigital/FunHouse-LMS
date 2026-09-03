import { openDB, type IDBPDatabase, type DBSchema } from 'idb';
import type {
  BalanceOut,
  EncryptedEnvelope,
  EncryptedField,
  EntityType,
  LocalDataOwner,
  StoredSyncAction,
  SyncAction,
  SyncStatus,
} from '../domain/types';
import {
  decryptEnvelope,
  decryptPayload,
  encryptEnvelope,
  generateDataKey,
  sha256Base64Url,
} from '../domain/crypto';

export const DB_NAME = 'funhouse-revenue';
export const DB_VERSION = 2;

export type LocalRecordStore =
  | 'players'
  | 'sessions'
  | 'payments'
  | 'entitlements'
  | 'consents'
  | 'attendance'
  | 'student_metrics';

/** Authorised logical local-record view. Raw IndexedDB rows use PersistedLocalRecord. */
export interface LocalRecord {
  local_id: string;
  client_id?: string;
  sync_scope?: string;
  enc?: EncryptedEnvelope | EncryptedField;
  [key: string]: unknown;
}

export interface CachedRead<T = unknown> { key: string; data: T; cached_at: string }
export interface CachedBalances { player_id: string; balances: BalanceOut[]; cached_at: string }
export interface MetaEntry { key: string; value: unknown }

interface OwnerKeyRow {
  owner_key_id: string;
  key_id: string;
  key: CryptoKey;
  created_at: string;
}

const QUARANTINE_KEY_META_KEY = 'legacy_quarantine_device_key_v2';
const QUARANTINE_KEY_ID = 'device-quarantine-v2';
const LOCAL_DATA_MUTATION_LOCK_NAME = 'funhouse-local-data-mutation';

type LegacySourceStore = 'sync_queue' | LocalRecordStore | 'meta';

interface LegacyQuarantineBody {
  version: 1;
  source_store: LegacySourceStore;
  source_key: string;
  source: QueueRow | LocalRow | MetaEntry;
}

interface LegacyQuarantineRow {
  quarantine_id: string;
  source_store: LegacySourceStore;
  envelope: EncryptedEnvelope;
}

interface PersistedQueueBody {
  client_id: string;
  owner_scope: string;
  entity: EntityType;
  created_at: string;
  status: SyncStatus;
  attempt_count: number;
  storage_revision: number;
  payload: Record<string, unknown>;
  player_id?: string;
  reason?: string;
}

/** Clear values are operational mirrors only; decryptQueueRow authenticates all of them. */
export interface PersistedSyncQueueRow {
  client_id: string;
  created_at: string;
  status: SyncStatus;
  attempt_count: number;
  owner_scope: string;
  envelope: EncryptedEnvelope;
}

interface LegacySyncQueueRow extends Partial<PersistedSyncQueueRow> {
  client_id: string;
  created_at: string;
  status: SyncStatus;
  attempt_count: number;
  sync_scope?: string;
  entity?: EntityType;
  payload?: Record<string, unknown>;
  player_id?: string;
  reason?: string;
}
type QueueRow = PersistedSyncQueueRow | LegacySyncQueueRow;

interface PersistedLocalBody {
  record: Record<string, unknown>;
  personal?: unknown;
}
export interface PersistedLocalRecord {
  local_id: string;
  client_id?: string;
  sync_scope: string;
  envelope: EncryptedEnvelope;
}
type LocalRow = PersistedLocalRecord | LocalRecord;

interface PersistedCachedRead {
  key: string;
  owner_scope: string;
  cached_at: string;
  envelope: EncryptedEnvelope;
}
interface PersistedBalances {
  player_id: string;
  owner_scope: string;
  cached_at: string;
  envelope: EncryptedEnvelope;
}
interface PersistedOwnerMetadata { owner_scope: string; envelope: EncryptedEnvelope }
interface PersistedLastSync { owner_scope: string; envelope: EncryptedEnvelope }

interface FunhouseDB extends DBSchema {
  sync_queue: {
    key: string;
    value: QueueRow;
    indexes: {
      by_status: string;
      by_entity: string;
      by_created_at: string;
      by_player: string;
      by_scope: string;
      by_scope_status: [string, string];
    };
  };
  players: { key: string; value: LocalRow; indexes: { by_client_id: string; by_name: string } };
  sessions: { key: string; value: LocalRow; indexes: { by_day: string; by_player: string } };
  payments: { key: string; value: LocalRow; indexes: { by_day: string; by_player: string } };
  entitlements: { key: string; value: LocalRow; indexes: { by_player: string; by_client_id: string } };
  consents: { key: string; value: LocalRow; indexes: { by_player: string } };
  attendance: { key: string; value: LocalRow; indexes: { by_session: string; by_player: string } };
  student_metrics: { key: string; value: LocalRow; indexes: { by_day: string } };
  cached_reads: { key: string; value: PersistedCachedRead };
  entitlement_balances: { key: string; value: PersistedBalances };
  owner_data_keys: { key: string; value: OwnerKeyRow };
  legacy_quarantine: {
    key: string;
    value: LegacyQuarantineRow;
    indexes: { by_source_store: string };
  };
  meta: { key: string; value: MetaEntry };
}

let sameContextLocalDataTail: Promise<void> = Promise.resolve();

/**
 * Serialize every sensitive local-data commit with auth replacement. Web Locks
 * provide same-origin coordination; the promise tail also orders callers in
 * runtimes/tests where Web Locks are absent or implemented per request.
 */
export function withLocalDataMutationLock<T>(operation: () => Promise<T>): Promise<T> {
  const run = async (): Promise<T> => {
    const locks = typeof globalThis.navigator === 'undefined'
      ? undefined
      : globalThis.navigator.locks;
    if (locks) {
      return locks.request(LOCAL_DATA_MUTATION_LOCK_NAME, () => operation()) as unknown as Promise<T>;
    }
    return operation();
  };
  const result = sameContextLocalDataTail.then(run, run);
  sameContextLocalDataTail = result.then(() => undefined, () => undefined);
  return result;
}

let dbPromise: Promise<IDBPDatabase<FunhouseDB>> | null = null;

function quarantineAad(sourceStore: string, quarantineId: string): string {
  return JSON.stringify({ version: 2, purpose: 'legacy-quarantine', source_store: sourceStore, quarantine_id: quarantineId });
}

async function getOrCreateQuarantineKey(db: IDBPDatabase<FunhouseDB>): Promise<CryptoKey> {
  const existing = await db.get('meta', QUARANTINE_KEY_META_KEY);
  if (existing?.value && typeof existing.value === 'object') return existing.value as CryptoKey;
  const candidate = await generateDataKey();
  const tx = db.transaction('meta', 'readwrite');
  const current = await tx.store.get(QUARANTINE_KEY_META_KEY);
  if (!current) await tx.store.put({ key: QUARANTINE_KEY_META_KEY, value: candidate });
  await tx.done;
  return (current?.value as CryptoKey | undefined) ?? candidate;
}

const LOCAL_RECORD_STORES: readonly LocalRecordStore[] = [
  'players', 'sessions', 'payments', 'entitlements',
  'consents', 'attendance', 'student_metrics',
];

function isLegacyScopedMetaKey(key: string): boolean {
  return /^(player_id_resolutions|session_id_resolutions|last_successful_sync):v1:/.test(key);
}

async function quarantineLegacyRows(db: IDBPDatabase<FunhouseDB>): Promise<void> {
  const stores: Array<'sync_queue' | LocalRecordStore> = ['sync_queue', ...LOCAL_RECORD_STORES];

  for (let pass = 0; pass < 16; pass += 1) {
    const pending: Array<{ store: LegacySourceStore; key: string; source: QueueRow | LocalRow | MetaEntry }> = [];
    for (const store of stores) {
      const rows = await db.getAll(store as LocalRecordStore & 'sync_queue') as Array<QueueRow | LocalRow>;
      for (const source of rows) {
        const sealed = store === 'sync_queue'
          ? isEncryptedQueueRow(source as QueueRow)
          : isSealedLocalRow(source as LocalRow);
        if (sealed) continue;
        const key = store === 'sync_queue'
          ? (source as LegacySyncQueueRow).client_id
          : (source as LocalRecord).local_id;
        if (typeof key === 'string' && key.length > 0) pending.push({ store, key, source });
        else throw new Error(`Cannot quarantine malformed legacy ${store} row without a primary key`);
      }
    }
    for (const source of await db.getAll('meta')) {
      if (isLegacyScopedMetaKey(source.key)) {
        pending.push({ store: 'meta', key: source.key, source });
      }
    }
    if (pending.length === 0) return;

    const key = await getOrCreateQuarantineKey(db);
    for (const item of pending) {
      const quarantineId = newClientId();
      const body: LegacyQuarantineBody = {
        version: 1,
        source_store: item.store,
        source_key: item.key,
        source: item.source,
      };
      const envelope = await encryptEnvelope(
        key,
        QUARANTINE_KEY_ID,
        body,
        quarantineAad(item.store, quarantineId),
      );
      const tx = db.transaction(
        [item.store, 'legacy_quarantine'] as Array<LegacySourceStore | 'legacy_quarantine'>,
        'readwrite',
      );
      const current = item.store === 'meta'
        ? await tx.objectStore('meta').get(item.key)
        : await tx.objectStore(item.store as LocalRecordStore & 'sync_queue').get(item.key) as QueueRow | LocalRow | undefined;
      if (!sourceMatches(current, item.source)) {
        tx.abort();
        try { await tx.done; } catch { /* rescan the changed source below */ }
        continue;
      }
      await tx.objectStore('legacy_quarantine').add({
        quarantine_id: quarantineId,
        source_store: item.store,
        envelope,
      });
      if (item.store === 'meta') await tx.objectStore('meta').delete(item.key);
      else await tx.objectStore(item.store as LocalRecordStore & 'sync_queue').delete(item.key);
      await tx.done;
    }
  }

  // Never expose a successfully opened database while any plaintext legacy row
  // is still changing too quickly to quarantine atomically.
  throw new Error('Legacy local data could not be quarantined to a stable snapshot');
}

export function getDb(): Promise<IDBPDatabase<FunhouseDB>> {
  if (!dbPromise) {
    dbPromise = (async () => {
      const db = await openDB<FunhouseDB>(DB_NAME, DB_VERSION, {
        upgrade(db, oldVersion, _newVersion, transaction) {
          if (oldVersion < 1) {
            const queue = db.createObjectStore('sync_queue', { keyPath: 'client_id' });
            queue.createIndex('by_status', 'status');
            queue.createIndex('by_entity', 'entity');
            queue.createIndex('by_created_at', 'created_at');
            queue.createIndex('by_player', 'player_id');
            const players = db.createObjectStore('players', { keyPath: 'local_id' });
            players.createIndex('by_client_id', 'client_id');
            players.createIndex('by_name', 'name');
            const sessions = db.createObjectStore('sessions', { keyPath: 'local_id' });
            sessions.createIndex('by_day', 'day');
            sessions.createIndex('by_player', 'player_id');
            const payments = db.createObjectStore('payments', { keyPath: 'local_id' });
            payments.createIndex('by_day', 'day');
            payments.createIndex('by_player', 'player_id');
            const entitlements = db.createObjectStore('entitlements', { keyPath: 'local_id' });
            entitlements.createIndex('by_player', 'player_id');
            entitlements.createIndex('by_client_id', 'client_id');
            const consents = db.createObjectStore('consents', { keyPath: 'local_id' });
            consents.createIndex('by_player', 'player_id');
            const attendance = db.createObjectStore('attendance', { keyPath: 'local_id' });
            attendance.createIndex('by_session', 'session_id');
            attendance.createIndex('by_player', 'player_id');
            const metrics = db.createObjectStore('student_metrics', { keyPath: 'local_id' });
            metrics.createIndex('by_day', 'day');
            db.createObjectStore('cached_reads', { keyPath: 'key' });
            db.createObjectStore('entitlement_balances', { keyPath: 'player_id' });
            db.createObjectStore('meta', { keyPath: 'key' });
          }
          if (oldVersion < 2) {
            if (!db.objectStoreNames.contains('owner_data_keys')) {
              db.createObjectStore('owner_data_keys', { keyPath: 'owner_key_id' });
            }
            if (!db.objectStoreNames.contains('legacy_quarantine')) {
              const quarantine = db.createObjectStore('legacy_quarantine', { keyPath: 'quarantine_id' });
              quarantine.createIndex('by_source_store', 'source_store');
            }
            const queue = transaction.objectStore('sync_queue');
            if (!queue.indexNames.contains('by_scope')) {
              queue.createIndex('by_scope', 'owner_scope');
            }
            if (!queue.indexNames.contains('by_scope_status')) {
              queue.createIndex('by_scope_status', ['owner_scope', 'status']);
            }
            // Approved policy: v1 caches/balances are disposable and refetchable.
            if (oldVersion >= 1) {
              if (db.objectStoreNames.contains('cached_reads')) db.deleteObjectStore('cached_reads');
              db.createObjectStore('cached_reads', { keyPath: 'key' });
              if (db.objectStoreNames.contains('entitlement_balances')) db.deleteObjectStore('entitlement_balances');
              db.createObjectStore('entitlement_balances', { keyPath: 'player_id' });
            }
          }
        },
        blocking() { dbPromise = null; },
      });
      await quarantineLegacyRows(db);
      return db;
    })();
  }
  return dbPromise;
}

export async function closeDb(): Promise<void> {
  if (!dbPromise) return;
  const db = await dbPromise;
  db.close();
  dbPromise = null;
}

export function newClientId(): string { return globalThis.crypto.randomUUID(); }

function extractPlayerId(payload: unknown): string | undefined {
  if (!payload || typeof payload !== 'object' || !('player_id' in payload)) return undefined;
  const value = (payload as Record<string, unknown>).player_id;
  return typeof value === 'string' ? value : undefined;
}

export function compareByCreatedAt(a: SyncAction, b: SyncAction): number {
  return a.created_at.localeCompare(b.created_at) || a.client_id.localeCompare(b.client_id);
}

function ownerAad(owner: Pick<LocalDataOwner, 'scope'>, store: string, primaryKey: string, discriminator: string): string {
  return JSON.stringify({ version: 2, store, primary_key: primaryKey, owner_scope: owner.scope, discriminator });
}

function assertOwner(owner: unknown): asserts owner is LocalDataOwner {
  const candidate = owner as Partial<LocalDataOwner> | null | undefined;
  if (!candidate?.subject || !candidate.scope || !candidate.keyId || !candidate.key || typeof candidate.isCurrent !== 'function') {
    throw new Error('Authenticated local-data owner key is unavailable');
  }
}

function assertOwnerCurrent(owner: LocalDataOwner): void {
  assertOwner(owner);
  if (!owner.isCurrent()) throw new Error('Authenticated local-data owner is stale');
}

async function ownerAwait<T>(owner: LocalDataOwner, work: Promise<T>): Promise<T> {
  assertOwnerCurrent(owner);
  const value = await work;
  assertOwnerCurrent(owner);
  return value;
}

async function abortTransaction(tx: { abort(): void; done: Promise<unknown> }): Promise<void> {
  tx.abort();
  try { await tx.done; } catch { /* expected abort */ }
}

async function guardTransaction(owner: LocalDataOwner, tx: { abort(): void; done: Promise<unknown> }): Promise<void> {
  try {
    assertOwnerCurrent(owner);
  } catch (error) {
    tx.abort();
    try { await tx.done; } catch { /* expected abort */ }
    throw error;
  }
}

async function transactionAwait<T>(
  owner: LocalDataOwner,
  tx: { abort(): void; done: Promise<unknown> },
  work: Promise<T>,
): Promise<T> {
  await guardTransaction(owner, tx);
  const value = await work;
  try {
    assertOwnerCurrent(owner);
  } catch (error) {
    await abortTransaction(tx);
    throw error;
  }
  return value;
}

function isEncryptedQueueRow(row: QueueRow): row is PersistedSyncQueueRow {
  const candidate = row as PersistedSyncQueueRow;
  return typeof candidate.owner_scope === 'string' && Boolean(candidate.envelope && typeof candidate.envelope === 'object' && 'version' in candidate.envelope);
}
function isSealedLocalRow(row: LocalRow): row is PersistedLocalRecord {
  const candidate = row as PersistedLocalRecord;
  return typeof candidate.sync_scope === 'string' && Boolean(candidate.envelope && typeof candidate.envelope === 'object' && 'version' in candidate.envelope);
}

const ENTITY_TYPES: readonly EntityType[] = ['player', 'consent', 'session', 'attendance', 'payment', 'entitlement', 'student_metrics'];
const SYNC_STATUSES: readonly SyncStatus[] = ['unsynced', 'applied', 'skipped', 'rejected', 'blocked'];

async function encryptQueueRow(
  action: SyncAction,
  owner: LocalDataOwner,
  status: SyncStatus,
  attemptCount = 0,
  reason?: string,
  revision = 1,
): Promise<PersistedSyncQueueRow> {
  assertOwnerCurrent(owner);
  const player_id = extractPlayerId(action.payload);
  const body: PersistedQueueBody = {
    client_id: action.client_id,
    owner_scope: owner.scope,
    entity: action.entity,
    created_at: action.created_at,
    status,
    attempt_count: attemptCount,
    storage_revision: revision,
    payload: action.payload as Record<string, unknown>,
    ...(player_id ? { player_id } : {}),
    ...(reason !== undefined ? { reason } : {}),
  };
  const envelope = await ownerAwait(owner, encryptEnvelope(
    owner.key,
    owner.keyId,
    body,
    ownerAad(owner, 'sync_queue', action.client_id, 'queue-action'),
    revision,
  ));
  return { client_id: action.client_id, created_at: action.created_at, status, attempt_count: attemptCount, owner_scope: owner.scope, envelope };
}

function validQueueBody(body: PersistedQueueBody): boolean {
  return ENTITY_TYPES.includes(body.entity) && SYNC_STATUSES.includes(body.status) &&
    Boolean(body.payload && typeof body.payload === 'object') &&
    Number.isInteger(body.attempt_count) && body.attempt_count >= 0 &&
    Number.isInteger(body.storage_revision) && body.storage_revision >= 1;
}

async function decryptQueueRow(row: PersistedSyncQueueRow, owner: LocalDataOwner): Promise<StoredSyncAction> {
  assertOwnerCurrent(owner);
  if (row.owner_scope !== owner.scope) throw new Error('Local queue item belongs to another owner');
  const body = await ownerAwait(owner, decryptEnvelope<PersistedQueueBody>(
    owner.key,
    owner.keyId,
    row.envelope,
    ownerAad(owner, 'sync_queue', row.client_id, 'queue-action'),
  ));
  if (!validQueueBody(body) ||
      body.client_id !== row.client_id || body.owner_scope !== row.owner_scope ||
      body.created_at !== row.created_at || body.status !== row.status ||
      body.attempt_count !== row.attempt_count || body.storage_revision !== row.envelope.revision) {
    throw new Error('Encrypted queue body is inconsistent with its operational mirrors');
  }
  return {
    client_id: body.client_id,
    created_at: body.created_at,
    status: body.status,
    attempt_count: body.attempt_count,
    storage_revision: body.storage_revision,
    sync_scope: body.owner_scope,
    entity: body.entity,
    payload: body.payload,
    ...(body.player_id ? { player_id: body.player_id } : {}),
    ...(body.reason !== undefined ? { reason: body.reason } : {}),
  };
}

/** Load or create the persistent non-extractable key for an opaque subject digest. */
export async function activateOwnerDataKey(subject: string): Promise<{ keyId: string; key: CryptoKey }> {
  if (!subject) throw new Error('Cannot activate local data without a JWT subject');
  const ownerKeyId = `v2:${await sha256Base64Url(subject)}`;
  const db = await getDb();
  let row = await db.get('owner_data_keys', ownerKeyId);
  if (!row) {
    const candidate: OwnerKeyRow = { owner_key_id: ownerKeyId, key_id: newClientId(), key: await generateDataKey(), created_at: new Date().toISOString() };
    const tx = db.transaction('owner_data_keys', 'readwrite');
    const existing = await tx.store.get(ownerKeyId);
    if (!existing) await tx.store.put(candidate);
    await tx.done;
    row = existing ?? candidate;
  }
  return { keyId: row.key_id, key: row.key };
}

export async function enqueueAction<P extends Record<string, unknown>>(
  action: SyncAction<P>,
  options: { status?: SyncStatus; owner: LocalDataOwner },
): Promise<StoredSyncAction<P>> {
  return withLocalDataMutationLock(async () => {
    const owner = options.owner;
    const row = await encryptQueueRow(action, owner, options.status ?? 'unsynced');
    const db = await ownerAwait(owner, getDb());
    const tx = db.transaction('sync_queue', 'readwrite');
    await guardTransaction(owner, tx);
    await transactionAwait(owner, tx, tx.store.put(row));
    await guardTransaction(owner, tx);
    await ownerAwait(owner, tx.done);
    return await decryptQueueRow(row, owner) as StoredSyncAction<P>;
  });
}

async function ownerQueueRows(owner: LocalDataOwner): Promise<PersistedSyncQueueRow[]> {
  assertOwnerCurrent(owner);
  const db = await ownerAwait(owner, getDb());
  const rows = await ownerAwait(owner, db.getAllFromIndex('sync_queue', 'by_scope', owner.scope));
  return rows.filter((row): row is PersistedSyncQueueRow => isEncryptedQueueRow(row) && row.owner_scope === owner.scope);
}

async function inspectOwnerQueue(owner: LocalDataOwner): Promise<{ actions: StoredSyncAction[]; corrupt: number }> {
  assertOwnerCurrent(owner);
  const actions: StoredSyncAction[] = [];
  let corrupt = 0;
  for (const row of await ownerQueueRows(owner)) {
    try { actions.push(await decryptQueueRow(row, owner)); }
    catch (error) {
      assertOwnerCurrent(owner);
      corrupt += 1;
    }
  }

  // A damaged clear owner_scope must not hide current-owner work from the
  // by_scope index. Probe only out-of-index v2 rows with this owner's key/AAD;
  // rows belonging to another owner simply fail authentication and are ignored.
  const db = await ownerAwait(owner, getDb());
  const allRows = await ownerAwait(owner, db.getAll('sync_queue'));
  for (const row of allRows) {
    if (!isEncryptedQueueRow(row) || row.owner_scope === owner.scope) continue;
    try {
      const body = await ownerAwait(owner, decryptEnvelope<PersistedQueueBody>(
        owner.key,
        owner.keyId,
        row.envelope,
        ownerAad(owner, 'sync_queue', row.client_id, 'queue-action'),
      ));
      if (body.owner_scope === owner.scope && body.client_id === row.client_id) corrupt += 1;
    } catch {
      assertOwnerCurrent(owner);
    }
  }
  return { actions, corrupt };
}

export async function getActionsByStatus(status: SyncStatus, owner: LocalDataOwner): Promise<StoredSyncAction[]> {
  const inspected = await inspectOwnerQueue(owner);
  assertOwnerCurrent(owner);
  return inspected.actions.filter((action) => action.status === status);
}

export async function getUnsyncedActions(owner: LocalDataOwner): Promise<StoredSyncAction[]> {
  return (await getActionsByStatus('unsynced', owner)).sort(compareByCreatedAt);
}

/** Count sealed legacy queue entries. They are never attributed or transmitted. */
export async function countQuarantinedLegacyActions(): Promise<number> {
  const db = await getDb();
  return db.countFromIndex('legacy_quarantine', 'by_source_store', 'sync_queue');
}

/** Count sealed legacy local-domain entries. */
export async function countQuarantinedLegacyRecords(): Promise<number> {
  const db = await getDb();
  const rows = await db.getAll('legacy_quarantine');
  return rows.filter((row) => LOCAL_RECORD_STORES.includes(row.source_store as LocalRecordStore)).length;
}

/** Combined queue, local-record, and scoped-metadata quarantine count used by sync health. */
export async function countQuarantinedLegacyData(): Promise<number> {
  const db = await getDb();
  return db.count('legacy_quarantine');
}

/** Explicit clear operational count for background scheduling only. */
export async function countOperationalUnsynced(): Promise<number> {
  const db = await getDb();
  return db.countFromIndex('sync_queue', 'by_status', 'unsynced');
}

/** Authenticated count: corrupt owner rows conservatively remain pending. */
export async function countUnsynced(owner: LocalDataOwner): Promise<number> {
  const inspected = await inspectOwnerQueue(owner);
  return inspected.actions.filter((action) => action.status === 'unsynced').length + inspected.corrupt;
}

export async function countActionsByStatus(owner: LocalDataOwner, status: SyncStatus): Promise<number> {
  const inspected = await inspectOwnerQueue(owner);
  const valid = inspected.actions.filter((action) => action.status === status).length;
  return status === 'unsynced' ? valid + inspected.corrupt : valid;
}

export async function getAction(clientId: string, owner: LocalDataOwner): Promise<StoredSyncAction | undefined> {
  assertOwnerCurrent(owner);
  const db = await ownerAwait(owner, getDb());
  const row = await ownerAwait(owner, db.get('sync_queue', clientId));
  if (!row || !isEncryptedQueueRow(row) || row.owner_scope !== owner.scope) return undefined;
  return decryptQueueRow(row, owner);
}

export async function getAllActions(owner: LocalDataOwner): Promise<StoredSyncAction[]> {
  return (await inspectOwnerQueue(owner)).actions;
}

export async function getActionsByPlayer(playerId: string, owner: LocalDataOwner): Promise<StoredSyncAction[]> {
  return (await getAllActions(owner)).filter((action) => action.player_id === playerId);
}

/** Guarded revision replacement; stale generations and lost updates never commit. */
async function putActionUnlocked(action: StoredSyncAction, owner: LocalDataOwner): Promise<void> {
  assertOwnerCurrent(owner);
  if (action.sync_scope !== owner.scope) throw new Error('Cannot persist another owner’s queue item');
  const expectedRevision = action.storage_revision;
  const replacement = await encryptQueueRow(
    action,
    owner,
    action.status,
    action.attempt_count,
    action.reason,
    expectedRevision + 1,
  );
  const db = await ownerAwait(owner, getDb());
  const tx = db.transaction('sync_queue', 'readwrite');
  await guardTransaction(owner, tx);
  const current = await transactionAwait(owner, tx, tx.store.get(action.client_id));
  await guardTransaction(owner, tx);
  if (!current || !isEncryptedQueueRow(current) || current.owner_scope !== owner.scope || current.envelope.revision !== expectedRevision) {
    await abortTransaction(tx);
    throw new Error('Local queue item changed before guarded update');
  }
  await transactionAwait(owner, tx, tx.store.put(replacement));
  await guardTransaction(owner, tx);
  await ownerAwait(owner, tx.done);
}

export function putAction(action: StoredSyncAction, owner: LocalDataOwner): Promise<void> {
  return withLocalDataMutationLock(() => putActionUnlocked(action, owner));
}

export async function bumpActionAttempt(
  expected: Pick<StoredSyncAction, 'client_id' | 'storage_revision'>,
  owner: LocalDataOwner,
): Promise<boolean> {
  const action = await getAction(expected.client_id, owner);
  if (!action || action.status !== 'unsynced' || action.storage_revision !== expected.storage_revision) return false;
  try {
    await putAction({ ...action, attempt_count: action.attempt_count + 1 }, owner);
    return true;
  } catch (error) {
    assertOwnerCurrent(owner);
    if (error instanceof Error && error.message.includes('changed before guarded update')) return false;
    throw error;
  }
}

export async function updateActionStatus(
  expected: Pick<StoredSyncAction, 'client_id' | 'storage_revision'>,
  status: SyncStatus,
  owner: LocalDataOwner,
  reason?: string,
): Promise<boolean> {
  const action = await getAction(expected.client_id, owner);
  if (!action || action.storage_revision !== expected.storage_revision) return false;
  try {
    await putAction({ ...action, status, ...(reason !== undefined ? { reason } : {}) }, owner);
    return true;
  } catch (error) {
    assertOwnerCurrent(owner);
    if (error instanceof Error && error.message.includes('changed before guarded update')) return false;
    throw error;
  }
}

function localBodyFromRecord(record: LocalRecord, personal?: unknown): PersistedLocalBody {
  const logical: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(record)) {
    if (key !== 'local_id' && key !== 'client_id' && key !== 'sync_scope' && key !== 'enc') logical[key] = value;
  }
  return { record: logical, ...(personal !== undefined ? { personal } : {}) };
}

async function sealLocalRecord(
  store: LocalRecordStore,
  record: LocalRecord,
  personal: unknown,
  owner: LocalDataOwner,
  revision = 1,
): Promise<PersistedLocalRecord> {
  assertOwnerCurrent(owner);
  if (!record.local_id) throw new Error('Local record id is required');
  const envelope = await ownerAwait(owner, encryptEnvelope(
    owner.key,
    owner.keyId,
    localBodyFromRecord(record, personal),
    ownerAad(owner, store, record.local_id, 'complete-local-record'),
    revision,
  ));
  return {
    local_id: record.local_id,
    ...(typeof record.client_id === 'string' ? { client_id: record.client_id } : {}),
    sync_scope: owner.scope,
    envelope,
  };
}

async function decryptLocalRow(store: LocalRecordStore, row: PersistedLocalRecord, owner: LocalDataOwner): Promise<LocalRecord> {
  assertOwnerCurrent(owner);
  if (row.sync_scope !== owner.scope) throw new Error('Local record belongs to another owner');
  const body = await ownerAwait(owner, decryptEnvelope<PersistedLocalBody>(
    owner.key,
    owner.keyId,
    row.envelope,
    ownerAad(owner, store, row.local_id, 'complete-local-record'),
  ));
  if (!body || typeof body.record !== 'object' || Array.isArray(body.record)) throw new Error('Encrypted local record is malformed');
  return {
    local_id: row.local_id,
    ...(row.client_id ? { client_id: row.client_id } : {}),
    sync_scope: row.sync_scope,
    ...body.record,
    enc: row.envelope,
  };
}

async function writeLocalRecordUnlocked(store: LocalRecordStore, record: LocalRecord, owner: LocalDataOwner): Promise<void> {
  if (record.sync_scope && record.sync_scope !== owner.scope) throw new Error('Cannot persist another owner’s local record');
  const row = await sealLocalRecord(store, record, undefined, owner);
  const db = await ownerAwait(owner, getDb());
  const tx = db.transaction(store, 'readwrite');
  await guardTransaction(owner, tx);
  await transactionAwait(owner, tx, tx.store.put(row));
  await guardTransaction(owner, tx);
  await ownerAwait(owner, tx.done);
}

export function writeLocalRecord(store: LocalRecordStore, record: LocalRecord, owner: LocalDataOwner): Promise<void> {
  return withLocalDataMutationLock(() => writeLocalRecordUnlocked(store, record, owner));
}

export async function getLocalRecord(store: LocalRecordStore, localId: string, owner: LocalDataOwner): Promise<LocalRecord | undefined> {
  assertOwnerCurrent(owner);
  const db = await ownerAwait(owner, getDb());
  const row = await ownerAwait(owner, db.get(store, localId));
  if (!row || !isSealedLocalRow(row) || row.sync_scope !== owner.scope) return undefined;
  return decryptLocalRow(store, row, owner);
}

export async function getAllLocalRecords(store: LocalRecordStore, owner: LocalDataOwner): Promise<LocalRecord[]> {
  assertOwnerCurrent(owner);
  const db = await ownerAwait(owner, getDb());
  const rows = await ownerAwait(owner, db.getAll(store));
  const output: LocalRecord[] = [];
  for (const row of rows) {
    if (isSealedLocalRow(row) && row.sync_scope === owner.scope) output.push(await decryptLocalRow(store, row, owner));
  }
  return output;
}

/** Clear indexes are intentionally unused for sealed rows; decrypt then filter logical views. */
export async function getLocalRecordsByIndex(
  store: LocalRecordStore,
  index: string,
  key: IDBValidKey,
  owner: LocalDataOwner,
): Promise<LocalRecord[]> {
  const records = await getAllLocalRecords(store, owner);
  assertOwnerCurrent(owner);
  return records.filter((record) => record[index] === key);
}

/** Decrypt only the personal fragment from a complete sealed local row. */
export async function decryptLocalPersonal<T>(store: LocalRecordStore, record: LocalRecord, owner: LocalDataOwner): Promise<T | null> {
  assertOwnerCurrent(owner);
  if (record.sync_scope !== owner.scope || !record.enc || !('version' in record.enc)) return null;
  const body = await ownerAwait(owner, decryptEnvelope<PersistedLocalBody>(
    owner.key,
    owner.keyId,
    record.enc,
    ownerAad(owner, store, record.local_id, 'complete-local-record'),
  ));
  return body.personal === undefined ? null : body.personal as T;
}

export interface PreparedLocalWrite { store: LocalRecordStore; record: PersistedLocalRecord }

export async function prepareLocalRecord(
  store: LocalRecordStore,
  record: LocalRecord,
  personal: unknown,
  owner: LocalDataOwner,
): Promise<PreparedLocalWrite> {
  return { store, record: await sealLocalRecord(store, record, personal, owner) };
}

export async function prepareQueueAction(action: SyncAction, status: SyncStatus | undefined, owner: LocalDataOwner): Promise<PersistedSyncQueueRow> {
  return encryptQueueRow(action, owner, status ?? 'unsynced');
}

/** Atomically commit pre-encrypted local rows and queue rows. */
async function commitPreparedCaptureUnlocked(
  records: PreparedLocalWrite[],
  actions: PersistedSyncQueueRow[],
  owner: LocalDataOwner,
): Promise<void> {
  assertOwnerCurrent(owner);
  const db = await ownerAwait(owner, getDb());
  const stores: Array<LocalRecordStore | 'sync_queue'> = Array.from(new Set([...records.map((item) => item.store), 'sync_queue' as const]));
  const tx = db.transaction(stores, 'readwrite');
  await guardTransaction(owner, tx);
  for (const { store, record } of records) {
    await transactionAwait(owner, tx, tx.objectStore(store).put(record));
    await guardTransaction(owner, tx);
  }
  for (const row of actions) {
    await transactionAwait(owner, tx, tx.objectStore('sync_queue').put(row));
    await guardTransaction(owner, tx);
  }
  await guardTransaction(owner, tx);
  await ownerAwait(owner, tx.done);
}

export function commitPreparedCapture(
  records: PreparedLocalWrite[],
  actions: PersistedSyncQueueRow[],
  owner: LocalDataOwner,
): Promise<void> {
  return withLocalDataMutationLock(() => commitPreparedCaptureUnlocked(records, actions, owner));
}

async function opaqueKey(owner: LocalDataOwner, values: string[]): Promise<string> {
  assertOwnerCurrent(owner);
  const digest = new Uint8Array(await ownerAwait(owner, globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(values)))));
  let binary = '';
  for (const byte of digest) binary += String.fromCharCode(byte);
  return `v2:${btoa(binary)}`;
}

async function writeCachedReadUnlocked<T>(key: string, data: T, owner: LocalDataOwner, cachedAt = new Date().toISOString()): Promise<void> {
  assertOwnerCurrent(owner);
  const physicalKey = await opaqueKey(owner, [owner.scope, key]);
  const body = { key, cached_at: cachedAt, data };
  const envelope = await ownerAwait(owner, encryptEnvelope(owner.key, owner.keyId, body, ownerAad(owner, 'cached_reads', physicalKey, key)));
  const row: PersistedCachedRead = { key: physicalKey, owner_scope: owner.scope, cached_at: cachedAt, envelope };
  const db = await ownerAwait(owner, getDb());
  const tx = db.transaction('cached_reads', 'readwrite');
  await guardTransaction(owner, tx);
  await transactionAwait(owner, tx, tx.store.put(row));
  await guardTransaction(owner, tx);
  await ownerAwait(owner, tx.done);
}

export function writeCachedRead<T>(key: string, data: T, owner: LocalDataOwner, cachedAt = new Date().toISOString()): Promise<void> {
  return withLocalDataMutationLock(() => writeCachedReadUnlocked(key, data, owner, cachedAt));
}

export async function getCachedRead<T>(key: string, owner: LocalDataOwner): Promise<CachedRead<T> | undefined> {
  assertOwnerCurrent(owner);
  const physicalKey = await opaqueKey(owner, [owner.scope, key]);
  const db = await ownerAwait(owner, getDb());
  const row = await ownerAwait(owner, db.get('cached_reads', physicalKey));
  if (!row) return undefined;
  if (row.owner_scope !== owner.scope) throw new Error('Cached dataset belongs to another owner');
  const body = await ownerAwait(owner, decryptEnvelope<{ key: string; cached_at: string; data: T }>(owner.key, owner.keyId, row.envelope, ownerAad(owner, 'cached_reads', physicalKey, key)));
  if (body.key !== key || body.cached_at !== row.cached_at) throw new Error('Cached dataset mirrors are inconsistent');
  return body;
}

async function writeBalancesUnlocked(playerId: string, balances: BalanceOut[], owner: LocalDataOwner, cachedAt = new Date().toISOString()): Promise<void> {
  assertOwnerCurrent(owner);
  const key = await opaqueKey(owner, [owner.scope, playerId]);
  const body = { player_id: playerId, cached_at: cachedAt, balances };
  const envelope = await ownerAwait(owner, encryptEnvelope(owner.key, owner.keyId, body, ownerAad(owner, 'entitlement_balances', key, playerId)));
  const row: PersistedBalances = { player_id: key, owner_scope: owner.scope, cached_at: cachedAt, envelope };
  const db = await ownerAwait(owner, getDb());
  const tx = db.transaction('entitlement_balances', 'readwrite');
  await guardTransaction(owner, tx);
  await transactionAwait(owner, tx, tx.store.put(row));
  await guardTransaction(owner, tx);
  await ownerAwait(owner, tx.done);
}

export function writeBalances(playerId: string, balances: BalanceOut[], owner: LocalDataOwner, cachedAt = new Date().toISOString()): Promise<void> {
  return withLocalDataMutationLock(() => writeBalancesUnlocked(playerId, balances, owner, cachedAt));
}

export async function getBalances(playerId: string, owner: LocalDataOwner): Promise<CachedBalances | undefined> {
  assertOwnerCurrent(owner);
  const key = await opaqueKey(owner, [owner.scope, playerId]);
  const db = await ownerAwait(owner, getDb());
  const row = await ownerAwait(owner, db.get('entitlement_balances', key));
  if (!row) return undefined;
  if (row.owner_scope !== owner.scope) throw new Error('Cached balances belong to another owner');
  const body = await ownerAwait(owner, decryptEnvelope<CachedBalances>(owner.key, owner.keyId, row.envelope, ownerAad(owner, 'entitlement_balances', key, playerId)));
  if (body.player_id !== playerId || body.cached_at !== row.cached_at) throw new Error('Cached balance mirrors are inconsistent');
  return body;
}

export const SESSION_META_KEY = 'session';
export const SESSION_KEY_META_KEY = 'session_key';
export const SESSION_OWNER_META_KEY = 'session_owner';
export interface PersistedAuthMetadata { encryptedSession: EncryptedField | undefined; sessionKey: CryptoKey | undefined; owner: string | null }
function normalizeAuthOwner(value: unknown): string | null { return typeof value === 'string' && value.length > 0 ? value : null; }

export async function getAuthMetadata(): Promise<PersistedAuthMetadata> {
  const db = await getDb();
  const tx = db.transaction('meta', 'readonly');
  const [sessionEntry, keyEntry, ownerEntry] = await Promise.all([tx.store.get(SESSION_META_KEY), tx.store.get(SESSION_KEY_META_KEY), tx.store.get(SESSION_OWNER_META_KEY)]);
  await tx.done;
  return { encryptedSession: sessionEntry?.value as EncryptedField | undefined, sessionKey: keyEntry?.value as CryptoKey | undefined, owner: normalizeAuthOwner(ownerEntry?.value) };
}
export async function replaceAuthMetadata(encryptedSession: EncryptedField, sessionKey: CryptoKey, owner: string): Promise<void> {
  const db = await getDb();
  const tx = db.transaction('meta', 'readwrite');
  await Promise.all([tx.store.put({ key: SESSION_META_KEY, value: encryptedSession }), tx.store.put({ key: SESSION_KEY_META_KEY, value: sessionKey }), tx.store.put({ key: SESSION_OWNER_META_KEY, value: owner })]);
  await tx.done;
}
export async function deleteAuthMetadataIfOwnedBy(expectedOwner: string | null): Promise<boolean> {
  const db = await getDb();
  const tx = db.transaction('meta', 'readwrite');
  const ownerEntry = await tx.store.get(SESSION_OWNER_META_KEY);
  if (normalizeAuthOwner(ownerEntry?.value) !== expectedOwner) { await tx.done; return false; }
  await Promise.all([tx.store.delete(SESSION_META_KEY), tx.store.delete(SESSION_KEY_META_KEY), tx.store.delete(SESSION_OWNER_META_KEY)]);
  await tx.done;
  return true;
}
export async function getMeta<T = unknown>(key: string): Promise<T | undefined> {
  const db = await getDb();
  const entry = await db.get('meta', key);
  return entry ? entry.value as T : undefined;
}
export async function setMeta(key: string, value: unknown): Promise<void> { const db = await getDb(); await db.put('meta', { key, value }); }
export async function deleteMeta(key: string): Promise<void> { const db = await getDb(); await db.delete('meta', key); }

export interface OwnerMetadataSnapshot<T> {
  value: T;
  storage_revision: number;
}

async function setOwnerMetadataUnlocked<T>(
  key: string,
  value: T,
  owner: LocalDataOwner,
  expectedRevision: number,
): Promise<void> {
  assertOwnerCurrent(owner);
  if (!Number.isInteger(expectedRevision) || expectedRevision < 0) throw new Error('Expected owner metadata revision is required');
  const envelope = await ownerAwait(owner, encryptEnvelope(owner.key, owner.keyId, value, ownerAad(owner, 'meta', key, key), expectedRevision + 1));
  const db = await ownerAwait(owner, getDb());
  const tx = db.transaction('meta', 'readwrite');
  await guardTransaction(owner, tx);
  const current = await transactionAwait(owner, tx, tx.store.get(key));
  const currentValue = current?.value as PersistedOwnerMetadata | undefined;
  const currentRevision = currentValue?.owner_scope === owner.scope && currentValue.envelope ? currentValue.envelope.revision : 0;
  if (currentRevision !== expectedRevision) { await abortTransaction(tx); throw new Error('Owner metadata changed before guarded update'); }
  await transactionAwait(owner, tx, tx.store.put({ key, value: { owner_scope: owner.scope, envelope } satisfies PersistedOwnerMetadata }));
  await guardTransaction(owner, tx);
  await ownerAwait(owner, tx.done);
}

export function setOwnerMetadata<T>(
  key: string,
  value: T,
  owner: LocalDataOwner,
  expectedRevision: number,
): Promise<void> {
  return withLocalDataMutationLock(() => setOwnerMetadataUnlocked(key, value, owner, expectedRevision));
}

export async function getOwnerMetadataSnapshot<T>(key: string, owner: LocalDataOwner): Promise<OwnerMetadataSnapshot<T> | undefined> {
  assertOwnerCurrent(owner);
  const db = await ownerAwait(owner, getDb());
  const entry = await ownerAwait(owner, db.get('meta', key));
  if (!entry) return undefined;
  const stored = entry.value;
  if (stored && typeof stored === 'object' && 'owner_scope' in stored && 'envelope' in stored) {
    const row = stored as PersistedOwnerMetadata;
    if (row.owner_scope !== owner.scope) throw new Error('Owner metadata belongs to another owner');
    const value = await ownerAwait(owner, decryptEnvelope<T>(owner.key, owner.keyId, row.envelope, ownerAad(owner, 'meta', key, key)));
    return { value, storage_revision: row.envelope.revision };
  }
  if (!key.endsWith(`:${owner.scope}`)) return undefined;
  try {
    await setOwnerMetadata(key, stored as T, owner, 0);
  } catch (error) {
    assertOwnerCurrent(owner);
    if (!(error instanceof Error) || !error.message.includes('changed before guarded update')) throw error;
  }
  return getOwnerMetadataSnapshot<T>(key, owner);
}

export async function getOwnerMetadata<T>(key: string, owner: LocalDataOwner): Promise<T | undefined> {
  return (await getOwnerMetadataSnapshot<T>(key, owner))?.value;
}

/** Atomic retrying merge for D2 maps; no stale read can erase newer mappings. */
export async function mergeOwnerMetadata(
  key: string,
  additions: Record<string, string>,
  owner: LocalDataOwner,
): Promise<void> {
  for (;;) {
    assertOwnerCurrent(owner);
    const snapshot = await getOwnerMetadataSnapshot<Record<string, string>>(key, owner);
    const merged = { ...(snapshot?.value ?? {}), ...additions };
    try {
      await setOwnerMetadata(key, merged, owner, snapshot?.storage_revision ?? 0);
      return;
    } catch (error) {
      assertOwnerCurrent(owner);
      if (!(error instanceof Error) || !error.message.includes('changed before guarded update')) throw error;
    }
  }
}

export async function getOrCreateDeviceId(): Promise<string> {
  const existing = await getMeta<string>('device_id');
  if (existing) return existing;
  const id = newClientId();
  await setMeta('device_id', id);
  return id;
}

function lastSuccessfulSyncKey(scope: string): string { return `last_successful_sync:${scope}`; }
export async function getLastSuccessfulSync(owner: LocalDataOwner): Promise<string | null> {
  assertOwnerCurrent(owner);
  const key = lastSuccessfulSyncKey(owner.scope);
  const db = await ownerAwait(owner, getDb());
  const entry = await ownerAwait(owner, db.get('meta', key));
  if (!entry) return null;
  const row = entry.value as PersistedLastSync;
  if (!row || row.owner_scope !== owner.scope || !row.envelope) return null;
  const body = await ownerAwait(owner, decryptEnvelope<{ timestamp: string; storage_revision: number }>(owner.key, owner.keyId, row.envelope, ownerAad(owner, 'meta', key, 'last-successful-sync')));
  if (body.storage_revision !== row.envelope.revision) throw new Error('Sync metadata revision is inconsistent');
  return body.timestamp;
}

async function setLastSuccessfulSyncUnlocked(timestamp: string, owner: LocalDataOwner): Promise<void> {
  assertOwnerCurrent(owner);
  const key = lastSuccessfulSyncKey(owner.scope);
  const db = await ownerAwait(owner, getDb());
  const initial = await ownerAwait(owner, db.get('meta', key));
  const initialRow = initial?.value as PersistedLastSync | undefined;
  const expectedRevision = initialRow?.owner_scope === owner.scope && initialRow.envelope ? initialRow.envelope.revision : 0;
  const envelope = await ownerAwait(owner, encryptEnvelope(owner.key, owner.keyId, { timestamp, storage_revision: expectedRevision + 1 }, ownerAad(owner, 'meta', key, 'last-successful-sync'), expectedRevision + 1));
  const tx = db.transaction('meta', 'readwrite');
  await guardTransaction(owner, tx);
  const current = await transactionAwait(owner, tx, tx.store.get(key));
  const currentRow = current?.value as PersistedLastSync | undefined;
  const currentRevision = currentRow?.owner_scope === owner.scope && currentRow.envelope ? currentRow.envelope.revision : 0;
  if (currentRevision !== expectedRevision) { await abortTransaction(tx); throw new Error('Sync metadata changed before guarded update'); }
  await transactionAwait(owner, tx, tx.store.put({ key, value: { owner_scope: owner.scope, envelope } satisfies PersistedLastSync }));
  await guardTransaction(owner, tx);
  await ownerAwait(owner, tx.done);
}

export function setLastSuccessfulSync(timestamp: string, owner: LocalDataOwner): Promise<void> {
  return withLocalDataMutationLock(() => setLastSuccessfulSyncUnlocked(timestamp, owner));
}

function sourceMatches(a: unknown, b: unknown): boolean {
  try { return JSON.stringify(a) === JSON.stringify(b); } catch { return false; }
}

function isStringMap(value: unknown): value is Record<string, string> {
  return Boolean(
    value &&
    typeof value === 'object' &&
    !Array.isArray(value) &&
    Object.entries(value).every(([key, item]) => key.length > 0 && typeof item === 'string'),
  );
}

async function migrateQuarantinedLegacyMetadata(
  owner: LocalDataOwner,
  db: IDBPDatabase<FunhouseDB>,
  row: LegacyQuarantineRow,
  body: LegacyQuarantineBody,
  legacyScope: string,
): Promise<void> {
  const source = body.source as MetaEntry;
  const playerKey = `player_id_resolutions:${legacyScope}`;
  const sessionKey = `session_id_resolutions:${legacyScope}`;
  const lastSyncKey = `last_successful_sync:${legacyScope}`;
  if (source.key !== playerKey && source.key !== sessionKey && source.key !== lastSyncKey) return;

  if (source.key === lastSyncKey) {
    // Approved migration policy: the legacy timestamp is disposable status.
    // Exact attribution removes its sealed copy; the next successful sync writes
    // a fresh owner-encrypted timestamp under the opaque v2 scope.
    const tx = db.transaction('legacy_quarantine', 'readwrite');
    await guardTransaction(owner, tx);
    const current = await transactionAwait(owner, tx, tx.store.get(row.quarantine_id));
    if (!sourceMatches(current, row)) { await abortTransaction(tx); return; }
    await transactionAwait(owner, tx, tx.store.delete(row.quarantine_id));
    await guardTransaction(owner, tx);
    await ownerAwait(owner, tx.done);
    return;
  }

  if (!isStringMap(source.value)) return;
  const prefix = source.key === playerKey ? 'player_id_resolutions' : 'session_id_resolutions';
  const opaqueKey = `${prefix}:${owner.scope}`;
  const existing = await getOwnerMetadataSnapshot<Record<string, string>>(opaqueKey, owner);
  if (existing && !isStringMap(existing.value)) return;
  const merged = { ...source.value, ...(existing?.value ?? {}) };
  const expectedRevision = existing?.storage_revision ?? 0;
  const envelope = await ownerAwait(owner, encryptEnvelope(
    owner.key,
    owner.keyId,
    merged,
    ownerAad(owner, 'meta', opaqueKey, opaqueKey),
    expectedRevision + 1,
  ));

  const tx = db.transaction(['meta', 'legacy_quarantine'], 'readwrite');
  await guardTransaction(owner, tx);
  const [currentQuarantine, currentOpaque] = await Promise.all([
    transactionAwait(owner, tx, tx.objectStore('legacy_quarantine').get(row.quarantine_id)),
    transactionAwait(owner, tx, tx.objectStore('meta').get(opaqueKey)),
  ]);
  const currentOpaqueValue = currentOpaque?.value as PersistedOwnerMetadata | undefined;
  const currentRevision = !currentOpaque
    ? 0
    : currentOpaqueValue?.owner_scope === owner.scope && currentOpaqueValue.envelope
      ? currentOpaqueValue.envelope.revision
      : -1;
  if (!sourceMatches(currentQuarantine, row) || currentRevision !== expectedRevision) {
    await abortTransaction(tx);
    return;
  }
  await transactionAwait(owner, tx, tx.objectStore('meta').put({
    key: opaqueKey,
    value: { owner_scope: owner.scope, envelope } satisfies PersistedOwnerMetadata,
  }));
  await transactionAwait(owner, tx, tx.objectStore('legacy_quarantine').delete(row.quarantine_id));
  await guardTransaction(owner, tx);
  await ownerAwait(owner, tx.done);
}

async function migrateScopedLegacyDataUnlocked(
  owner: LocalDataOwner,
  legacySessionKey: CryptoKey,
  legacyScope: string,
): Promise<void> {
  assertOwnerCurrent(owner);
  const db = await ownerAwait(owner, getDb());
  const quarantined = await ownerAwait(owner, db.getAll('legacy_quarantine'));
  if (quarantined.length === 0) return;
  const quarantineKey = await ownerAwait(owner, getOrCreateQuarantineKey(db));

  for (const row of quarantined) {
    assertOwnerCurrent(owner);
    let body: LegacyQuarantineBody;
    try {
      body = await ownerAwait(owner, decryptEnvelope<LegacyQuarantineBody>(
        quarantineKey,
        QUARANTINE_KEY_ID,
        row.envelope,
        quarantineAad(row.source_store, row.quarantine_id),
      ));
    } catch {
      assertOwnerCurrent(owner);
      continue;
    }
    if (
      body.version !== 1 ||
      body.source_store !== row.source_store ||
      typeof body.source_key !== 'string' ||
      body.source_key.length === 0
    ) continue;
    const actualSourceKey = row.source_store === 'sync_queue'
      ? (body.source as LegacySyncQueueRow).client_id
      : row.source_store === 'meta'
        ? (body.source as MetaEntry).key
        : (body.source as LocalRecord).local_id;
    if (actualSourceKey !== body.source_key) continue;

    if (row.source_store === 'meta') {
      try {
        await migrateQuarantinedLegacyMetadata(owner, db, row, body, legacyScope);
      } catch {
        assertOwnerCurrent(owner);
        // Corrupt, malformed, stale, or concurrently changed metadata remains
        // encrypted and counted for an exact-owner retry.
      }
      continue;
    }

    if (row.source_store === 'sync_queue') {
      const source = body.source as LegacySyncQueueRow;
      if (
        source.sync_scope !== legacyScope ||
        typeof source.client_id !== 'string' ||
        typeof source.created_at !== 'string' ||
        !source.entity || !ENTITY_TYPES.includes(source.entity) ||
        !source.payload || typeof source.payload !== 'object' ||
        !SYNC_STATUSES.includes(source.status) ||
        !Number.isInteger(source.attempt_count) || source.attempt_count! < 0
      ) continue;
      try {
        const replacement = await encryptQueueRow(
          {
            client_id: source.client_id,
            entity: source.entity,
            created_at: source.created_at,
            payload: source.payload,
          },
          owner,
          source.status,
          source.attempt_count,
          source.reason,
        );
        const tx = db.transaction(['sync_queue', 'legacy_quarantine'], 'readwrite');
        await guardTransaction(owner, tx);
        const current = await tx.objectStore('legacy_quarantine').get(row.quarantine_id);
        if (!sourceMatches(current, row)) { await abortTransaction(tx); continue; }
        await tx.objectStore('sync_queue').put(replacement);
        await tx.objectStore('legacy_quarantine').delete(row.quarantine_id);
        await guardTransaction(owner, tx);
        await ownerAwait(owner, tx.done);
      } catch {
        assertOwnerCurrent(owner);
      }
      continue;
    }

    const store = row.source_store as LocalRecordStore;
    const source = body.source as LocalRecord;
    if (source.sync_scope !== legacyScope || typeof source.local_id !== 'string') continue;
    try {
      let personal: unknown = undefined;
      if (source.enc) {
        personal = await ownerAwait(owner, decryptPayload(
          legacySessionKey,
          source.enc as EncryptedField,
        ));
      }
      const replacement = await sealLocalRecord(store, source, personal, owner);
      const tx = db.transaction([store, 'legacy_quarantine'], 'readwrite');
      await guardTransaction(owner, tx);
      const current = await tx.objectStore('legacy_quarantine').get(row.quarantine_id);
      if (!sourceMatches(current, row)) { await abortTransaction(tx); continue; }
      await tx.objectStore(store).put(replacement);
      await tx.objectStore('legacy_quarantine').delete(row.quarantine_id);
      await guardTransaction(owner, tx);
      await ownerAwait(owner, tx.done);
    } catch {
      assertOwnerCurrent(owner);
      // Wrong/corrupt legacy personal keys retain encrypted quarantine intact.
    }
  }
}

/**
 * Resumable exact-scope migration from encrypted device quarantine. AuthManager
 * passes `mutationLockHeld` because it already holds auth-session then local-data
 * locks in that order; direct callers acquire the mutation lock here.
 */
export function migrateScopedLegacyData(
  owner: LocalDataOwner,
  legacySessionKey: CryptoKey,
  options: { legacyScope?: string; mutationLockHeld?: boolean } = {},
): Promise<void> {
  const operation = () => migrateScopedLegacyDataUnlocked(
    owner,
    legacySessionKey,
    options.legacyScope ?? owner.scope,
  );
  return options.mutationLockHeld ? operation() : withLocalDataMutationLock(operation);
}
