"""Property-based test for the Alerts_Engine (Task 12.2, Req 12).

Property 21 -- alerts are deterministic (computing twice yields identical
results, no AI/model call) and each alert type is present exactly when its
boundary condition holds:

* no-recent-session: a session strictly older than 7 days (a session exactly
  7 days old still counts as recent).
* entitlement-expiring: ``valid_to`` within ``[now_date, now_date + horizon]``.
* subscription-due: a subscription entitlement whose ``valid_to`` is on/before
  ``now_date``.
* unsynced-device: last sync strictly older than 5 days.

Requires a reachable PostgreSQL; skips otherwise. Runs a minimum of 100
Hypothesis iterations. Each example is isolated with a SAVEPOINT rollback (the
engine is read-only).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from funhouse_api.alerts import engine
from funhouse_api.rbac import Scope
from funhouse_pipeline.db.migrations import run_migrations
from funhouse_pipeline.db.seed import seed

pytestmark = [pytest.mark.db, pytest.mark.property]

_DB_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

_NOW = datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc)
_HORIZON = 7


@pytest.fixture
def seeded_db(db_connection):
    run_migrations(db_connection)
    seed(db_connection)
    ppu = db_connection.execute(
        "SELECT id FROM products WHERE name = 'PayPerUse-1hr'"
    ).fetchone()[0]
    sub = db_connection.execute(
        "SELECT id FROM products WHERE name = 'Subscription'"
    ).fetchone()[0]
    return db_connection, str(ppu), str(sub)


def _make_location(conn) -> str:
    return str(
        conn.execute(
            "INSERT INTO locations (name) VALUES (%s) RETURNING id",
            (f"loc-{uuid.uuid4().hex[:10]}",),
        ).fetchone()[0]
    )


def _make_player(conn, loc) -> str:
    return str(
        conn.execute(
            "INSERT INTO players (first_name, location_id) VALUES (%s, %s) RETURNING id",
            (f"p-{uuid.uuid4().hex[:6]}", loc),
        ).fetchone()[0]
    )


# Feature: funhouse-api, Property 21: Alerts are deterministic and honor their
# rule boundaries. For any dataset, computing alerts twice yields identical
# results (no AI, pure conditional logic), and an alert of each type is present
# exactly when its boundary condition holds.
# Validates: Requirements 12.1, 12.2, 12.3, 12.4, 12.5
@_DB_SETTINGS
@given(
    session_age=st.integers(min_value=0, max_value=20),
    vt_offset_exp=st.integers(min_value=-5, max_value=15),
    vt_offset_sub=st.integers(min_value=-10, max_value=15),
    dev_age=st.integers(min_value=0, max_value=15),
)
def test_property_21_alerts_deterministic_and_boundary_correct(
    seeded_db, session_age, vt_offset_exp, vt_offset_sub, dev_age
):
    conn, ppu_id, sub_id = seeded_db
    conn.execute("SAVEPOINT ex")
    try:
        loc = _make_location(conn)
        now_date = _NOW.date()

        # Rule 1 subject: a player whose only session is `session_age` days old.
        p_session = _make_player(conn, loc)
        conn.execute(
            "INSERT INTO sessions (player_id, session_type, started_at, location_id) "
            "VALUES (%s, 'lounge', %s, %s)",
            (p_session, _NOW - timedelta(days=session_age), loc),
        )

        # Rule 2 subject: a (non-subscription) entitlement expiring in vt_offset_exp days.
        p_exp = _make_player(conn, loc)
        e_exp = str(
            conn.execute(
                "INSERT INTO entitlements (player_id, product_id, status, "
                "remaining_units, valid_to, location_id) "
                "VALUES (%s, %s, 'active', 10, %s, %s) RETURNING id",
                (p_exp, ppu_id, now_date + timedelta(days=vt_offset_exp), loc),
            ).fetchone()[0]
        )

        # Rule 3 subject: a subscription entitlement with valid_to in vt_offset_sub days.
        p_sub = _make_player(conn, loc)
        e_sub = str(
            conn.execute(
                "INSERT INTO entitlements (player_id, product_id, status, "
                "remaining_units, valid_to, location_id) "
                "VALUES (%s, %s, 'active', 10, %s, %s) RETURNING id",
                (p_sub, sub_id, now_date + timedelta(days=vt_offset_sub), loc),
            ).fetchone()[0]
        )

        # Rule 4 subject: a device whose last sync is dev_age days old.
        device = f"dev-{uuid.uuid4().hex[:8]}"
        rec = str(
            conn.execute(
                "INSERT INTO entitlements (player_id, product_id, status, "
                "remaining_units, location_id) VALUES (%s, %s, 'active', 1, %s) "
                "RETURNING id",
                (p_sub, ppu_id, loc),
            ).fetchone()[0]
        )
        conn.execute(
            "INSERT INTO sync_log (entity, record_id, action, device_id, "
            "location_id, server_timestamp) VALUES "
            "('entitlements', %s, 'insert', %s, %s, %s)",
            (rec, device, loc, _NOW - timedelta(days=dev_age)),
        )

        scope = Scope("founder", None, None)
        first = engine.alerts(conn, scope, now=_NOW, expiry_horizon_days=_HORIZON)
        second = engine.alerts(conn, scope, now=_NOW, expiry_horizon_days=_HORIZON)

        # Determinism (Req 12.1).
        assert first == second

        pairs = {(a.type, a.subject_id) for a in first}

        # Rule 1 boundary: recent iff session within last 7 days.
        assert (
            (engine.ALERT_NO_RECENT_SESSION, p_session) in pairs
        ) == (session_age > 7)

        # Rule 2 boundary: expiring iff 0 <= offset <= horizon.
        assert (
            (engine.ALERT_ENTITLEMENT_EXPIRING, e_exp) in pairs
        ) == (0 <= vt_offset_exp <= _HORIZON)

        # Rule 3 boundary: subscription due iff valid_to on/before now.
        assert (
            (engine.ALERT_SUBSCRIPTION_DUE, e_sub) in pairs
        ) == (vt_offset_sub <= 0)

        # Rule 4 boundary: unsynced iff last sync strictly older than 5 days.
        assert (
            (engine.ALERT_UNSYNCED_DEVICE, device) in pairs
        ) == (dev_age > 5)
    finally:
        conn.execute("ROLLBACK TO SAVEPOINT ex")
