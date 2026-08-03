"""Sync_Service: batch sync with idempotency and last-write-wins (Req 4, 5).

The Sync_Service applies a :class:`SyncBatch` action-by-action with strict
per-action isolation, reusing the Phase 0 Load logic for every write (see
:mod:`funhouse_api.sync.mapping`). For each action, in order (design "Batch sync
sequence"):

1. **Scope check** -- :meth:`Scope.assert_can_write`; a cross-scope target is
   ``rejected`` and never persisted (Req 4.7, Property 6).
2. **POPIA filter** -- ``popia.filter_payload`` strips prohibited fields before
   anything reaches the database (Req 14.1, Property 22).
3. **Idempotency lookup** -- resolve the entity's key (``dedup_key`` /
   ``natural_key`` / ``client_id``) and look for an already-applied row; a
   re-sent action is a no-op ``skipped`` with no duplicate row (Req 4.2,
   Property 8).
4. **Last-write-wins** -- when a row already exists, the device-origin
   ``created_at`` (stored on ``client_timestamp``) decides the winner, with the
   action ``client_id`` as a deterministic tie-break; an older/duplicate action
   is ``skipped`` and the stored values are preserved (Req 5.1, 5.2, 5.4,
   Property 10).
5. **Apply + audit atomically** -- the write and its ``append_sync_log`` entry
   share one transaction, committed only after both succeed, so a write is never
   persisted without its audit entry (Req 4.4, 14.6, Properties 11, 12). Any
   exception rolls back *only this action* and records ``rejected``; the batch
   continues (Req 4.5, Property 9).

Exactly one :class:`ActionResult` is returned per submitted action (Req 4.1).

Transaction model
-----------------
Each action runs on its own with the connection returned to a clean state
(``rollback``) before it starts. Direct-insert entities (session/attendance/
payment) and the player path perform their write and ``append_sync_log`` and
then ``commit`` only if both succeeded -- so injecting a failing audit append
(Property 12) rolls the whole write back. ``student_metrics`` follows the same
natural-key insert/LWW path (location-scoped only, ``value`` stored as TEXT).
Consents reuse
:func:`funhouse_pipeline.load.consent.append_consent` and entitlements reuse the
:mod:`funhouse_api.entitlements.engine`, both of which keep the write and its
audit entry in one transaction by construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence

from funhouse_api.config import DEFAULT_LOCATION_TIMEZONE
from funhouse_api.entitlements import engine
from funhouse_api.rbac import AuthzError, Scope
from funhouse_api.sync import mapping
from funhouse_pipeline.extract.records import ExtractedRecord
from funhouse_pipeline.load.audit import ACTION_INSERT, ACTION_SKIP, ACTION_UPDATE
from funhouse_pipeline.load.audit import append_sync_log as _append_sync_log
from funhouse_pipeline.load.consent import append_consent
from funhouse_pipeline.load.dedup import compute_dedup_key, resolve_players
from funhouse_pipeline.load.loader import amount_to_cents
from funhouse_pipeline.load.popia import filter_payload

# Bound at module level so tests can monkeypatch it to exercise the audit-failure
# path (Property 12): a failing append rolls the whole write back.
append_sync_log = _append_sync_log

# Per-action result statuses (Req 4.1).
STATUS_APPLIED = "applied"
STATUS_SKIPPED = "skipped"
STATUS_REJECTED = "rejected"

_FACILITATOR_ENTITIES = {
    mapping.ENTITY_SESSION,
    mapping.ENTITY_ATTENDANCE,
    mapping.ENTITY_STUDENT_METRICS,
}


class SyncAuditError(Exception):
    """Raised when a ``sync_log`` append fails, to force a write rollback (Req 14.6)."""


# --------------------------------------------------------------------------- #
# DTOs (mirrored by the router's Pydantic models)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SyncAction:
    """One offline-created write within a batch (design DTOs)."""

    client_id: str
    entity: str
    created_at: datetime
    payload: dict[str, Any]


@dataclass(frozen=True)
class SyncBatch:
    """An ordered queue of actions from one device."""

    actions: list[SyncAction] = field(default_factory=list)


@dataclass(frozen=True)
class ActionResult:
    """The per-action outcome (Req 4.1)."""

    client_id: str
    entity: str
    status: str  # applied | skipped | rejected
    record_id: Any | None = None
    reason: str | None = None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _aware(moment: datetime | None) -> datetime | None:
    """Return ``moment`` as a timezone-aware UTC datetime (or ``None``)."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def _prefers_incoming(
    incoming_ts: datetime | None,
    incoming_client_id: Any,
    stored_ts: datetime | None,
    stored_client_id: Any,
) -> bool:
    """Last-write-wins decision (Req 5.1, 5.2, 5.4).

    Returns ``True`` when the incoming action should overwrite the stored record:
    a strictly-later device-origin ``created_at`` wins; on an exact tie the larger
    ``client_id`` wins deterministically; an equal ``client_id`` (an idempotent
    re-send) does not overwrite. The winner is therefore ``max`` over
    ``(created_at, client_id)``, which is order-independent (Property 10).
    """
    incoming_ts = _aware(incoming_ts)
    stored_ts = _aware(stored_ts)
    if stored_ts is None:
        return incoming_ts is not None
    if incoming_ts is None:
        return False
    if incoming_ts > stored_ts:
        return True
    if incoming_ts < stored_ts:
        return False
    return str(incoming_client_id) > str(stored_client_id)


