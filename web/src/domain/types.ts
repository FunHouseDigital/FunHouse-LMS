/**
 * Domain TypeScript types for the Revenue PWA.
 *
 * These types bind exactly to the confirmed Spec 2 Container_API contract as
 * documented in design.md ("Data Models" and "Confirmed Container_API
 * contract"). They are the shared vocabulary used by the Local_Store, the
 * Sync_Engine, the capture builders, and the read views.
 *
 * Requirements: 4.2 (Sync_Action shape), 5.3 (POST /sync result reconcile),
 * 8.2 (integer-minute entitlement balances).
 */

// ---- Sync primitives (match sync/router.py SyncActionModel / SyncResultModel) ----

/**
 * Entities the client can produce actions for.
 *
 * `student_metrics` is now a live `POST /sync` entity (Dependency D1 resolved,
 * Container API PR #3): it is a natural-key entity keyed on
 * `player_id`/`metric_type`/`measured_at`. Metrics enqueue as normal `unsynced`
 * actions and are included in flush batches like the other capture entities.
 */
export type EntityType =
  | 'player'
  | 'consent'
  | 'session'
  | 'attendance'
  | 'payment'
  | 'entitlement'
  | 'student_metrics';

/**
 * Local status metadata for a queued action.
 * - `unsynced`: awaiting transmission (an Unsynced_Item).
 * - `applied` / `skipped`: terminal success results from the server.
 * - `rejected`: server rejected the action; retained locally with a reason.
 * - `blocked`: general forward-compatibility mechanism — locally held and
 *   excluded from batches until an entity is accepted by the API. No capture
 *   currently routes into it (metrics now sync live once D1 was resolved), but
 *   the mechanism is retained for any future not-yet-accepted entity.
 */
export type SyncStatus = 'unsynced' | 'applied' | 'skipped' | 'rejected' | 'blocked';

/** The wire shape sent to `POST /sync` (matches the API `SyncActionModel`). */
export interface SyncAction<P = Record<string, unknown>> {
  /** crypto.randomUUID(); unique per device (Req 4.3). */
  client_id: string;
  entity: EntityType;
  /** ISO timestamp; device time of capture, preserved across retries (Req 5.7). */
  created_at: string;
  payload: P;
}

/** A `SyncAction` as persisted in the Local_Store `sync_queue`. */
export interface StoredSyncAction<P = Record<string, unknown>> extends SyncAction<P> {
  /** Authenticated account/location/school scope that owns this offline action. */
  sync_scope?: string;
  status: SyncStatus;
  /** Set when `status === 'rejected'` (Req 5.6). */
  reason?: string;
  /** Mirror of `payload.player_id` for the `by_player` index (Req 8.2). */
  player_id?: string;
  attempt_count: number;
}

/** A single per-action result from the `POST /sync` response. */
export interface ActionResult {
  client_id: string;
  entity: string;
  status: 'applied' | 'skipped' | 'rejected';
  record_id: string | null;
  reason: string | null;
}

/** The full `POST /sync` response body. */
export interface SyncResult {
  results: ActionResult[];
}

// ---- Per-entity payloads (match sync/service.py handlers) ----

export interface PlayerPayload {
  first_name: string;
  last_name?: string;
  birth_date?: string;
  grade?: string;
  location_id?: string;
  school_id?: string;
}

/** The four guardian consent categories captured at registration. */
export type ConsentType = 'media' | 'data_processing' | 'participation' | 'communications';

export interface ConsentPayload {
  player_id: string;
  consent_type: ConsentType;
  granted: boolean;
  granted_at?: string;
  method?: string;
}

export interface SessionPayload {
  player_id: string;
  session_type: 'lounge' | 'lesson' | 'kit' | 'esports';
  started_at: string;
  ended_at: string;
  duration_minutes: number;
  school_id?: string;
  /** kit-module / match-particulars / lesson-reference (Req 14.3). */
  reference?: string;
}

export interface AttendancePayload {
  session_id: string;
  player_id: string;
  attendance_date: string;
  present: boolean;
  school_id?: string;
}

export interface PaymentPayload {
  player_id: string;
  product_id?: string;
  /** Integer cents (the server also accepts a string `amount` such as "R30"). */
  amount_cents: number;
  method?: string;
  paid_at?: string;
}

/** Sell flow: create an entitlement from a purchased product. */
export interface EntitlementCreatePayload {
  player_id: string;
  product_id: string;
}

/** Log Session paying by entitlement: draw `amount` minutes. */
export interface EntitlementDrawPayload {
  entitlement_id: string;
  /** Integer minutes. */
  amount: number;
}

/**
 * `student_metrics` sync payload (Dependency D1 resolved). Natural-key entity
 * keyed server-side on `player_id`/`metric_type`/`measured_at`; the server sets
 * `logged_by`/`location` and stores `value` as TEXT (coercing the numeric
 * value). The student's display name is personal data kept encrypted at rest
 * locally and is deliberately NOT sent on the wire.
 */
export interface StudentMetricsPayload {
  player_id?: string;
  metric_type: 'typing_wpm' | 'typing_accuracy';
  value: number;
  measured_at: string;
}

// ---- Cached server reads (match read routers) ----

/** `GET /players` row. */
export interface PlayerOut {
  id: string;
  first_name: string;
  last_name: string | null;
  birth_date: string | null;
  grade: string | null;
  school_id: string | null;
  location_id: string;
  consent_status: string;
  active: boolean;
}

/** `GET /players/{id}/entitlements` row. */
export interface BalanceOut {
  entitlement_id: string;
  product_id: string;
  /** Integer MINUTES; `null` means unlimited. */
  remaining_units: number | null;
  valid_from: string | null;
  valid_to: string | null;
  status: string;
}

/** One synchronized session returned by player history. */
export type SessionHistoryOut = Record<string, unknown> & {
  id: string;
  session_type: 'lounge' | 'lesson' | 'kit' | 'esports';
  started_at: string | null;
  ended_at: string | null;
  duration_minutes: number | null;
  /** PS5/PS4 or the school-session quick-field reference. */
  reference: string | null;
  school_id: string | null;
  logged_by: string | null;
  location_id: string;
};

/** `GET /players/{id}/history` response. */
export interface PlayerHistory {
  player_id: string;
  sessions: SessionHistoryOut[];
  payments: Record<string, unknown>[];
  entitlement_draws: Record<string, unknown>[];
}

/** `GET /revenue/summary` response (integer cents). */
export interface RevenueSummary {
  pay_per_use_cents: number;
  subscription_cents: number;
  school_contracts_cents: number;
}

/** `GET /alerts` row. */
export interface Alert {
  type: string;
  subject_id: string;
  detail: string;
}

/** `GET /products` row. */
export interface ProductOut {
  id: string;
  name: string;
  type: string;
  price_cents: number;
  rules: Record<string, unknown>;
  location_id: string;
}

/** `POST /auth/login` `200` response. */
export interface LoginResponse {
  access_token: string;
  token_type: 'bearer';
  expires_at: string;
}

// ---- Meta ----

/** Authenticated session stored (encrypted) in `meta.session`. */
export interface Session {
  access_token: string;
  expires_at: string;
  role: 'manager' | 'founder' | 'facilitator';
  location_id: string | null;
}

/** Device/meta values held in the `meta` store. */
export interface Meta {
  device_id: string;
  last_successful_sync: string | null;
  crypto_salt: string;
}

// ---- Encrypted-at-rest envelope for personal data (Req 17.1) ----

/** AES-GCM envelope: both fields are base64-encoded. */
export interface EncryptedField {
  iv: string;
  ciphertext: string;
}
