"""Property-based tests for POPIA + audit cross-cutting guarantees (Task 15).

Implements three design correctness properties over a real ephemeral
PostgreSQL (skips when none is reachable), each at a minimum of 100 Hypothesis
iterations:

* Property 22 -- prohibited fields are never persisted: any payload containing a
  national identity number or physical address (in any recognized spelling) is
  stripped by ``popia.filter_payload`` before the write, so no Prohibited_Field
  reaches the stored row (Req 14.1).
* Property 11 -- every persisted write has a matching ``sync_log`` entry, and
  ``logged_by`` is set on the target tables that carry that column (Req 4.4,
  7.5, 10.4, 14.2).
* Property 12 -- a write and its audit entry commit together: if the ``sync_log``
  append cannot be recorded, the whole write rolls back so no business row is
  persisted without its audit entry (Req 14.6).

All writes go through the real Sync_Service and the reused Phase 0 write paths;
nothing deterministic is mocked except the injected failing audit append that
Property 12 uses to force the roll-back branch.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from funhouse_api.rbac import Scope
from funhouse_api.sync import service
from funhouse_pipeline.db.migrations import run_migrations
from funhouse_pipeline.db.seed import seed
from funhouse_pipeline.load.popia import is_prohibited_key

pytestmark = [pytest.mark.db, pytest.mark.property]

_DB_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

_BASE = datetime(2024, 6, 1, 12, tzinfo=timezone.utc)


@pytest.fixture
def seeded_db(db_connection):
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


def _make_player(conn, loc, school_id=None) -> str:
    pid = str(
        conn.execute(
            "INSERT INTO players (first_name, consent_status, location_id, school_id) "
            "VALUES (%s, 'pending', %s, %s) RETURNING id",
            (f"P{uuid.uuid4().hex[:8]}", loc, school_id),
        ).fetchone()[0]
    )
    conn.commit()
    return pid


# Recognized prohibited-key spellings (each canonicalizes to a PROHIBITED key).
_PROHIBITED_SPELLINGS = [
    "id_number", "ID Number", "id-number", "national_id", "SA ID Number",
    "identity_number", "passport", "passport_no",
    "address", "Physical Address", "home_address", "residential_address",
    "street", "street_name", "postal_code", "zip",
]


# --------------------------------------------------------------------------- #
# Property 22: prohibited fields are never persisted (Req 14.1)
# --------------------------------------------------------------------------- #


# Feature: funhouse-api, Property 22: Prohibited fields are never persisted
# (POPIA). For any write payload containing one or more Prohibited_Fields
# (national identity numbers or physical addresses in any recognized spelling),
# no Prohibited_Field is present on the stored row.
# Validates: Requirements 14.1
@_DB_SETTINGS
@given(
    prohibited=st.lists(st.sampled_from(_PROHIBITED_SPELLINGS), min_size=1, max_size=5),
    grade=st.text(alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")),
                  min_size=0, max_size=6),
)
def test_property_22_prohibited_fields_never_persisted(seeded_db, prohibited, grade):
    conn, loc, aya = seeded_db
    tag = uuid.uuid4().hex[:10]
    secret = f"SECRET-{uuid.uuid4().hex}"  # a value we can search for everywhere

    # Every recognized spelling really is prohibited (sanity on the generator).
    assert all(is_prohibited_key(k) for k in prohibited)

    payload = {
        "first_name": f"Clean{tag}",
        "grade": grade,
        "location_id": loc,
    }
    for key in prohibited:
        payload[key] = secret

    action = service.SyncAction(
        client_id=f"p22-{tag}", entity="player", created_at=_BASE, payload=payload
    )
    [result] = service.apply_batch(conn, _founder(), [action], logged_by=aya)
    assert result.status == service.STATUS_APPLIED, result.reason

    # The prohibited secret value appears in no text column of the stored row.
    conn.rollback()
    row = conn.execute(
        "SELECT first_name, last_name, grade, dedup_key "
        "FROM players WHERE id = %s",
        (result.record_id,),
    ).fetchone()
    assert row is not None
    for value in row:
        assert secret not in (str(value) if value is not None else "")

    # The POPIA filter stripped every prohibited key before the write, so the
    # cleaned payload the write saw carries none of them (Req 14.1).
    from funhouse_pipeline.load.popia import filter_payload

    clean, dropped = filter_payload(payload)
    assert set(dropped) == set(prohibited)
    assert not any(is_prohibited_key(k) for k in clean)


# --------------------------------------------------------------------------- #
# Property 11: every persisted write has a matching sync_log entry (Req 14.2)
# --------------------------------------------------------------------------- #

# Tables carrying a logged_by column (schema 001); logged_by must be set there.
_LOGGED_BY_TABLES = {"sessions", "payments", "attendance"}


# Feature: funhouse-api, Property 11: Every persisted write has a matching
# sync_log entry. For any successful write (resource or sync), a sync_log row
# references that entity and record id, and logged_by is set on target tables
# that carry that column.
# Validates: Requirements 4.4, 7.5, 10.4, 14.2
@_DB_SETTINGS
@given(
    include_attendance=st.booleans(),
    include_entitlement=st.booleans(),
    duration=st.integers(min_value=1, max_value=180),
)
def test_property_11_every_write_has_audit(
    seeded_db, include_attendance, include_entitlement, duration
):
    conn, loc, aya = seeded_db
    player_id = _make_player(conn, loc)
    product_id = str(conn.execute("SELECT id FROM products LIMIT 1").fetchone()[0])
    tag = uuid.uuid4().hex[:10]

    actions = [
        service.SyncAction(
            client_id=f"pl-{tag}", entity="player", created_at=_BASE,
            payload={"first_name": f"Aud{tag}", "location_id": loc},
        ),
        service.SyncAction(
            client_id=f"se-{tag}", entity="session", created_at=_BASE,
            payload={"player_id": player_id, "session_type": "lounge",
                     "started_at": "2024-06-01T10:00:00+00:00",
                     "duration_minutes": duration},
        ),
        service.SyncAction(
            client_id=f"pa-{tag}", entity="payment", created_at=_BASE,
            payload={"player_id": player_id, "product_id": product_id,
                     "amount_cents": 3000, "method": "cash",
                     "paid_at": "2024-06-01T10:31:00+00:00"},
        ),
    ]
    if include_attendance:
        actions.append(
            service.SyncAction(
                client_id=f"at-{tag}", entity="attendance", created_at=_BASE,
                payload={"player_id": player_id, "attendance_date": "2024-06-01",
                         "present": True},
            )
        )
    if include_entitlement:
        actions.append(
            service.SyncAction(
                client_id=f"en-{tag}", entity="entitlement", created_at=_BASE,
                payload={"player_id": player_id, "product_id": product_id},
            )
        )

    results = service.apply_batch(conn, _founder(), actions, logged_by=aya)
    assert all(r.status == service.STATUS_APPLIED for r in results), [
        (r.entity, r.status, r.reason) for r in results
    ]

    conn.rollback()
    table_for = {
        "player": "players", "session": "sessions", "payment": "payments",
        "attendance": "attendance", "entitlement": "entitlements",
    }
    for r in results:
        table = table_for[r.entity]
        # A sync_log row references this entity + record id (Req 14.2).
        n_audit = conn.execute(
            "SELECT count(*) FROM sync_log WHERE entity = %s AND record_id = %s",
            (table, r.record_id),
        ).fetchone()[0]
        assert n_audit >= 1, (table, r.record_id)
        # logged_by is set where the table carries it (Req 4.4, 7.5, 10.4).
        if table in _LOGGED_BY_TABLES:
            logged_by = conn.execute(
                f"SELECT logged_by FROM {table} WHERE id = %s", (r.record_id,)
            ).fetchone()[0]
            assert str(logged_by) == str(aya)


# --------------------------------------------------------------------------- #
# Property 12: a write and its audit entry commit together (Req 14.6)
# --------------------------------------------------------------------------- #


# Feature: funhouse-api, Property 12: Audit atomicity -- a write and its audit
# entry commit together. For any write, if the sync_log append cannot be
# recorded, the entire write is rolled back so no business row is persisted
# without its audit entry.
# Validates: Requirements 14.6
@_DB_SETTINGS
@given(
    fail_audit=st.booleans(),
    duration=st.integers(min_value=1, max_value=180),
)
def test_property_12_write_audit_atomic(seeded_db, fail_audit, duration):
    conn, loc, aya = seeded_db
    player_id = _make_player(conn, loc)
    tag = uuid.uuid4().hex[:10]

    def _boom(*args, **kwargs):
        raise service.SyncAuditError("injected audit-append failure")

    conn.rollback()
    before = conn.execute("SELECT count(*) FROM sessions").fetchone()[0]

    action = service.SyncAction(
        client_id=f"p12-{tag}", entity="session", created_at=_BASE,
        payload={"player_id": player_id, "session_type": "lounge",
                 "started_at": "2024-06-01T10:00:00+00:00",
                 "duration_minutes": duration},
    )
    # Inject the failing audit append per-example and always restore it, so the
    # patch never leaks into a later Hypothesis example (unlike monkeypatch,
    # which resets only at function teardown).
    original_append = service.append_sync_log
    if fail_audit:
        service.append_sync_log = _boom
    try:
        [result] = service.apply_batch(conn, _founder(), [action], logged_by=aya)
    finally:
        service.append_sync_log = original_append

    conn.rollback()
    after = conn.execute("SELECT count(*) FROM sessions").fetchone()[0]

    if fail_audit:
        # The audit could not be recorded -> whole write rolled back (Req 14.6).
        assert result.status == service.STATUS_REJECTED
        assert after == before  # no orphaned session row
    else:
        assert result.status == service.STATUS_APPLIED
        assert after == before + 1
        # The write and its audit entry both landed.
        n_audit = conn.execute(
            "SELECT count(*) FROM sync_log WHERE entity = 'sessions' AND record_id = %s",
            (result.record_id,),
        ).fetchone()[0]
        assert n_audit >= 1