def _reset_connection(conn: Any) -> None:
    """Return the connection to a clean, transaction-free state before an action."""
    try:
        conn.rollback()
    except Exception:  # pragma: no cover - defensive
        pass


def _resolve_player_scope(conn: Any, player_id: Any) -> tuple[Any, Any]:
    """Return ``(location_id, school_id)`` for a player, or raise ``LookupError``."""
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT location_id, school_id FROM players WHERE id = %s", (player_id,)
        )
        row = cursor.fetchone()
    if row is None:
        raise LookupError(f"player {player_id} not found")
    return row[0], row[1]


def _resolve_entitlement_scope(conn: Any, entitlement_id: Any) -> tuple[Any, Any]:
    """Return ``(location_id, player_school_id)`` for an entitlement, or raise."""
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT e.location_id, p.school_id
            FROM entitlements e JOIN players p ON p.id = e.player_id
            WHERE e.id = %s
            """,
            (entitlement_id,),
        )
        row = cursor.fetchone()
    if row is None:
        raise LookupError(f"entitlement {entitlement_id} not found")
    return row[0], row[1]


def _resolve_session_scope(conn: Any, session_id: Any) -> tuple[Any, Any, Any]:
    """Return ``(location_id, school_id, player_id)`` for a session, or raise."""
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT location_id, school_id, player_id FROM sessions WHERE id = %s",
            (session_id,),
        )
        row = cursor.fetchone()
    if row is None:
        raise LookupError(f"session {session_id} not found")
    return row[0], row[1], row[2]


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def apply_batch(
    conn: Any,
    scope: Scope,
    actions: Sequence[SyncAction],
    *,
    logged_by: Any,
    location_timezone: str = DEFAULT_LOCATION_TIMEZONE,
) -> list[ActionResult]:
    """Apply every action in the batch with per-action isolation (Req 4).

    Returns exactly one :class:`ActionResult` per submitted action (Req 4.1). A
    failing action is isolated (rolled back) and reported ``rejected`` while the
    remaining actions still apply (Req 4.5, Property 9).
    """
    results: list[ActionResult] = []
    for action in actions:
        _reset_connection(conn)
        try:
            result = _apply_action(
                conn, scope, action, logged_by=logged_by,
                location_timezone=location_timezone,
            )
        except AuthzError as exc:
            _reset_connection(conn)
            result = ActionResult(
                action.client_id, action.entity, STATUS_REJECTED,
                reason=f"out_of_scope: {exc}",
            )
        except Exception as exc:  # noqa: BLE001 - per-action failure isolation (Req 4.5)
            _reset_connection(conn)
            result = ActionResult(
                action.client_id, action.entity, STATUS_REJECTED, reason=str(exc)
            )
        results.append(result)
    return results


def _apply_action(
    conn: Any,
    scope: Scope,
    action: SyncAction,
    *,
    logged_by: Any,
    location_timezone: str,
) -> ActionResult:
    """Dispatch a single action to its entity handler (design mapping)."""
    entity = action.entity
    if scope.role == "facilitator" and entity not in _FACILITATOR_ENTITIES:
        return ActionResult(
            action.client_id, entity, STATUS_REJECTED, reason="forbidden_entity"
        )
    if entity not in mapping.VALID_ENTITIES:
        return ActionResult(
            action.client_id, entity, STATUS_REJECTED, reason="unknown_entity"
        )

    # POPIA filter first so prohibited fields never reach any code path (Req 14.1).
    clean, _dropped = filter_payload(action.payload or {})
    created_at = _aware(action.created_at)

    if (
        scope.role == "facilitator"
        and entity == mapping.ENTITY_SESSION
        and clean.get("session_type") not in {"lesson", "kit", "esports"}
    ):
        return ActionResult(
            action.client_id, entity, STATUS_REJECTED, reason="forbidden_session_type"
        )

    if entity == mapping.ENTITY_PLAYER:
        return _apply_player(conn, scope, action, clean, created_at, logged_by=logged_by)
    if entity == mapping.ENTITY_CONSENT:
        return _apply_consent(conn, scope, action, clean, created_at, logged_by=logged_by)
    if entity == mapping.ENTITY_ENTITLEMENT:
        return _apply_entitlement(
            conn, scope, action, clean, created_at,
            logged_by=logged_by, location_timezone=location_timezone,
        )
    # session / attendance / payment / student_metrics -> natural-key insert-or-LWW.
    return _apply_natural_key(
        conn, scope, action, clean, created_at, entity=entity, logged_by=logged_by
    )


# --------------------------------------------------------------------------- #
# Player (dedup_key) -- reuse dedup.resolve_players
# --------------------------------------------------------------------------- #


def _apply_player(
    conn: Any,
    scope: Scope,
    action: SyncAction,
    payload: dict[str, Any],
    created_at: datetime | None,
    *,
    logged_by: Any,
) -> ActionResult:
    """Register (or resolve) a player via the reused dedup layer (Req 6.5, 4.2)."""
    # Scope: a caller-supplied location/school outside scope is a cross-scope
    # write and is rejected (Req 4.7); otherwise the row is stamped to scope.
    target_loc = payload.get("location_id")
    target_school = payload.get("school_id")
    if target_loc is not None:
        scope.assert_can_write(target_loc, target_school)  # AuthzError -> rejected

    stamped: dict[str, Any] = {"location_id": target_loc, "school_id": target_school}
    scope.stamp(stamped)
    effective_loc = stamped.get("location_id")
    effective_school = stamped.get("school_id")
    if effective_loc is None:
        return ActionResult(
            action.client_id, action.entity, STATUS_REJECTED,
            reason="location_required",
        )

    record = ExtractedRecord(
        record_id=action.client_id,
        target_table="players",
        payload={
            "first_name": payload.get("first_name"),
            "last_name": payload.get("last_name"),
            "birth_date": payload.get("birth_date"),
            "grade": payload.get("grade"),
        },
        confidence_score=1.0,
        source_file="api:sync",
        provider="api",
        extracted_at=created_at or datetime.now(timezone.utc),
    )

    resolution = resolve_players([record], conn, location_id=effective_loc)
    player_id = resolution.resolved.get(action.client_id)
    if player_id is None:
        # Nameless or ambiguous per the dedup layer -> cannot resolve to a row.
        conn.rollback()
        return ActionResult(
            action.client_id, action.entity, STATUS_REJECTED,
            reason="unresolved_player",
        )

    newly_created = player_id in resolution.created
    with conn.cursor() as cursor:
        if effective_school is not None:
            cursor.execute(
                "UPDATE players SET school_id = COALESCE(school_id, %s) WHERE id = %s",
                (effective_school, player_id),
            )
        append_sync_log(
            cursor,
            entity="players",
            record_id=player_id,
            action=ACTION_INSERT if newly_created else ACTION_SKIP,
            location_id=effective_loc,
            user_id=logged_by,
            client_timestamp=created_at,
        )
    conn.commit()

    status = STATUS_APPLIED if newly_created else STATUS_SKIPPED
    return ActionResult(action.client_id, action.entity, status, record_id=player_id)


# --------------------------------------------------------------------------- #
# Consent (client_id) -- reuse consent.append_consent (append-only)
# --------------------------------------------------------------------------- #


def _apply_consent(
    conn: Any,
    scope: Scope,
    action: SyncAction,
    payload: dict[str, Any],
    created_at: datetime | None,
    *,
    logged_by: Any,
) -> ActionResult:
    """Append a consent row via the reused append-only ledger (Req 6.4, 4.2)."""
    player_id = payload.get("player_id")
    if player_id is None:
        return ActionResult(
            action.client_id, action.entity, STATUS_REJECTED, reason="player_required"
        )
    location_id, school_id = _resolve_player_scope(conn, player_id)
    scope.assert_can_write(location_id, school_id)  # AuthzError -> rejected

    consent_type = payload.get("consent_type")
    if not consent_type:
        return ActionResult(
            action.client_id, action.entity, STATUS_REJECTED,
            reason="consent_type_required",
        )

    # Idempotency by the action's client_id (Req 4.2): a re-send is a no-op.
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT id FROM consents WHERE client_id = %s LIMIT 1", (action.client_id,)
        )
        existing = cursor.fetchone()
    if existing is not None:
        with conn.cursor() as cursor:
            append_sync_log(
                cursor, entity="consents", record_id=existing[0], action=ACTION_SKIP,
                location_id=location_id, user_id=logged_by, client_timestamp=created_at,
            )
        conn.commit()
        return ActionResult(
            action.client_id, action.entity, STATUS_SKIPPED, record_id=existing[0]
        )

    granted = payload.get("granted", True)
    consent_id = append_consent(
        conn,
        player_id=player_id,
        consent_type=str(consent_type),
        granted=bool(granted),
        location_id=location_id,
        granted_at=payload.get("granted_at"),
        method=payload.get("method"),
        captured_by_user_id=logged_by,
        client_timestamp=created_at,
        client_id=action.client_id,
    )
    # append_consent keeps its write + audit in one (possibly nested) transaction
    # but does not commit when a prior read already opened the transaction; commit
    # explicitly so the applied consent is durable before the next action's reset.
    conn.commit()
    return ActionResult(
        action.client_id, action.entity, STATUS_APPLIED, record_id=consent_id
    )


# --------------------------------------------------------------------------- #
# Entitlement (client_id) -- reuse Entitlement_Engine.create / draw
# --------------------------------------------------------------------------- #


def _apply_entitlement(
    conn: Any,
    scope: Scope,
    action: SyncAction,
    payload: dict[str, Any],
    created_at: datetime | None,
    *,
    logged_by: Any,
    location_timezone: str,
) -> ActionResult:
    """Create an entitlement, or draw against one, via the Engine (Req 8, 4.2)."""
    now = created_at or datetime.now(timezone.utc)

    # A draw is signalled by an entitlement_id + amount; otherwise it is a create.
    entitlement_id = payload.get("entitlement_id")
    if entitlement_id is not None and payload.get("amount") is not None:
        location_id, school_id = _resolve_entitlement_scope(conn, entitlement_id)
        scope.assert_can_write(location_id, school_id)  # AuthzError -> rejected

        # Idempotency: a draw records a sync_log 'update' entry whose
        # client_timestamp is the device-origin created_at; if one already exists
        # for this entitlement + created_at the draw was applied -> no-op skip.
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM sync_log WHERE entity = 'entitlements' "
                "AND record_id = %s AND action = 'update' AND client_timestamp = %s",
                (entitlement_id, created_at),
            )
            already = cursor.fetchone()[0]
        if already:
            with conn.cursor() as cursor:
                append_sync_log(
                    cursor, entity="entitlements", record_id=entitlement_id,
                    action=ACTION_SKIP, location_id=location_id, user_id=logged_by,
                    client_timestamp=created_at,
                )
            conn.commit()
            return ActionResult(
                action.client_id, action.entity, STATUS_SKIPPED,
                record_id=entitlement_id,
            )

        result = engine.draw(
            conn, entitlement_id, int(payload["amount"]), logged_by=logged_by,
            now=now, location_timezone=location_timezone,
        )
        if result.applied:
            return ActionResult(
                action.client_id, action.entity, STATUS_APPLIED,
                record_id=entitlement_id,
            )
        return ActionResult(
            action.client_id, action.entity, STATUS_REJECTED,
            record_id=entitlement_id, reason=result.reason,
        )

    # Create path.
    player_id = payload.get("player_id")
    product_id = payload.get("product_id")
    if player_id is None or product_id is None:
        return ActionResult(
            action.client_id, action.entity, STATUS_REJECTED,
            reason="player_and_product_required",
        )
    location_id, school_id = _resolve_player_scope(conn, player_id)
    scope.assert_can_write(location_id, school_id)  # AuthzError -> rejected

    # Idempotency by client_id: a re-sent create is a no-op skip (Req 4.2).
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT id FROM entitlements WHERE client_id = %s LIMIT 1",
            (action.client_id,),
        )
        existing = cursor.fetchone()
    if existing is not None:
        with conn.cursor() as cursor:
            append_sync_log(
                cursor, entity="entitlements", record_id=existing[0], action=ACTION_SKIP,
                location_id=location_id, user_id=logged_by, client_timestamp=created_at,
            )
        conn.commit()
        return ActionResult(
            action.client_id, action.entity, STATUS_SKIPPED, record_id=existing[0]
        )

    created = engine.create_entitlement(
        conn, player_id=player_id, product_id=product_id, location_id=location_id,
        logged_by=logged_by, now=now, location_timezone=location_timezone,
        client_id=action.client_id,
    )
    return ActionResult(
        action.client_id, action.entity, STATUS_APPLIED, record_id=created.id
    )


# --------------------------------------------------------------------------- #
# Session / attendance / payment (natural_key) -- controlled insert or LWW
# --------------------------------------------------------------------------- #

# Columns inserted per natural-key entity (identity + value + sync metadata).
_INSERT_COLUMNS: dict[str, tuple[str, ...]] = {
    mapping.ENTITY_SESSION: (
        "player_id", "session_type", "started_at", "ended_at", "duration_minutes",
        "school_id", "logged_by",
    ),
    mapping.ENTITY_ATTENDANCE: (
        "session_id", "player_id", "attendance_date", "present", "school_id",
        "logged_by",
    ),
    mapping.ENTITY_PAYMENT: (
        "player_id", "product_id", "amount_cents", "method", "paid_at", "logged_by",
    ),
    # student_metrics is LOCATION-scoped only (no school_id column); value is TEXT.
    mapping.ENTITY_STUDENT_METRICS: (
        "player_id", "lesson_id", "metric_type", "value", "measured_at", "logged_by",
    ),
}

# Non-identity value columns updated when an incoming action wins LWW (Req 5.1).
_MUTABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    mapping.ENTITY_SESSION: ("duration_minutes",),
    mapping.ENTITY_ATTENDANCE: ("present",),
    mapping.ENTITY_PAYMENT: ("method",),
    # The measured value is the mutable field; a later edit overwrites it (LWW).
    mapping.ENTITY_STUDENT_METRICS: ("value",),
}


def _normalize_natural_payload(entity: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize a natural-key payload before keying/inserting.

    Reuses :func:`funhouse_pipeline.load.loader.amount_to_cents` for payments so a
    device-supplied ``amount`` (e.g. ``"R30"``) becomes integer cents; an
    explicit ``amount_cents`` is used as-is (Req 10.1).
    """
    normalized = dict(payload)
    if entity == mapping.ENTITY_PAYMENT:
        if normalized.get("amount_cents") is None:
            normalized["amount_cents"] = amount_to_cents(normalized.get("amount"))
    if entity == mapping.ENTITY_STUDENT_METRICS:
        # The column is TEXT (holds numeric metrics and free-text observations);
        # coerce any device-supplied value to a string so it stores cleanly.
        if normalized.get("value") is not None:
            normalized["value"] = str(normalized["value"])
    return normalized


