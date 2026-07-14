"""Append-only consent-ledger writes (Task 12.2, Req 11.1-11.3).

The ``consents`` table is an **append-only ledger** (design § Overview,
principle 3; Req 11): a consent write appends a new row, a *revocation* appends a
**new** row with ``granted = false`` rather than editing an existing one, and no
row is ever deleted or overwritten. The database enforces this at two layers
(migration 002): a trigger rejects ``UPDATE``/``DELETE`` on ``consents`` and the
pipeline role is granted ``INSERT``/``SELECT`` only.

This module's contract is to **never issue an UPDATE or DELETE** against
``consents`` -- it only ever ``INSERT``s. The trigger is the backstop; correct
code never trips it. Every append also writes a ``sync_log`` entry (action
``insert``) in the same transaction, so consent writes are audited exactly like
every other write (Req 14.5).

Public API
----------
* :func:`append_consent` -- append one consent row (grant or revocation) and
  audit it. Returns the new ``consents.id``.
* :func:`revoke_consent` -- convenience wrapper that appends a revocation row
  (``granted = false``); a revocation is *a new appended row*, never an edit
  (Req 11.2).
* :func:`load_consent_records` -- batch entry point for consent
  :class:`~funhouse_pipeline.extract.records.ExtractedRecord`s
  (``target_table == 'consents'``). It resolves each record's ``player_name`` to
  a ``players.id`` and appends a row, flagging any that cannot be resolved.

How it plugs into the pipeline
------------------------------
In Phase 0 the Extract stage produces only the five target tables
(players/sessions/payments/lessons/student_metrics), so no consent records flow
through the record stream and :func:`load_consent_records` is dormant. It exists
as a clean seam: if a future extractor emits ``consents`` records,
:func:`funhouse_pipeline.load.loader.load_clean_records` already routes them
here. Explicit consent capture (e.g. an Operator recording a signed paper form)
calls :func:`append_consent` / :func:`revoke_consent` directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from funhouse_pipeline.extract.records import ExtractedRecord
from funhouse_pipeline.load.audit import ACTION_INSERT, append_sync_log

# Reason codes for consent records that could not be appended automatically.
UNRESOLVED_PLAYER = "UNRESOLVED_PLAYER"  # player_name matched no players row
AMBIGUOUS_PLAYER = "AMBIGUOUS_PLAYER"    # player_name matched >1 players row
MISSING_PLAYER = "MISSING_PLAYER"        # no player identity on the record
MISSING_CONSENT_TYPE = "MISSING_CONSENT_TYPE"  # consent_type is required
APPEND_ERROR = "APPEND_ERROR"            # unexpected DB error during append


@dataclass(frozen=True)
class AppendedConsent:
    """A consent row successfully appended to the ledger."""

    consent_id: Any
    player_id: Any
    consent_type: str
    granted: bool
    record_id: str = ""


@dataclass(frozen=True)
class FlaggedConsent:
    """A consent record withheld from the ledger and routed to review."""

    record_id: str
    reason: str
    detail: str = ""


@dataclass
class ConsentLoadResult:
    """Outcome of appending a batch of consent records."""

    appended: list[AppendedConsent] = field(default_factory=list)
    flagged: list[FlaggedConsent] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"Consent ledger: {len(self.appended)} appended, "
            f"{len(self.flagged)} flagged for review."
        )


def _as_bool(value: Any, *, default: bool = True) -> bool:
    """Interpret a raw ``granted`` value as a boolean (documented default True).

    A consent record with no explicit ``granted`` value is treated as a grant
    (the common case: capturing a signed form); revocations set it explicitly.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"false", "f", "no", "n", "0", "revoked", "revoke", "denied", "deny"}:
        return False
    if text in {"true", "t", "yes", "y", "1", "granted", "grant"}:
        return True
    return default


def append_consent(
    conn: Any,
    *,
    player_id: Any,
    consent_type: str,
    granted: bool,
    location_id: Any,
    granted_at: Any = None,
    guardian_id: Any | None = None,
    method: Any | None = None,
    captured_by_user_id: Any | None = None,
    device_id: Any | None = None,
    client_timestamp: Any | None = None,
) -> Any:
    """Append one row to the append-only consent ledger and audit it (Req 11.1).

    This only ever issues an ``INSERT``; it never updates or deletes an existing
    row (Req 11.3). A revocation is expressed by passing ``granted=False`` (see
    :func:`revoke_consent`), which appends a *new* row (Req 11.2). The insert and
    its ``sync_log`` entry share one transaction so the audit is atomic with the
    write (Req 14.5).

    Args:
        conn: An open DB-API connection whose ``search_path`` points at the
            target schema. The caller owns the connection; this function manages
            one nested transaction and does not close it.
        player_id: The subject ``players.id`` (NOT NULL FK).
        consent_type: E.g. ``data_processing`` or ``photo`` (NOT NULL).
        granted: ``True`` for a grant, ``False`` for a revocation.
        location_id: ``location_id`` (NOT NULL FK to ``locations``).
        granted_at: When consent was granted/revoked. Defaults to ``now()`` at
            the database when omitted.
        guardian_id: Optional ``guardians.id`` who gave/withdrew consent.
        method: Optional capture method (``paper``/``verbal``/``whatsapp``/...).
        captured_by_user_id: Acting user (``users.id``); also the ``sync_log``
            actor.
        device_id: Optional originating device for the audit entry.
        client_timestamp: Optional client-side timestamp for the audit entry.

    Returns:
        The new ``consents.id``.
    """
    # Build the INSERT column list, placeholders, and bound values in lockstep.
    columns: list[str] = ["player_id", "consent_type", "granted", "location_id"]
    placeholders: list[str] = ["%s", "%s", "%s", "%s"]
    values: list[Any] = [player_id, consent_type, granted, location_id]

    # granted_at is NOT NULL; default to the database clock when not supplied.
    columns.append("granted_at")
    if granted_at is not None:
        placeholders.append("%s")
        values.append(granted_at)
    else:
        placeholders.append("now()")  # no bound value for this column

    for col, val in (
        ("guardian_id", guardian_id),
        ("method", method),
        ("captured_by_user_id", captured_by_user_id),
        ("device_id", device_id),
        ("client_timestamp", client_timestamp),
    ):
        if val is not None:
            columns.append(col)
            placeholders.append("%s")
            values.append(val)

    with conn.transaction():
        with conn.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO consents ({', '.join(columns)}) "
                f"VALUES ({', '.join(placeholders)}) RETURNING id",
                values,
            )
            consent_id = cursor.fetchone()[0]
            append_sync_log(
                cursor,
                entity="consents",
                record_id=consent_id,
                action=ACTION_INSERT,
                location_id=location_id,
                user_id=captured_by_user_id,
                device_id=device_id,
                client_timestamp=client_timestamp,
            )
    return consent_id


