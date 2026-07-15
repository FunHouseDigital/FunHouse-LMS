"""Tests for the ``student_metrics`` sync entity (Task 17, Req 4.8, closes D1).

The ``student_metrics`` entity rides the SAME natural-key insert/LWW path as
session/attendance/payment, so it inherits the design's Property 8 (idempotency)
and Property 10 (last-write-wins) guarantees. This module adds:

* a **non-DB mapping-wiring** assertion (always runs, even without PostgreSQL);
* **Property 8** coverage extended to ``student_metrics`` (idempotent re-send:
  no duplicate row; second application ``skipped``);
* **Property 10** coverage extended to ``student_metrics`` (LWW on the same
  natural key ``(player_id, metric_type, measured_at)``, order-independent);
* **example tests** for Req 4.8 specifics: ``logged_by`` + ``location_id``
  stamping and a matching ``sync_log`` entry; out-of-scope rejection; and an
  invalid ``metric_type`` rejected in isolation while the batch continues.

DB-backed tests reuse the ``db_connection`` fixture and skip gracefully when no
PostgreSQL server is reachable.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from funhouse_api.rbac import Scope
from funhouse_api.sync import mapping, service
from funhouse_pipeline.db.migrations import run_migrations
from funhouse_pipeline.db.seed import seed

_BASE = datetime(2024, 6, 1, 12, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Non-DB: mapping wiring (always runs; guards the static registration)
# --------------------------------------------------------------------------- #


def test_student_metrics_mapping_wiring():
    """student_metrics is a valid natural-key entity wired into the registry."""
    assert mapping.ENTITY_STUDENT_METRICS == "student_metrics"
    assert mapping.ENTITY_STUDENT_METRICS in mapping.VALID_ENTITIES

    emap = mapping.MAPPINGS[mapping.ENTITY_STUDENT_METRICS]
    assert emap.table == "student_metrics"
    assert emap.key_kind == mapping.KEY_NATURAL
    assert emap.key_column == "natural_key"

    # Identifying fields are exactly (player_id, metric_type, measured_at) (Req 4.8).
    assert mapping._NATURAL_KEY_FIELDS[mapping.ENTITY_STUDENT_METRICS] == (
        "player_id",
        "metric_type",
        "measured_at",
    )

    # The natural key is deterministic and stable for the same identity.
    payload = {
        "player_id": "11111111-1111-1111-1111-111111111111",
        "metric_type": "typing_wpm",
        "measured_at": "2024-06-01T10:00:00+00:00",
        "value": "42",
    }
    k1 = mapping.compute_sync_natural_key(mapping.ENTITY_STUDENT_METRICS, payload)
    k2 = mapping.compute_sync_natural_key(
        mapping.ENTITY_STUDENT_METRICS, {**payload, "value": "999"}
    )
    assert k1.startswith("student_metrics:")
    assert k1 == k2  # value is not identifying -> edits collide on one key


# --------------------------------------------------------------------------- #
# DB-backed fixtures / helpers
# --------------------------------------------------------------------------- #

_DB_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


@pytest.fixture
def seeded_db(db_connection):
    """Run migrations + seed and return ``(conn, location_id, user_id)``."""
    run_migrations(db_connection)
    seed(db_connection)
    loc = db_connection.execute(
        "SELECT id FROM locations WHERE name = 'Smithfield'"
    ).fetchone()[0]
    aya = db_connection.execute("SELECT id FROM users WHERE name = 'Aya'").fetchone()[0]
    db_connection.commit()
    return db_connection, loc, aya


def _founder() -> Scope:
    return Scope(role="founder", location_id=None, school_id=None)


def _make_player(conn, location_id, school_id=None) -> str:
    pid = str(
        conn.execute(
            "INSERT INTO players (first_name, consent_status, location_id, school_id) "
            "VALUES (%s, 'pending', %s, %s) RETURNING id",
            (f"P{uuid.uuid4().hex[:8]}", location_id, school_id),
        ).fetchone()[0]
    )
    conn.commit()
    return pid


def _count(conn, table: str) -> int:
    conn.rollback()  # read on a clean transaction
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def _metric_action(player_id, *, client_id, value, created_at, metric_type="typing_wpm",
                   measured_at="2024-06-01T10:00:00+00:00"):
    return service.SyncAction(
        client_id=client_id,
        entity="student_metrics",
        created_at=created_at,
        payload={
            "player_id": player_id,
            "metric_type": metric_type,
            "measured_at": measured_at,
            "value": value,
        },
    )


pytestmark_db = [pytest.mark.db, pytest.mark.property]


# --------------------------------------------------------------------------- #
# Property 8 (extended to student_metrics): idempotent re-send
# --------------------------------------------------------------------------- #


# Feature: funhouse-api, Property 8: Batch sync is idempotent (applying a batch
# twice equals applying it once) -- extended to the student_metrics entity: a
# re-sent metric creates no duplicate row and is reported skipped.
# Validates: Requirements 4.2, 4.3, 4.6, 4.8
@pytest.mark.db
@pytest.mark.property
@_DB_SETTINGS
@given(value=st.integers(min_value=0, max_value=300))
def test_student_metrics_idempotent(seeded_db, value):
    conn, loc, aya = seeded_db
    player_id = _make_player(conn, loc)
    action = _metric_action(
        player_id, client_id=f"sm-{uuid.uuid4().hex[:10]}", value=value,
        created_at=_BASE,
    )

    before = _count(conn, "student_metrics")
    first = service.apply_batch(conn, _founder(), [action], logged_by=aya)
    after_first = _count(conn, "student_metrics")

    assert first[0].status == service.STATUS_APPLIED, (first[0].status, first[0].reason)
    assert after_first == before + 1

    # Re-sending the identical action is a no-op skip, no duplicate row.
    second = service.apply_batch(conn, _founder(), [action], logged_by=aya)
    assert second[0].status == service.STATUS_SKIPPED
    assert _count(conn, "student_metrics") == after_first


# --------------------------------------------------------------------------- #
# Property 10 (extended to student_metrics): last-write-wins, order-independent
# --------------------------------------------------------------------------- #


# Feature: funhouse-api, Property 10: Last-write-wins is monotonic and
# order-independent -- extended to student_metrics: edits sharing the natural key
# (player_id, metric_type, measured_at) collapse to one row whose value is that
# of the latest device-origin created_at, regardless of submission order.
# Validates: Requirements 5.1, 5.2, 5.4, 4.8
@pytest.mark.db
@pytest.mark.property
@_DB_SETTINGS
@given(
    offsets=st.lists(
        st.integers(min_value=0, max_value=20), min_size=2, max_size=6, unique=True
    ),
    data=st.data(),
)
def test_student_metrics_last_write_wins(seeded_db, offsets, data):
    conn, loc, aya = seeded_db
    player_id = _make_player(conn, loc)

    edits = [
        _metric_action(
            player_id,
            client_id=f"sm-{off}-{uuid.uuid4().hex[:6]}",
            value=str(100 + off),
            created_at=_BASE + timedelta(days=off),
        )
        for off in offsets
    ]
    shuffled = data.draw(st.permutations(edits))
    results = service.apply_batch(conn, _founder(), list(shuffled), logged_by=aya)

    conn.rollback()
    rows = conn.execute(
        "SELECT value FROM student_metrics WHERE player_id = %s", (player_id,)
    ).fetchall()
    # One row (all edits collide on one natural key); value is the latest edit's.
    assert len(rows) == 1
    assert rows[0][0] == str(100 + max(offsets))
    assert any(r.status == service.STATUS_APPLIED for r in results)


# --------------------------------------------------------------------------- #
# Example tests: Req 4.8 specifics (stamping, audit, scope, invalid metric_type)
# --------------------------------------------------------------------------- #


@pytest.mark.db
def test_student_metrics_stamps_logged_by_location_and_audit(seeded_db):
    """logged_by + location_id are stamped and a sync_log entry is appended (Req 4.8)."""
    conn, loc, aya = seeded_db
    player_id = _make_player(conn, loc)
    action = _metric_action(
        player_id, client_id="sm-stamp", value="85",
        created_at=_BASE, metric_type="typing_accuracy",
    )

    results = service.apply_batch(conn, _founder(), [action], logged_by=aya)
    assert results[0].status == service.STATUS_APPLIED, results[0].reason
    record_id = results[0].record_id

    conn.rollback()
    row = conn.execute(
        "SELECT player_id, metric_type, value, logged_by, location_id "
        "FROM student_metrics WHERE id = %s",
        (record_id,),
    ).fetchone()
    assert str(row[0]) == str(player_id)
    assert row[1] == "typing_accuracy"
    assert row[2] == "85"  # stored as TEXT
    assert str(row[3]) == str(aya)  # logged_by = acting user
    assert str(row[4]) == str(loc)  # location_id stamped to caller scope

    # A matching sync_log entry exists for the write (Req 4.4, 4.8).
    audit = conn.execute(
        "SELECT count(*) FROM sync_log WHERE entity = 'student_metrics' "
        "AND record_id = %s AND action = 'insert'",
        (record_id,),
    ).fetchone()[0]
    assert audit >= 1


@pytest.mark.db
def test_student_metrics_out_of_scope_rejected(seeded_db):
    """A metric targeting a player outside the caller's scope is rejected (Req 4.7)."""
    conn, loc, aya = seeded_db
    player_id = _make_player(conn, loc)  # player in Smithfield

    # A manager scoped to a DIFFERENT location cannot write this player's metric.
    other_scope = Scope(role="manager", location_id=str(uuid.uuid4()), school_id=None)
    action = _metric_action(
        player_id, client_id="sm-oos", value="60", created_at=_BASE
    )

    before = _count(conn, "student_metrics")
    results = service.apply_batch(conn, other_scope, [action], logged_by=aya)
    assert results[0].status == service.STATUS_REJECTED
    assert _count(conn, "student_metrics") == before  # nothing persisted