def _apply_natural_key(
    conn: Any,
    scope: Scope,
    action: SyncAction,
    payload: dict[str, Any],
    created_at: datetime | None,
    *,
    entity: str,
    logged_by: Any,
) -> ActionResult:
    """Insert a session/attendance/payment, or resolve an LWW conflict (Req 4, 5)."""
    emap = mapping.MAPPINGS[entity]
    normalized = _normalize_natural_payload(entity, payload)

    player_id = normalized.get("player_id")
    if player_id is None:
        return ActionResult(
            action.client_id, entity, STATUS_REJECTED, reason="player_required"
        )
    location_id, player_school = _resolve_player_scope(conn, player_id)
    scope.assert_can_write(location_id, player_school)  # AuthzError -> rejected

    if entity == mapping.ENTITY_ATTENDANCE:
        session_id = normalized.get("session_id")
        if scope.role == "facilitator" and session_id is None:
            return ActionResult(
                action.client_id,
                entity,
                STATUS_REJECTED,
                reason="session_required",
            )
        if session_id is not None:
            session_location, session_school, session_player = _resolve_session_scope(
                conn, session_id
            )
            scope.assert_can_write(session_location, session_school)
            if str(session_player) != str(player_id):
                return ActionResult(
                    action.client_id,
                    entity,
                    STATUS_REJECTED,
                    reason="session_player_mismatch",
                )

    if entity == mapping.ENTITY_PAYMENT and normalized.get("amount_cents") is None:
        return ActionResult(
            action.client_id, entity, STATUS_REJECTED, reason="bad_amount"
        )

    if entity == mapping.ENTITY_STUDENT_METRICS:
        # Respect the Phase 0 metric_type CHECK and NOT-NULL value up front, so an
        # invalid or incomplete metric is a clean isolated rejection (Req 4.8, 4.5)
        # rather than a mid-transaction constraint error.
        if normalized.get("metric_type") not in mapping.VALID_METRIC_TYPES:
            return ActionResult(
                action.client_id, entity, STATUS_REJECTED, reason="invalid_metric_type"
            )
        if normalized.get("value") is None:
            return ActionResult(
                action.client_id, entity, STATUS_REJECTED, reason="value_required"
            )

    # A session/attendance inherits the player's school when none supplied.
    # Validate the effective value before keying or persistence so a facilitator
    # cannot authorize via the player and then supply a different school_id.
    effective_school = normalized.get("school_id")
    if effective_school is None and entity in (
        mapping.ENTITY_SESSION, mapping.ENTITY_ATTENDANCE
    ):
        effective_school = player_school
    if entity in (mapping.ENTITY_SESSION, mapping.ENTITY_ATTENDANCE):
        scope.assert_can_write(location_id, effective_school)
    normalized["school_id"] = effective_school
    normalized["logged_by"] = logged_by

    natural_key = mapping.compute_sync_natural_key(entity, normalized)

    with conn.cursor() as cursor:
        cursor.execute(
            f"SELECT id, client_timestamp, client_id FROM {emap.table} "
            f"WHERE natural_key = %s",
            (natural_key,),
        )
        existing = cursor.fetchone()

    if existing is None:
        return _insert_natural(
            conn, action, entity, normalized, created_at,
            natural_key=natural_key, location_id=location_id, logged_by=logged_by,
        )

    existing_id, stored_ts, stored_client_id = existing
    if _prefers_incoming(created_at, action.client_id, stored_ts, stored_client_id):
        return _update_natural(
            conn, action, entity, normalized, created_at,
            record_id=existing_id, location_id=location_id, logged_by=logged_by,
        )

    # Older / duplicate action: preserve stored values, record a skip (Req 5.2).
    with conn.cursor() as cursor:
        append_sync_log(
            cursor, entity=emap.table, record_id=existing_id, action=ACTION_SKIP,
            location_id=location_id, user_id=logged_by, client_timestamp=created_at,
        )
    conn.commit()
    return ActionResult(action.client_id, entity, STATUS_SKIPPED, record_id=existing_id)


