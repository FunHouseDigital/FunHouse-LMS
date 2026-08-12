"""Regression tests for entitlement-draw sync idempotency.

These tests exercise stable offline-action receipts against a real PostgreSQL
schema. They cover sequential and concurrent replay, the migration-010 legacy
handover, direct API draws, and unlimited entitlements.
"""

from __future__ import annotations

import json
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event

import pytest

from funhouse_api.entitlements import engine
from funhouse_api.rbac import Scope
from funhouse_api.sync import service
from funhouse_pipeline.db.migrations import run_migrations
from funhouse_pipeline.db.seed import seed

pytestmark = [pytest.mark.db]

_CREATED_AT = datetime(2024, 5, 31, 12, tzinfo=timezone.utc)
_DRAW_AT = datetime(2024, 6, 1, 12, tzinfo=timezone.utc)


@pytest.fixture
def seeded_db(db_connection):
    """Run migrations and seed reference data for an isolated schema."""
    run_migrations(db_connection)
    seed(db_connection)
    location_id = db_connection.execute(
        "SELECT id FROM locations WHERE name = 'Smithfield'"
    ).fetchone()[0]
    user_id = db_connection.execute(
        "SELECT id FROM users WHERE name = 'Aya'"
    ).fetchone()[0]
    db_connection.commit()
    return db_connection, location_id, user_id


def _founder() -> Scope:
    return Scope(role="founder", location_id=None, school_id=None)


def _make_entitlement(conn, location_id, user_id, rules):
    """Create a uniquely named product, player and entitlement."""
    product_id = conn.execute(
        "INSERT INTO products (name, type, price_cents, rules, location_id) "
        "VALUES (%s, 'once_off_pass', 1000, %s::jsonb, %s) RETURNING id",
        (f"idempotency-{uuid.uuid4().hex}", json.dumps(rules), location_id),
    ).fetchone()[0]
    player_id = conn.execute(
        "INSERT INTO players (first_name, consent_status, location_id) "
        "VALUES (%s, 'pending', %s) RETURNING id",
        (f"P{uuid.uuid4().hex[:10]}", location_id),
    ).fetchone()[0]
    conn.commit()
    return engine.create_entitlement(
        conn,
        player_id=player_id,
        product_id=product_id,
        location_id=location_id,
        logged_by=user_id,
        now=_CREATED_AT,
    )


def _draw_action(client_id: str, entitlement_id, created_at: datetime, amount: int = 3):
    return service.SyncAction(
        client_id=client_id,
        entity="entitlement",
        created_at=created_at,
        payload={"entitlement_id": str(entitlement_id), "amount": amount},
    )


def _remaining_units(conn, entitlement_id):
    conn.rollback()
    return conn.execute(
        "SELECT remaining_units FROM entitlements WHERE id = %s",
        (entitlement_id,),
    ).fetchone()[0]


def test_replay_uses_client_id_even_when_delivery_timestamp_changes(seeded_db):
    """A lost-response replay is skipped even when its timestamp changes."""
    conn, location_id, user_id = seeded_db
    entitlement = _make_entitlement(conn, location_id, user_id, {"units": 10})
    client_id = f"draw-{uuid.uuid4().hex}"

    first = service.apply_batch(
        conn,
        _founder(),
        [_draw_action(client_id, entitlement.id, _DRAW_AT)],
        logged_by=user_id,
    )[0]
    replay = service.apply_batch(
        conn,
        _founder(),
        [_draw_action(client_id, entitlement.id, _DRAW_AT + timedelta(minutes=5))],
        logged_by=user_id,
    )[0]

    assert first.status == service.STATUS_APPLIED
    assert replay.status == service.STATUS_SKIPPED
    assert _remaining_units(conn, entitlement.id) == 7

    receipts = conn.execute(
        "SELECT action, client_id, legacy_client_id_missing, client_timestamp "
        "FROM sync_log WHERE entity = 'entitlements' AND record_id = %s "
        "AND action IN ('update', 'skip') ORDER BY client_timestamp, id",
        (entitlement.id,),
    ).fetchall()
    assert receipts == [
        ("update", client_id, False, _DRAW_AT),
        ("skip", None, False, _DRAW_AT + timedelta(minutes=5)),
    ]