@pytest.mark.db
def test_student_metrics_invalid_metric_type_isolated(seeded_db):
    """An invalid metric_type is rejected in isolation; the batch continues (Req 4.8, 4.5)."""
    conn, loc, aya = seeded_db
    player_id = _make_player(conn, loc)

    bad = _metric_action(
        player_id, client_id="sm-bad", value="1", created_at=_BASE,
        metric_type="not_a_metric",
    )
    good = _metric_action(
        player_id, client_id="sm-good", value="120", created_at=_BASE,
        metric_type="quiz_score", measured_at="2024-06-02T09:00:00+00:00",
    )

    before = _count(conn, "student_metrics")
    results = service.apply_batch(conn, _founder(), [bad, good], logged_by=aya)

    by_id = {r.client_id: r for r in results}
    assert by_id["sm-bad"].status == service.STATUS_REJECTED
    assert by_id["sm-bad"].reason == "invalid_metric_type"
    assert by_id["sm-good"].status == service.STATUS_APPLIED, by_id["sm-good"].reason
    # Only the good metric persisted; the bad one never crashed the batch.
    assert _count(conn, "student_metrics") == before + 1


@pytest.mark.db
def test_student_metrics_missing_value_rejected(seeded_db):
    """A metric missing its required value is rejected cleanly (Req 4.8)."""
    conn, loc, aya = seeded_db
    player_id = _make_player(conn, loc)
    action = service.SyncAction(
        client_id="sm-noval", entity="student_metrics", created_at=_BASE,
        payload={"player_id": player_id, "metric_type": "observation",
                 "measured_at": "2024-06-01T10:00:00+00:00"},
    )
    before = _count(conn, "student_metrics")
    results = service.apply_batch(conn, _founder(), [action], logged_by=aya)
    assert results[0].status == service.STATUS_REJECTED
    assert results[0].reason == "value_required"
    assert _count(conn, "student_metrics") == before
