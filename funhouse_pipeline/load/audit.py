"""Audit-trail helper: append ``sync_log`` entries (Task 12.1, Req 14.5).

POPIA-by-design means the pipeline must be able to answer "who touched this
child's record and when" (design § Overview, principle 3). Req 14.5 makes this
concrete: **every** write the Loader performs records the acting identity in
``logged_by`` (on the tables that carry that column) *and* appends a
corresponding ``sync_log`` entry referencing the ``entity`` (table name), the
written row's ``record_id``, and the ``action``.

This module is the single, deterministic place that writes ``sync_log`` rows so
both :mod:`funhouse_pipeline.load.loader` (records) and
:mod:`funhouse_pipeline.load.consent` (consent ledger) audit their writes the
same way.

Atomicity
---------
:func:`append_sync_log` operates on a **cursor**, never on its own transaction.
The caller opens the audit insert inside the *same* per-record transaction that
performed the write, so the audit entry and the write commit (or roll back)
together -- the audit trail can never diverge from what was actually written
(design § Error Handling: a duplicate skip appends a ``skip`` ``sync_log`` entry;
a failed insert leaves neither a row nor an audit entry).

Actions
-------
The schema's ``sync_log.action`` CHECK permits exactly ``insert``, ``update``,
``delete`` and ``skip`` (migration 001). The loader uses:

* ``insert`` -- a new row was created,
* ``skip``   -- a natural-key/dedup duplicate was a no-op (Req 9.5),
* ``update`` -- an existing row was merged/filled in place (player dedup merge).
"""

from __future__ import annotations

from typing import Any

#: ``sync_log.action`` values (mirrors the schema CHECK in migration 001).
ACTION_INSERT = "insert"
ACTION_UPDATE = "update"
ACTION_DELETE = "delete"
ACTION_SKIP = "skip"

#: The full set of permitted actions (matches the DB CHECK constraint).
ALLOWED_ACTIONS: frozenset[str] = frozenset(
    {ACTION_INSERT, ACTION_UPDATE, ACTION_DELETE, ACTION_SKIP}
)


def append_sync_log(
    cursor: Any,
    *,
    entity: str,
    record_id: Any,
    action: str,
    location_id: Any,
    user_id: Any | None = None,
    device_id: Any | None = None,
    client_timestamp: Any | None = None,
) -> Any:
    """Append one ``sync_log`` row on ``cursor`` and return its id (Req 14.5).

    The insert is issued on the supplied cursor so it participates in whatever
    transaction the caller has open -- keeping the audit entry atomic with the
    write it describes. This function never commits.

    Args:
        cursor: An open DB-API cursor inside the write's transaction.
        entity: The table name the audited row belongs to (``sync_log.entity``).
        record_id: The id of the written (or, for a ``skip``, the pre-existing)
            row (``sync_log.record_id``, NOT NULL in the schema).
        action: One of :data:`ALLOWED_ACTIONS` (``insert``/``update``/``skip``/
            ``delete``).
        location_id: ``sync_log.location_id`` (NOT NULL FK to ``locations``).
        user_id: The acting identity (``sync_log.user_id``; nullable when the
            acting user is unknown for a backfill).
        device_id: Optional originating device (``sync_log.device_id``).
        client_timestamp: Optional client-side timestamp of the action.

    Returns:
        The new ``sync_log.id``.

    Raises:
        ValueError: If ``action`` is not one of the permitted actions.
    """
    if action not in ALLOWED_ACTIONS:
        raise ValueError(
            f"invalid sync_log action {action!r}; expected one of "
            f"{sorted(ALLOWED_ACTIONS)}"
        )
    cursor.execute(
        "INSERT INTO sync_log "
        "(entity, record_id, action, user_id, device_id, location_id, client_timestamp) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (entity, record_id, action, user_id, device_id, location_id, client_timestamp),
    )
    return cursor.fetchone()[0]
