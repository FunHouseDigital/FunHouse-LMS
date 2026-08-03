/**
 * Local_Store — the device's IndexedDB persistence layer (Req 4, 5, 6, 8.2).
 *
 * A single IndexedDB database (`funhouse-revenue`, version-managed) accessed
 * through the thin `idb` wrapper. It holds:
 *  - the durable Sync_Queue (`sync_queue`),
 *  - one object store per captured entity (local domain records),
 *  - cached server reads (`cached_reads`, `entitlement_balances`),
 *  - device/auth metadata (`meta`).
 *
 * Stores, keyPaths, and indexes match design.md ("Local_Store schema" and
 * "IndexedDB store definitions") exactly.
 */
import { openDB, type IDBPDatabase, type DBSchema } from 'idb';
import type {
  BalanceOut,
  EncryptedField,
  StoredSyncAction,
  SyncAction,
  SyncStatus,
} from '../domain/types';

export const DB_NAME = 'funhouse-revenue';
export const DB_VERSION = 1;

/** Local domain-record stores (one per captured entity). All keyPath `local_id`. */
export type LocalRecordStore =
  | 'players'
  | 'sessions'
  | 'payments'
  | 'entitlements'
  | 'consents'
  | 'attendance'
  | 'student_metrics';

/**
 * A locally persisted domain record. Sensitive fields are stored as an
 * encrypted blob (see crypto service); non-sensitive index keys such as
 * `by_day`/`player_id`/`client_id` are stored in clear so indexing works.
 */
export interface LocalRecord {
  local_id: string;
  client_id?: string;
  player_id?: string;
  name?: string;
  day?: string;
  session_id?: string;
  /** AES-GCM envelope containing this record's personal fields. */
  enc?: EncryptedField;
  /** Authenticated account/location/school scope that owns this local mirror. */
  sync_scope?: string;
  [key: string]: unknown;
}

/** A cached server read (`cached_reads` store). */
export interface CachedRead<T = unknown> {
  key: string;
  data: T;
  cached_at: string;
}

/** A cached entitlement balance set (`entitlement_balances` store, Req 8.2/8.5). */
export interface CachedBalances {
  player_id: string;
  balances: BalanceOut[];
  cached_at: string;
}

/** A `meta` key/value entry. */
export interface MetaEntry {
  key: string;
  value: unknown;
}

interface FunhouseDB extends DBSchema {
  sync_queue: {
    key: string;
    value: StoredSyncAction;
    indexes: {
      by_status: string;
      by_entity: string;
      by_created_at: string;
      by_player: string;
    };
  };
  players: {
    key: string;
    value: LocalRecord;
    indexes: { by_client_id: string; by_name: string };
  };
  sessions: {
    key: string;
    value: LocalRecord;
    indexes: { by_day: string; by_player: string };
  };
  payments: {
    key: string;
    value: LocalRecord;
    indexes: { by_day: string; by_player: string };
  };
  entitlements: {
    key: string;
    value: LocalRecord;
    indexes: { by_player: string; by_client_id: string };
  };
  consents: {
    key: string;
    value: LocalRecord;
    indexes: { by_player: string };
  };
  attendance: {
    key: string;
    value: LocalRecord;
    indexes: { by_session: string; by_player: string };
  };
  student_metrics: {
    key: string;
    value: LocalRecord;
    indexes: { by_day: string };
  };
  cached_reads: {
    key: string;
    value: CachedRead;
  };
  entitlement_balances: {
    key: string;
    value: CachedBalances;
  };
  meta: {
    key: string;
    value: MetaEntry;
  };
}

let dbPromise: Promise<IDBPDatabase<FunhouseDB>> | null = null;

/** Open (or reuse) the Local_Store database, creating stores/indexes on upgrade. */
export function getDb(): Promise<IDBPDatabase<FunhouseDB>> {
  if (!dbPromise) {
    dbPromise = openDB<FunhouseDB>(DB_NAME, DB_VERSION, {
      upgrade(db) {
        // Sync_Queue (Req 4.2, 4.3, 5, 6)
        const queue = db.createObjectStore('sync_queue', { keyPath: 'client_id' });
        queue.createIndex('by_status', 'status');
        queue.createIndex('by_entity', 'entity');
        queue.createIndex('by_created_at', 'created_at');
        queue.createIndex('by_player', 'player_id');

        // Local domain-record stores (Req 4.1, 4.5, 9.3)
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

        // Cached server reads (Req 9.2, 13.5, 16.3, 8.2/8.5)
        db.createObjectStore('cached_reads', { keyPath: 'key' });
        db.createObjectStore('entitlement_balances', { keyPath: 'player_id' });

        // Auth / meta (Req 5.7, 6.4, 17)
        db.createObjectStore('meta', { keyPath: 'key' });
      },
    });
  }
  return dbPromise;
}