def revoke_consent(
    conn: Any,
    *,
    player_id: Any,
    consent_type: str,
    location_id: Any,
    granted_at: Any = None,
    guardian_id: Any | None = None,
    method: Any | None = None,
    captured_by_user_id: Any | None = None,
    device_id: Any | None = None,
    client_timestamp: Any | None = None,
) -> Any:
    """Record a revocation as a **newly appended** row (Req 11.2).

    Thin wrapper over :func:`append_consent` with ``granted=False``. It never
    modifies the prior grant row -- the revocation is a new ledger entry, so the
    full consent history is preserved (Req 11.3).
    """
    return append_consent(
        conn,
        player_id=player_id,
        consent_type=consent_type,
        granted=False,
        location_id=location_id,
        granted_at=granted_at,
        guardian_id=guardian_id,
        method=method,
        captured_by_user_id=captured_by_user_id,
        device_id=device_id,
        client_timestamp=client_timestamp,
    )


def load_consent_records(
    records: Iterable[ExtractedRecord],
    conn: Any,
    *,
    location_id: Any,
    players_index: Any | None = None,
    captured_by_user_id: Any | None = None,
    device_id: Any | None = None,
) -> ConsentLoadResult:
    """Append a batch of consent ``ExtractedRecord``s to the ledger.

    Each record's ``player_name`` is resolved to a ``players.id`` (via the shared
    name index the loader builds, or one built here) and a consent row is
    appended. Records whose player cannot be resolved, or that lack a
    ``consent_type``, are flagged for review rather than appended -- consistent
    with the loader's "flag, never guess a FK" rule.

    Args:
        records: Consent records (``target_table == 'consents'``); others are
            ignored.
        conn: Open connection with the schema on the ``search_path``.
        location_id: ``location_id`` for appended rows.
        players_index: Optional pre-built name index (a
            :class:`funhouse_pipeline.load.loader._NameIndex`); built here from
            the ``players`` table when not supplied.
        captured_by_user_id: Acting user recorded on rows and audit entries.
        device_id: Optional originating device for audit entries.

    Returns:
        A :class:`ConsentLoadResult` listing appended and flagged records.
    """
    # Lazy import avoids a circular dependency with the loader module.
    from funhouse_pipeline.load.loader import _NameIndex, _build_players_index

    index = players_index if players_index is not None else _build_players_index(conn)
    result = ConsentLoadResult()

    for record in records:
        if record.target_table != "consents":
            continue
        payload = record.payload or {}

        player_name = payload.get("player_name")
        if player_name is None or str(player_name).strip() == "":
            result.flagged.append(FlaggedConsent(record.record_id, MISSING_PLAYER))
            continue
        resolved = index.resolve(player_name)
        if resolved is None:
            result.flagged.append(
                FlaggedConsent(record.record_id, UNRESOLVED_PLAYER, str(player_name))
            )
            continue
        if resolved is _NameIndex.AMBIGUOUS:
            result.flagged.append(
                FlaggedConsent(record.record_id, AMBIGUOUS_PLAYER, str(player_name))
            )
            continue

        consent_type = payload.get("consent_type")
        if consent_type is None or str(consent_type).strip() == "":
            result.flagged.append(FlaggedConsent(record.record_id, MISSING_CONSENT_TYPE))
            continue

        granted = _as_bool(payload.get("granted"))
        try:
            consent_id = append_consent(
                conn,
                player_id=resolved,
                consent_type=str(consent_type),
                granted=granted,
                location_id=location_id,
                granted_at=payload.get("granted_at"),
                method=payload.get("method"),
                captured_by_user_id=captured_by_user_id,
                device_id=device_id,
            )
        except Exception as exc:  # noqa: BLE001 - per-record isolation
            result.flagged.append(
                FlaggedConsent(record.record_id, APPEND_ERROR, str(exc))
            )
            continue

        result.appended.append(
            AppendedConsent(
                consent_id=consent_id,
                player_id=resolved,
                consent_type=str(consent_type),
                granted=granted,
                record_id=record.record_id,
            )
        )

    return result