def test_distinct_client_ids_at_same_timestamp_each_apply(seeded_db):
    """Two genuine same-time actions remain distinct and both decrement."""
    conn, location_id, user_id = seeded_db
    entitlement = _make_entitlement(conn, location_id, user_id, {"units": 10})
    first_id = f"draw-{uuid.uuid4().hex}"
    second_id = f"draw-{uuid.uuid4().hex}"

    results = service.apply_batch(
        conn,
        _founder(),
        [
            _draw_action(first_id, entitlement.id, _DRAW_AT, amount=2),
            _draw_action(second_id, entitlement.id, _DRAW_AT, amount=2),
        ],
        logged_by=user_id,
    )

    assert [result.status for result in results] == [
        service.STATUS_APPLIED,
        service.STATUS_APPLIED,
    ]
    assert _remaining_units(conn, entitlement.id) == 6
    receipts = conn.execute(
        "SELECT entity, record_id, action, client_id, legacy_client_id_missing, "
        "client_timestamp FROM sync_log WHERE client_id = ANY(%s) ORDER BY client_id",
        ([first_id, second_id],),
    ).fetchall()
    assert receipts == sorted(
        [
            ("entitlements", entitlement.id, "update", first_id, False, _DRAW_AT),
            ("entitlements", entitlement.id, "update", second_id, False, _DRAW_AT),
        ],
        key=lambda row: row[3],
    )


def test_concurrent_same_client_id_decrements_once(seeded_db, pg_dsn):
    """The entitlement row lock serialises concurrent deliveries of one action."""
    import psycopg
    from psycopg import sql

    conn, location_id, user_id = seeded_db
    entitlement = _make_entitlement(conn, location_id, user_id, {"units": 10})
    client_id = f"draw-{uuid.uuid4().hex}"
    schema = conn.execute("SELECT current_schema()").fetchone()[0]
    conn.commit()
    first_locked = Event()
    second_attempting = Event()
    release_first = Event()
    second_pid: list[int] = []

    def worker_connection():
        worker = psycopg.connect(pg_dsn)
        worker.execute(
            sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema))
        )
        worker.commit()
        return worker

    def deliver_first():
        worker = worker_connection()
        try:
            # Hold the entitlement lock before entering draw so the second
            # backend is observably blocked at the production lock boundary.
            worker.execute(
                "SELECT id FROM entitlements WHERE id = %s FOR UPDATE",
                (entitlement.id,),
            ).fetchone()
            first_locked.set()
            if not release_first.wait(timeout=10):
                raise TimeoutError("timed out waiting to release the first draw")
            return engine.draw(
                worker,
                entitlement.id,
                3,
                logged_by=user_id,
                now=_DRAW_AT,
                client_id=client_id,
            ).status
        finally:
            worker.close()

    def deliver_second():
        if not first_locked.wait(timeout=10):
            raise TimeoutError("first draw did not acquire the entitlement lock")
        worker = worker_connection()
        try:
            second_pid.append(worker.info.backend_pid)
            second_attempting.set()
            return engine.draw(
                worker,
                entitlement.id,
                3,
                logged_by=user_id,
                now=_DRAW_AT,
                client_id=client_id,
            ).status
        finally:
            worker.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(deliver_first)
        second_future = executor.submit(deliver_second)
        try:
            assert first_locked.wait(timeout=10)
            assert second_attempting.wait(timeout=10)

            # Prove actual overlap rather than relying on thread scheduling:
            # the second backend must be waiting on a PostgreSQL lock while the
            # first transaction owns the entitlement row.
            blocked = False
            deadline = time.monotonic() + 10
            with psycopg.connect(pg_dsn, autocommit=True) as observer:
                while time.monotonic() < deadline:
                    wait_type = observer.execute(
                        "SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s",
                        (second_pid[0],),
                    ).fetchone()
                    if wait_type is not None and wait_type[0] == "Lock":
                        blocked = True
                        break
                    time.sleep(0.05)
            assert blocked, "second draw never blocked on the entitlement row"
        finally:
            release_first.set()

        statuses = [
            first_future.result(timeout=10),
            second_future.result(timeout=10),
        ]

    assert Counter(statuses) == Counter(
        {engine.DRAW_APPLIED: 1, engine.DRAW_SKIPPED: 1}
    )
    assert _remaining_units(conn, entitlement.id) == 7
    assert conn.execute(
        "SELECT entity, record_id, action, client_id, legacy_client_id_missing, "
        "client_timestamp FROM sync_log WHERE client_id = %s",
        (client_id,),
    ).fetchall() == [
        ("entitlements", entitlement.id, "update", client_id, False, _DRAW_AT)
    ]