/**
 * Close the database and drop the cached connection so the next `getDb()`
 * reopens it. Used to simulate an app relaunch (Property 3) and in test setup.
 */
export async function closeDb(): Promise<void> {
  if (dbPromise) {
    const db = await dbPromise;
    db.close();
    dbPromise = null;
  }
}

// ---- Identity helpers ----

/** Generate a per-action `client_id`, unique across all actions on the device (Req 4.3). */
export function newClientId(): string {
  return globalThis.crypto.randomUUID();
}

function extractPlayerId(payload: unknown): string | undefined {
  if (payload && typeof payload === 'object' && 'player_id' in payload) {
    const value = (payload as Record<string, unknown>).player_id;
    return typeof value === 'string' ? value : undefined;
  }
  return undefined;
}

/** Deterministic Sync_Queue ordering: `created_at` ascending, tie-broken by `client_id` (Req 5.1). */
export function compareByCreatedAt(a: SyncAction, b: SyncAction): number {
  if (a.created_at < b.created_at) return -1;
  if (a.created_at > b.created_at) return 1;
  if (a.client_id < b.client_id) return -1;
  if (a.client_id > b.client_id) return 1;
  return 0;
}

// ---- Sync_Queue CRUD ----

/**
 * Enqueue a Sync_Action into the durable queue. The `player_id` mirror is
 * populated from the payload so the `by_player` index works (Req 8.2). Defaults
 * to `unsynced` status; pass `status: 'blocked'` for forward-compatible entities.
 */
export async function enqueueAction<P>(
  action: SyncAction<P>,
  opts: { status?: SyncStatus; scope?: string | null } = {},
): Promise<StoredSyncAction<P>> {
  const db = await getDb();
  const player_id = extractPlayerId(action.payload);
  const stored: StoredSyncAction<P> = {
    ...action,
    status: opts.status ?? 'unsynced',
    attempt_count: 0,
    ...(opts.scope ? { sync_scope: opts.scope } : {}),
    ...(player_id !== undefined ? { player_id } : {}),
  };
  await db.put('sync_queue', stored as unknown as StoredSyncAction);
  return stored;
}

/** Read all queued actions with a given status. */
export async function getActionsByStatus(
  status: SyncStatus,
  scope?: string | null,
): Promise<StoredSyncAction[]> {
  const db = await getDb();
  const actions = await db.getAllFromIndex('sync_queue', 'by_status', status);
  return scope ? actions.filter((action) => action.sync_scope === scope) : actions;
}

/**
 * Return pre-scoping queue items whose owner cannot be established safely.
 * They are quarantined: visible as a device warning, but never assigned to the
 * next account or transmitted under that account's credentials.
 */
export async function getLegacyUnscopedActions(
  status: SyncStatus = 'unsynced',
): Promise<StoredSyncAction[]> {
  const db = await getDb();
  const actions = await db.getAllFromIndex('sync_queue', 'by_status', status);
  return actions.filter((action) => !action.sync_scope);
}

/** Read the unsynced actions ordered by `created_at` then `client_id` (Req 5.1). */
export async function getUnsyncedActions(scope?: string | null): Promise<StoredSyncAction[]> {
  const actions = await getActionsByStatus('unsynced', scope);
  return actions.sort(compareByCreatedAt);
}

/** Count the current Unsynced_Items (drives the badge, Req 6.1). */
export async function countUnsynced(scope?: string | null): Promise<number> {
  if (scope) return (await getActionsByStatus('unsynced', scope)).length;
  const db = await getDb();
  return db.countFromIndex('sync_queue', 'by_status', 'unsynced');
}

/** Fetch a single queued action by its `client_id`. */
export async function getAction(clientId: string): Promise<StoredSyncAction | undefined> {
  const db = await getDb();
  return db.get('sync_queue', clientId);
}

/** All queued actions (any status), for the Entitlement_Calculator etc. */
export async function getAllActions(): Promise<StoredSyncAction[]> {
  const db = await getDb();
  return db.getAll('sync_queue');
}

/** Look up queued actions for a player via the `by_player` index (Req 8.2). */
export async function getActionsByPlayer(playerId: string): Promise<StoredSyncAction[]> {
  const db = await getDb();
  return db.getAllFromIndex('sync_queue', 'by_player', playerId);
}

/** Persist a full stored action (e.g. after incrementing `attempt_count`, Req 5.5). */
export async function putAction(action: StoredSyncAction): Promise<void> {
  const db = await getDb();
  await db.put('sync_queue', action);
}