def _insert_natural(
    conn: Any,
    action: SyncAction,
    entity: str,
    payload: dict[str, Any],
    created_at: datetime | None,
    *,
    natural_key: str,
    location_id: Any,
    logged_by: Any,
) -> ActionResult:
    """Insert a new natural-key row and audit it in one transaction (Req 4.4, 14.6)."""
    emap = mapping.MAPPINGS[entity]
    # Only include entity columns that carry a value, so NOT-NULL columns with a
    # DB default (e.g. attendance.present) fall back to their default when the
    # device omitted them.
    entity_cols = [c for c in _INSERT_COLUMNS[entity] if payload.get(c) is not None]
    columns = entity_cols + [
        "location_id", "natural_key", "client_id", "device_id", "client_timestamp"
    ]
    values: list[Any] = [payload.get(c) for c in entity_cols] + [
        location_id, natural_key, action.client_id,
        payload.get("device_id"), created_at,
    ]
    placeholders = ", ".join(["%s"] * len(columns))

    with conn.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {emap.table} ({', '.join(columns)}) "
            f"VALUES ({placeholders}) "
            "ON CONFLICT (natural_key) DO NOTHING RETURNING id",
            values,
        )
        row = cursor.fetchone()
        if row is None:
            # Concurrent insert of the same key: treat as an idempotent skip.
            cursor.execute(
                f"SELECT id FROM {emap.table} WHERE natural_key = %s", (natural_key,)
            )
            existing = cursor.fetchone()
            record_id = existing[0] if existing else None
            append_sync_log(
                cursor, entity=emap.table, record_id=record_id, action=ACTION_SKIP,
                location_id=location_id, user_id=logged_by, client_timestamp=created_at,
            )
            conn.commit()
            return ActionResult(
                action.client_id, entity, STATUS_SKIPPED, record_id=record_id
            )
        record_id = row[0]
        # Audit shares this transaction; a failing append aborts the insert too.
        append_sync_log(
            cursor, entity=emap.table, record_id=record_id, action=ACTION_INSERT,
            location_id=location_id, user_id=logged_by, client_timestamp=created_at,
        )
    conn.commit()
    return ActionResult(action.client_id, entity, STATUS_APPLIED, record_id=record_id)


def _update_natural(
    conn: Any,
    action: SyncAction,
    entity: str,
    payload: dict[str, Any],
    created_at: datetime | None,
    *,
    record_id: Any,
    location_id: Any,
    logged_by: Any,
) -> ActionResult:
    """Apply the LWW winner's value fields + audit in one transaction (Req 5.1)."""
    emap = mapping.MAPPINGS[entity]
    mutable = _MUTABLE_COLUMNS[entity]
    set_cols = list(mutable) + ["client_timestamp", "client_id"]
    set_values: list[Any] = [payload.get(c) for c in mutable] + [
        created_at, action.client_id
    ]
    assignments = ", ".join(f"{c} = %s" for c in set_cols) + ", updated_at = now()"

    with conn.cursor() as cursor:
        cursor.execute(
            f"UPDATE {emap.table} SET {assignments} WHERE id = %s",
            [*set_values, record_id],
        )
        append_sync_log(
            cursor, entity=emap.table, record_id=record_id, action=ACTION_UPDATE,
            location_id=location_id, user_id=logged_by, client_timestamp=created_at,
        )
    conn.commit()
    return ActionResult(action.client_id, entity, STATUS_APPLIED, record_id=record_id)
