"""Sync action -> Phase 0 Load mapping (Req 4.6).

Every :class:`~funhouse_api.sync.service.SyncAction` targets a known entity, and
this module is the single place that describes, per entity:

* its **idempotency key kind** -- ``dedup_key`` (players), ``natural_key``
  (sessions/attendance/payments), or ``client_id`` (consents/entitlements);
* the **table** the write lands in and the **column** that stores the key on the
  row (used by the Sync_Service to look up an already-applied action and to
  resolve last-write-wins conflicts); and
* the **reused Phase 0 Load path** the Sync_Service delegates the actual write to
  (documented on each mapping; the service imports the callables directly).

The guiding principle (design: "reuse, not reimplementation") is that the
Sync_Service is *orchestration only*: it computes the deterministic idempotency
key here and hands the write to the reused Phase 0 logic
(:func:`funhouse_pipeline.load.dedup.resolve_players`,
:func:`funhouse_pipeline.load.consent.append_consent`,
:func:`funhouse_api.entitlements.engine.create_entitlement` /
:func:`~funhouse_api.entitlements.engine.draw`, and controlled inserts that carry
the same ``natural_key`` / ``append_sync_log`` semantics as
:mod:`funhouse_pipeline.load.loader`).

Idempotency-key construction reuses the Phase 0 rules:

* **players** -> :func:`funhouse_pipeline.load.dedup.compute_dedup_key` over the
  ``first_name``/``last_name``/``birth_date`` payload (Req 6.5, 4.6).
* **sessions / attendance / payments** -> :func:`compute_sync_natural_key`, a
  deterministic SHA-256 over the row's identifying domain fields (the same
  ``"<table>:<digest>"`` shape as
  :func:`funhouse_pipeline.load.loader.compute_natural_key`, Req 4.6). The
  identifying fields are the ones that make two device edits *of the same
  logical record* collide on one key, so re-sends are idempotent and genuine
  edits are resolved by last-write-wins.
* **consents / entitlements** -> the device-supplied ``client_id`` of the action
  itself (Req 4.2); the value is persisted on the row's ``client_id`` column so a
  re-sent action is detected as already applied.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping

# --------------------------------------------------------------------------- #
# Entity types (mirrors the design's EntityType) -- Req 4.6
# --------------------------------------------------------------------------- #

ENTITY_PLAYER = "player"
ENTITY_CONSENT = "consent"
ENTITY_SESSION = "session"
ENTITY_ATTENDANCE = "attendance"
ENTITY_PAYMENT = "payment"
ENTITY_ENTITLEMENT = "entitlement"

VALID_ENTITIES: frozenset[str] = frozenset(
    {
        ENTITY_PLAYER,
        ENTITY_CONSENT,
        ENTITY_SESSION,
        ENTITY_ATTENDANCE,
        ENTITY_PAYMENT,
        ENTITY_ENTITLEMENT,
    }
)

# Idempotency-key kinds.
KEY_DEDUP = "dedup_key"
KEY_NATURAL = "natural_key"
KEY_CLIENT = "client_id"


@dataclass(frozen=True)
class EntityMapping:
    """Static description of how an entity is keyed and where it is written.

    Attributes:
        entity: The sync entity name (one of the ``ENTITY_*`` constants).
        table: The physical table the write lands in.
        key_kind: The idempotency-key kind (``dedup_key`` / ``natural_key`` /
            ``client_id``).
        key_column: The column on ``table`` that stores the idempotency-key value
            for lookup (``dedup_key`` / ``natural_key`` / ``client_id``).
        reused_path: Human-readable note of the reused Phase 0 Load logic the
            Sync_Service delegates the write to (documentation / traceability).
    """

    entity: str
    table: str
    key_kind: str
    key_column: str
    reused_path: str


#: The entity -> mapping registry (Req 4.6, design "Sync action -> Load mapping").
MAPPINGS: dict[str, EntityMapping] = {
    ENTITY_PLAYER: EntityMapping(
        entity=ENTITY_PLAYER,
        table="players",
        key_kind=KEY_DEDUP,
        key_column="dedup_key",
        reused_path="dedup.compute_dedup_key + dedup.resolve_players",
    ),
    ENTITY_CONSENT: EntityMapping(
        entity=ENTITY_CONSENT,
        table="consents",
        key_kind=KEY_CLIENT,
        key_column="client_id",
        reused_path="consent.append_consent (append-only)",
    ),
    ENTITY_SESSION: EntityMapping(
        entity=ENTITY_SESSION,
        table="sessions",
        key_kind=KEY_NATURAL,
        key_column="natural_key",
        reused_path="loader natural-key insert path + audit.append_sync_log",
    ),
    ENTITY_ATTENDANCE: EntityMapping(
        entity=ENTITY_ATTENDANCE,
        table="attendance",
        key_kind=KEY_NATURAL,
        key_column="natural_key",
        reused_path="loader natural-key insert path + audit.append_sync_log",
    ),
    ENTITY_PAYMENT: EntityMapping(
        entity=ENTITY_PAYMENT,
        table="payments",
        key_kind=KEY_NATURAL,
        key_column="natural_key",
        reused_path="loader natural-key insert path (amount->cents, product FK) + audit",
    ),
    ENTITY_ENTITLEMENT: EntityMapping(
        entity=ENTITY_ENTITLEMENT,
        table="entitlements",
        key_kind=KEY_CLIENT,
        key_column="client_id",
        reused_path="Entitlement_Engine.create_entitlement / draw",
    ),
}


#: Identifying domain fields per natural-key entity. These are the fields that
#: make two device edits of the *same logical record* hash to one key (so a
#: re-send is idempotent and a genuine edit is a last-write-wins conflict on the
#: same key). Mutable/value fields (e.g. ``duration_minutes``) are deliberately
#: excluded so editing them does not fork the identity.
_NATURAL_KEY_FIELDS: Mapping[str, tuple[str, ...]] = {
    ENTITY_SESSION: ("player_id", "session_type", "started_at", "ended_at"),
    ENTITY_ATTENDANCE: ("player_id", "attendance_date", "session_id"),
    ENTITY_PAYMENT: ("player_id", "product_id", "amount_cents", "paid_at"),
}


def _norm_field(value: Any) -> str:
    """Normalize a value for natural-key composition (stable across runs).

    Mirrors :func:`funhouse_pipeline.load.loader._norm_field`: lower-cased,
    trimmed, internal whitespace collapsed; ``None`` becomes the empty string.
    """
    if value is None:
        return ""
    return " ".join(str(value).strip().lower().split())


def compute_sync_natural_key(entity: str, payload: Mapping[str, Any]) -> str:
    """Deterministic ``natural_key`` for a natural-key sync entity (Req 4.6).

    A stable SHA-256 over the table name, the entity's identifying domain fields
    (in a fixed order), and a constant ``"sync"`` provenance token, formatted as
    ``"<table>:<hexdigest>"`` -- the same shape produced by
    :func:`funhouse_pipeline.load.loader.compute_natural_key`, so the API reuses
    the Phase 0 natural-key idempotency rule rather than inventing a new one.

    The same identifying fields always hash to the same key regardless of which
    device submitted the action, so re-sending an action is a no-op via
    ``ON CONFLICT (natural_key) DO NOTHING`` and two edits of the same record
    collide on one key for last-write-wins resolution.
    """
    mapping = MAPPINGS[entity]
    fields = _NATURAL_KEY_FIELDS[entity]
    parts = [mapping.table]
    parts.extend(_norm_field(payload.get(f)) for f in fields)
    parts.append("sync")
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"{mapping.table}:{digest}"