/**
 * Update a queued action's status and (optionally) rejection reason (Req 5.3, 5.6).
 * No-op if the action is not present.
 */
export async function updateActionStatus(
  clientId: string,
  status: SyncStatus,
  reason?: string,
): Promise<void> {
  const db = await getDb();
  const existing = await db.get('sync_queue', clientId);
  if (!existing) return;
  existing.status = status;
  if (reason !== undefined) existing.reason = reason;
  await db.put('sync_queue', existing);
}

// ---- Local domain records ----

/** Write (upsert) a local domain record (write-before-confirm, Req 4.1). */
export async function writeLocalRecord(
  store: LocalRecordStore,
  record: LocalRecord,
): Promise<void> {
  const db = await getDb();
  await db.put(store, record);
}

/** Read a single local record by its `local_id`. */
export async function getLocalRecord(
  store: LocalRecordStore,
  localId: string,
): Promise<LocalRecord | undefined> {
  const db = await getDb();
  return db.get(store, localId);
}

/** Read all records from a local store, optionally restricted to one owner. */
export async function getAllLocalRecords(
  store: LocalRecordStore,
  scope?: string | null,
): Promise<LocalRecord[]> {
  const db = await getDb();
  const records = await db.getAll(store);
  return scope ? records.filter((record) => record.sync_scope === scope) : records;
}

/** Read local records via one of the store's indexes (e.g. `by_day`, `by_player`). */
export async function getLocalRecordsByIndex(
  store: LocalRecordStore,
  index: string,
  key: IDBValidKey,
): Promise<LocalRecord[]> {
  const db = await getDb();
  // idb's generic index name typing is per-store; casts are safe for known indexes.
  return db.getAllFromIndex(store, index as never, key as never);
}

// ---- Cached server reads ----

/** Write/overwrite a cached read under `key` with a fresh `cached_at` (Req 9.2, 13.5, 16.3). */
export async function writeCachedRead<T>(
  key: string,
  data: T,
  cachedAt: string = new Date().toISOString(),
): Promise<void> {
  const db = await getDb();
  await db.put('cached_reads', { key, data, cached_at: cachedAt });
}

/** Read a cached read by `key`. */
export async function getCachedRead<T = unknown>(key: string): Promise<CachedRead<T> | undefined> {
  const db = await getDb();
  return (await db.get('cached_reads', key)) as CachedRead<T> | undefined;
}

/** Build an account-scoped key while retaining the legacy key for unscoped callers/tests. */
function balanceCacheKey(playerId: string, scope?: string | null): string {
  return scope ? `${scope}:${playerId}` : playerId;
}

/** Replace the cached entitlement balances for a player (Req 8.5). */
export async function writeBalances(
  playerId: string,
  balances: BalanceOut[],
  cachedAt: string = new Date().toISOString(),
  scope?: string | null,
): Promise<void> {
  const db = await getDb();
  const key = balanceCacheKey(playerId, scope);
  await db.put('entitlement_balances', { player_id: key, balances, cached_at: cachedAt });
}

/** Read the cached entitlement balances for a player. */
export async function getBalances(
  playerId: string,
  scope?: string | null,
): Promise<CachedBalances | undefined> {
  const db = await getDb();
  return db.get('entitlement_balances', balanceCacheKey(playerId, scope));
}

// ---- Meta ----

/** Read a `meta` value by key. */
export async function getMeta<T = unknown>(key: string): Promise<T | undefined> {
  const db = await getDb();
  const entry = await db.get('meta', key);
  return entry ? (entry.value as T) : undefined;
}

/** Write a `meta` value by key. */
export async function setMeta(key: string, value: unknown): Promise<void> {
  const db = await getDb();
  await db.put('meta', { key, value });
}

/** Return the device_id, generating and persisting one once per install (Req 4.3). */
export async function getOrCreateDeviceId(): Promise<string> {
  const existing = await getMeta<string>('device_id');
  if (existing) return existing;
  const id = newClientId();
  await setMeta('device_id', id);
  return id;
}

function lastSuccessfulSyncKey(scope?: string | null): string {
  return scope ? `last_successful_sync:${scope}` : 'last_successful_sync';
}

/** Read the last successful sync timestamp (drives the >5-day stale warning, Req 6.4). */
export async function getLastSuccessfulSync(scope?: string | null): Promise<string | null> {
  return (await getMeta<string>(lastSuccessfulSyncKey(scope))) ?? null;
}

/** Record the last successful sync timestamp (Req 6.4 basis). */
export async function setLastSuccessfulSync(
  timestamp: string,
  scope?: string | null,
): Promise<void> {
  await setMeta(lastSuccessfulSyncKey(scope), timestamp);
}
