"""Property-based tests for the Sync_Service (Task 14, Req 4, 5).

Implements three design correctness properties over a real ephemeral
PostgreSQL (skips when none is reachable), each at a minimum of 100 Hypothesis
iterations:

* Property 8  -- batch sync is idempotent (apply twice == apply once; the second
  application reports every action ``skipped``; no duplicate rows) (Req 4.2,
  4.3, 4.6).
* Property 9  -- per-action result completeness and failure isolation (exactly
  one result per action; a failing action does not block the rest) (Req 4.1,
  4.5).
* Property 10 -- last-write-wins is monotonic and order-independent (Req 5.1,
  5.2, 5.4).

The reused Phase 0 dedup/consent/audit/loader logic and the Entitlement_Engine
all run for real -- nothing deterministic is mocked. The Sync_Service commits
each applied action, so every example uses unique client_ids/identities and the
disposable-schema fixture drops all rows at teardown.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from funhouse_api.rbac import Scope
from funhouse_api.sync import service
from funhouse_pipeline.db.migrations import run_migrations
from funhouse_pipeline.db.seed import seed

pytestmark = [pytest.mark.db, pytest.mark.property]

_DB_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

_BASE = datetime(2024, 6, 1, 12, tzinfo=timezone.utc)


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


def _seed_product(conn) -> str:
    return str(conn.execute("SELECT id FROM products LIMIT 1").fetchone()[0])


def _count(conn, table: str) -> int:
    conn.rollback()  # read on a clean transaction
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


# --------------------------------------------------------------------------- #
# Property 8: batch sync is idempotent (Req 4.2, 4.3, 4.6)
# --------------------------------------------------------------------------- #


def _build_mixed_actions(loc, player_id, product_id):
    """A representative mixed batch: player, consent, session, payment, entitlement."""
    tag = uuid.uuid4().hex[:10]
    return [
        service.SyncAction(
            client_id=f"pl-{tag}", entity="player", created_at=_BASE,
            payload={"first_name": f"Sync{tag}", "last_name": "Doe",
                     "birth_date": "2012-03-04", "location_id": loc},
        ),
        service.SyncAction(
            client_id=f"co-{tag}", entity="consent", created_at=_BASE,
            payload={"player_id": player_id, "consent_type": "data_processing",
                     "granted": True},
        ),
        service.SyncAction(
            client_id=f"se-{tag}", entity="session", created_at=_BASE,
            payload={"player_id": player_id, "session_type": "lounge",
                     "started_at": "2024-06-01T10:00:00+00:00",
                     "ended_at": "2024-06-01T10:30:00+00:00",
                     "duration_minutes": 30},
        ),
        service.SyncAction(
            client_id=f"pa-{tag}", entity="payment", created_at=_BASE,
            payload={"player_id": player_id, "product_id": product_id,
                     "amount_cents": 3000, "method": "cash",
                     "paid_at": "2024-06-01T10:31:00+00:00"},
        ),
        service.SyncAction(
            client_id=f"en-{tag}", entity="entitlement", created_at=_BASE,
            payload={"player_id": player_id, "product_id": product_id},
        ),
    ]


# Feature: funhouse-api, Property 8: Batch sync is idempotent (applying a batch
# twice equals applying it once). Applying a batch then the identical batch again
# yields the same final state as applying once; the second application reports
# every action skipped; no duplicate rows for a repeated key.
# Validates: Requirements 4.2, 4.3, 4.6
@_DB_SETTINGS
@given(seed_val=st.integers(min_value=0, max_value=10_000))
def test_property_8_batch_idempotent(seeded_db, seed_val):
    conn, loc, aya = seeded_db
    player_id = _make_player(conn, loc)
    product_id = _seed_product(conn)
    actions = _build_mixed_actions(loc, player_id, product_id)

    tables = ("players", "consents", "sessions", "payments", "entitlements")
    before = {t: _count(conn, t) for t in tables}

    first = service.apply_batch(conn, _founder(), actions, logged_by=aya)
    after_first = {t: _count(conn, t) for t in tables}

    # Every action applied on the first pass.
    assert all(r.status == service.STATUS_APPLIED for r in first), [
        (r.entity, r.status, r.reason) for r in first
    ]
    # Each entity created exactly one row.
    for t in tables:
        assert after_first[t] == before[t] + 1, t

    # Second identical application: all skipped, no new rows (idempotent).
    second = service.apply_batch(conn, _founder(), actions, logged_by=aya)
    after_second = {t: _count(conn, t) for t in tables}
    assert all(r.status == service.STATUS_SKIPPED for r in second), [
        (r.entity, r.status, r.reason) for r in second
    ]
    assert after_second == after_first  # final state unchanged


# --------------------------------------------------------------------------- #
# Property 9: per-action completeness and failure isolation (Req 4.1, 4.5)
# --------------------------------------------------------------------------- #

_VALID_STATUSES = {service.STATUS_APPLIED, service.STATUS_SKIPPED, service.STATUS_REJECTED}


# Feature: funhouse-api, Property 9: Per-action result completeness and failure
# isolation. For any batch, the response contains exactly one result per
# submitted action with a status in {applied, skipped, rejected}; a failing
# action does not prevent the remaining valid actions from applying.
# Validates: Requirements 4.1, 4.5
@_DB_SETTINGS
@given(
    n_good_before=st.integers(min_value=0, max_value=3),
    n_bad=st.integers(min_value=1, max_value=3),
    n_good_after=st.integers(min_value=0, max_value=3),
)
def test_property_9_completeness_and_isolation(
    seeded_db, n_good_before, n_bad, n_good_after
):
    conn, loc, aya = seeded_db
    player_id = _make_player(conn, loc)

    def _good_session(i):
        return service.SyncAction(
            client_id=f"good-{uuid.uuid4().hex[:8]}-{i}", entity="session",
            created_at=_BASE + timedelta(seconds=i),
            payload={"player_id": player_id, "session_type": "lounge",
                     "started_at": f"2024-06-01T10:{i % 60:02d}:00+00:00",
                     "duration_minutes": 20 + i},
        )

    def _bad_action(i):
        # A session referencing a non-existent player -> handler raises ->
        # isolated rejection (Req 4.5).
        return service.SyncAction(
            client_id=f"bad-{uuid.uuid4().hex[:8]}-{i}", entity="session",
            created_at=_BASE,
            payload={"player_id": str(uuid.uuid4()), "session_type": "lounge",
                     "duration_minutes": 15},
        )

    actions = (
        [_good_session(i) for i in range(n_good_before)]
        + [_bad_action(i) for i in range(n_bad)]
        + [_good_session(100 + i) for i in range(n_good_after)]
    )

    sessions_before = _count(conn, "sessions")
    results = service.apply_batch(conn, _founder(), actions, logged_by=aya)

    # Exactly one result per action, each a valid status.
    assert len(results) == len(actions)
    assert all(r.status in _VALID_STATUSES for r in results)

    # Every bad action is rejected; every good action applied.
    bad_results = [r for r in results if r.client_id.startswith("bad-")]
    good_results = [r for r in results if r.client_id.startswith("good-")]
    assert all(r.status == service.STATUS_REJECTED for r in bad_results)
    assert all(r.status == service.STATUS_APPLIED for r in good_results), [
        (r.status, r.reason) for r in good_results
    ]

    # The good sessions were actually persisted despite the failures between.
    assert _count(conn, "sessions") == sessions_before + n_good_before + n_good_after


# --------------------------------------------------------------------------- #
# Property 10: last-write-wins monotonic + order-independent (Req 5.1, 5.2, 5.4)
# --------------------------------------------------------------------------- #


def _session_edit(player_id, *, client_id, minutes, created_at):
    """A session action with a fixed identity (player+type+times) and a mutable
    duration -- so re-submissions collide on one natural key for LWW."""
    return service.SyncAction(
        client_id=client_id, entity="session", created_at=created_at,
        payload={"player_id": player_id, "session_type": "lounge",
                 "started_at": "2024-06-01T09:00:00+00:00",
                 "ended_at": "2024-06-01T09:45:00+00:00",
                 "duration_minutes": minutes},
    )


# Feature: funhouse-api, Property 10: Last-write-wins is monotonic and
# order-independent. The final stored values are those of the action with the
# latest device-origin created_at; an older incoming action never overwrites a
# newer stored one (reported skipped); equal created_at resolves deterministically
# by client_id, so any submission order yields the same final state.
# Validates: Requirements 5.1, 5.2, 5.4
@_DB_SETTINGS
@given(
    offsets=st.lists(
        st.integers(min_value=0, max_value=20), min_size=2, max_size=6, unique=True
    ),
    data=st.data(),
)
def test_property_10_last_write_wins(seeded_db, offsets, data):
    conn, loc, aya = seeded_db
    player_id = _make_player(conn, loc)

    # Each edit: created_at = base + offset days, duration encodes the offset so
    # we can identify which edit won. client_id is unique per edit.
    edits = [
        _session_edit(
            player_id,
            client_id=f"edit-{off}-{uuid.uuid4().hex[:6]}",
            minutes=100 + off,
            created_at=_BASE + timedelta(days=off),
        )
        for off in offsets
    ]

    # Submit in a shuffled order (order-independence).
    shuffled = data.draw(st.permutations(edits))
    results = service.apply_batch(conn, _founder(), list(shuffled), logged_by=aya)

    # Exactly one session row (all edits collide on one natural key).
    conn.rollback()
    rows = conn.execute(
        "SELECT duration_minutes FROM sessions WHERE player_id = %s", (player_id,)
    ).fetchall()
    assert len(rows) == 1

    # The winner is the edit with the latest created_at (largest offset).
    winner_off = max(offsets)
    assert rows[0][0] == 100 + winner_off

    # An older-than-current incoming action is reported skipped; the newest is
    # applied at least once. Depending on the shuffle several may apply as the
    # running max advances, but never a stale overwrite.
    assert any(r.status == service.STATUS_APPLIED for r in results)
    assert all(r.status in _VALID_STATUSES for r in results)