def test_legacy_receipt_is_claimed_without_second_decrement(seeded_db):
    """A TRUE-marked rollout receipt is upgraded to the stable client identity."""
    conn, location_id, user_id = seeded_db
    entitlement = _make_entitlement(conn, location_id, user_id, {"units": 10})
    client_id = f"draw-{uuid.uuid4().hex}"

    # Simulate a draw completed by an old API revision after migration 010: it
    # decremented the balance but omitted client_id and inherited legacy TRUE.
    conn.execute(
        "UPDATE entitlements SET remaining_units = 7 WHERE id = %s",
        (entitlement.id,),
    )
    conn.execute(
        "INSERT INTO sync_log "
        "(entity, record_id, action, user_id, location_id, client_timestamp) "
        "VALUES ('entitlements', %s, 'update', %s, %s, %s)",
        (entitlement.id, user_id, location_id, _DRAW_AT),
    )
    conn.commit()

    first_replay = engine.draw(
        conn,
        entitlement.id,
        3,
        logged_by=user_id,
        now=_DRAW_AT,
        client_id=client_id,
    )
    later_replay = engine.draw(
        conn,
        entitlement.id,
        3,
        logged_by=user_id,
        now=_DRAW_AT + timedelta(minutes=5),
        client_id=client_id,
    )

    assert first_replay.skipped
    assert later_replay.skipped
    assert _remaining_units(conn, entitlement.id) == 7
    assert conn.execute(
        "SELECT entity, record_id, action, client_id, legacy_client_id_missing, "
        "client_timestamp FROM sync_log WHERE client_id = %s",
        (client_id,),
    ).fetchall() == [
        ("entitlements", entitlement.id, "skip", client_id, False, _DRAW_AT)
    ]
    assert conn.execute(
        "SELECT client_id, client_timestamp FROM sync_log "
        "WHERE entity = 'entitlements' AND record_id = %s AND action = 'skip' "
        "ORDER BY client_timestamp",
        (entitlement.id,),
    ).fetchall() == [
        (client_id, _DRAW_AT),
        (None, _DRAW_AT + timedelta(minutes=5)),
    ]
    assert conn.execute(
        "SELECT count(*) FROM sync_log WHERE entity = 'entitlements' "
        "AND record_id = %s AND action = 'update' "
        "AND legacy_client_id_missing = TRUE AND client_timestamp = %s",
        (entitlement.id, _DRAW_AT),
    ).fetchone()[0] == 1


def test_direct_draw_audit_is_not_a_legacy_fallback_candidate(seeded_db):
    """A direct draw and a same-time sync action are separate operations."""
    conn, location_id, user_id = seeded_db
    entitlement = _make_entitlement(conn, location_id, user_id, {"units": 10})
    client_id = f"draw-{uuid.uuid4().hex}"

    direct = engine.draw(
        conn,
        entitlement.id,
        2,
        logged_by=user_id,
        now=_DRAW_AT,
    )
    synced = engine.draw(
        conn,
        entitlement.id,
        2,
        logged_by=user_id,
        now=_DRAW_AT,
        client_id=client_id,
    )

    assert direct.applied
    assert synced.applied
    assert _remaining_units(conn, entitlement.id) == 6
    rows = conn.execute(
        "SELECT client_id, legacy_client_id_missing FROM sync_log "
        "WHERE entity = 'entitlements' AND record_id = %s "
        "AND action = 'update' AND client_timestamp = %s ORDER BY client_id NULLS FIRST",
        (entitlement.id, _DRAW_AT),
    ).fetchall()
    assert rows == [(None, False), (client_id, False)]


def test_unlimited_draw_records_replay_receipt_without_update_signature(seeded_db):
    """Unlimited draws are applied once and replay through a skip receipt."""
    conn, location_id, user_id = seeded_db
    entitlement = _make_entitlement(conn, location_id, user_id, {})
    client_id = f"draw-{uuid.uuid4().hex}"

    first = engine.draw(
        conn,
        entitlement.id,
        1,
        logged_by=user_id,
        now=_DRAW_AT,
        client_id=client_id,
    )
    replay = engine.draw(
        conn,
        entitlement.id,
        1,
        logged_by=user_id,
        now=_DRAW_AT + timedelta(minutes=5),
        client_id=client_id,
    )

    assert first.applied
    assert replay.skipped
    assert _remaining_units(conn, entitlement.id) is None
    assert conn.execute(
        "SELECT entity, record_id, action, client_id, legacy_client_id_missing, "
        "client_timestamp FROM sync_log WHERE client_id = %s",
        (client_id,),
    ).fetchall() == [
        ("entitlements", entitlement.id, "skip", client_id, False, _DRAW_AT)
    ]
    assert conn.execute(
        "SELECT client_id, client_timestamp FROM sync_log "
        "WHERE entity = 'entitlements' AND record_id = %s AND action = 'skip' "
        "ORDER BY client_timestamp",
        (entitlement.id,),
    ).fetchall() == [
        (client_id, _DRAW_AT),
        (None, _DRAW_AT + timedelta(minutes=5)),
    ]
    assert conn.execute(
        "SELECT count(*) FROM sync_log WHERE entity = 'entitlements' "
        "AND record_id = %s AND action = 'update'",
        (entitlement.id,),
    ).fetchone()[0] == 0
