"""Property-based tests for the players service (Task 8, Req 6, 8.7).

Implements three design correctness properties:

* Property 14 -- player registration is deduplicated (Req 6.5).
* Property 13 -- the consents ledger is append-only / monotonic (Req 6.3, 6.4,
  14.4).
* Property 15 -- player history is complete within scope and leaks nothing
  outside it (Req 6.7, 8.7).

All tests require a reachable PostgreSQL and skip otherwise. Each runs a minimum
of 100 Hypothesis iterations. The reused Phase 0 dedup/consent/audit logic runs
for real -- nothing deterministic is mocked.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from funhouse_api.players import service
from funhouse_api.rbac import Scope
from funhouse_pipeline.db.migrations import run_migrations
from funhouse_pipeline.db.seed import seed
from funhouse_pipeline.load.consent import append_consent, revoke_consent

pytestmark = [pytest.mark.db, pytest.mark.property]

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
    aya = db_connection.execute(
        "SELECT id FROM users WHERE name = 'Aya'"
    ).fetchone()[0]
    return db_connection, loc, aya


def _manager_scope(location_id) -> Scope:
    return Scope(role="manager", location_id=str(location_id), school_id=None)


# --------------------------------------------------------------------------- #
# Property 14: player registration is deduplicated (Req 6.5)
# --------------------------------------------------------------------------- #


# Feature: funhouse-api, Property 14: Player registration is deduplicated. For
# any two registration requests that resolve to the same Phase 0 dedup key,
# exactly one players row exists afterward (the second resolves to the existing
# row).
# Validates: Requirements 6.5
@_DB_SETTINGS
@given(
    first=st.text(alphabet=st.characters(whitelist_categories=("Ll", "Lu")), min_size=1, max_size=12),
    last=st.text(alphabet=st.characters(whitelist_categories=("Ll", "Lu")), min_size=1, max_size=12),
    birth_year=st.integers(min_value=2005, max_value=2018),
)
def test_property_14_registration_deduplicated(seeded_db, first, last, birth_year):
    conn, loc, aya = seeded_db
    scope = _manager_scope(loc)
    # Make the identity unique per example so accumulation across examples does
    # not interfere (dedup is asserted within this example's identity).
    tag = uuid.uuid4().hex[:8]
    first_u = f"{first}{tag}"
    birth = date(birth_year, 6, 1)
    consents = [service.ConsentInput(consent_type="data_processing")]

    p1 = service.register_player(
        conn, scope, logged_by=aya, first_name=first_u, last_name=last,
        birth_date=birth, consents=consents,
    )
    p2 = service.register_player(
        conn, scope, logged_by=aya, first_name=first_u, last_name=last,
        birth_date=birth, consents=consents,
    )

    # Both registrations resolve to the same row (Req 6.5).
    assert str(p1.id) == str(p2.id)

    dedup_key = f"{first_u.strip().lower()}|{last.strip().lower()}|{birth.isoformat()}"
    count = conn.execute(
        "SELECT count(*) FROM players WHERE dedup_key = %s", (dedup_key,)
    ).fetchone()[0]
    assert count == 1


# --------------------------------------------------------------------------- #
# Property 13: consents ledger is append-only / monotonic (Req 6.3, 6.4, 14.4)
# --------------------------------------------------------------------------- #


# Feature: funhouse-api, Property 13: Consents ledger is append-only (count never
# decreases). For any sequence of consent grants and revocations, the number of
# consents rows is monotonically non-decreasing (a revocation appends a new row
# and never updates or deletes an existing one).
# Validates: Requirements 6.3, 6.4, 14.4
@_DB_SETTINGS
@given(actions=st.lists(st.booleans(), min_size=1, max_size=20))
def test_property_13_consents_append_only(seeded_db, actions):
    conn, loc, aya = seeded_db
    scope = _manager_scope(loc)

    # Register a fresh player (with an initial consent) unique to this example.
    player = service.register_player(
        conn, scope, logged_by=aya,
        first_name=f"C{uuid.uuid4().hex[:10]}",
        consents=[service.ConsentInput(consent_type="data_processing")],
    )
    player_id = player.id

    def _count() -> int:
        return conn.execute(
            "SELECT count(*) FROM consents WHERE player_id = %s", (player_id,)
        ).fetchone()[0]

    prev = _count()
    assert prev == 1  # the initial consent from registration

    for grant in actions:
        if grant:
            append_consent(
                conn, player_id=player_id, consent_type="photo", granted=True,
                location_id=loc, captured_by_user_id=aya,
            )
        else:
            revoke_consent(
                conn, player_id=player_id, consent_type="photo",
                location_id=loc, captured_by_user_id=aya,
            )
        current = _count()
        # Monotonically non-decreasing, and each action appends exactly one row.
        assert current == prev + 1
        prev = current


# --------------------------------------------------------------------------- #
# Property 15: history complete within scope, leaks nothing (Req 6.7, 8.7)
# --------------------------------------------------------------------------- #


def _make_location(conn) -> str:
    return str(
        conn.execute(
            "INSERT INTO locations (name) VALUES (%s) RETURNING id",
            (f"loc-{uuid.uuid4().hex[:10]}",),
        ).fetchone()[0]
    )


def _make_player(conn, location_id, school_id=None) -> str:
    return str(
        conn.execute(
            "INSERT INTO players (first_name, location_id, school_id) "
            "VALUES (%s, %s, %s) RETURNING id",
            (f"p-{uuid.uuid4().hex[:6]}", location_id, school_id),
        ).fetchone()[0]
    )


# Feature: funhouse-api, Property 15: Player history is complete within scope and
# leaks nothing outside it. For any player, the history response includes that
# player's in-scope sessions, payments, and entitlement draws (each draw carrying
# its acting user and timestamp) and contains no record outside the caller's
# scope.
# Validates: Requirements 6.7, 8.7
@_DB_SETTINGS
@given(
    n_in=st.integers(min_value=0, max_value=4),
    n_out=st.integers(min_value=0, max_value=4),
)
def test_property_15_history_scope_containment(seeded_db, n_in, n_out):
    conn, seeded_loc, aya = seeded_db
    conn.execute("SAVEPOINT ex")
    try:
        loc_in = _make_location(conn)
        loc_out = _make_location(conn)
        # The player lives at loc_in (manager-in scope).
        player_id = _make_player(conn, loc_in)
        product_id = str(
            conn.execute("SELECT id FROM products LIMIT 1").fetchone()[0]
        )

        in_sessions, in_payments, in_draws = set(), set(), set()

        def _add_records(location, count, *, in_scope):
            for _ in range(count):
                sid = str(
                    conn.execute(
                        "INSERT INTO sessions (player_id, session_type, "
                        "location_id) VALUES (%s, 'lounge', %s) RETURNING id",
                        (player_id, location),
                    ).fetchone()[0]
                )
                pid = str(
                    conn.execute(
                        "INSERT INTO payments (player_id, amount_cents, "
                        "location_id) VALUES (%s, 100, %s) RETURNING id",
                        (player_id, location),
                    ).fetchone()[0]
                )
                eid = str(
                    conn.execute(
                        "INSERT INTO entitlements (player_id, product_id, "
                        "remaining_units, location_id) VALUES (%s, %s, 10, %s) "
                        "RETURNING id",
                        (player_id, product_id, location),
                    ).fetchone()[0]
                )
                conn.execute(
                    "INSERT INTO sync_log (entity, record_id, action, user_id, "
                    "location_id, server_timestamp) VALUES "
                    "('entitlements', %s, 'update', %s, %s, now())",
                    (eid, aya, location),
                )
                if in_scope:
                    in_sessions.add(sid)
                    in_payments.add(pid)
                    in_draws.add(eid)

        _add_records(loc_in, n_in, in_scope=True)
        _add_records(loc_out, n_out, in_scope=False)

        scope = Scope(role="manager", location_id=loc_in, school_id=None)
        history = service.player_history(conn, scope, player_id)

        got_sessions = {str(s["id"]) for s in history.sessions}
        got_payments = {str(p["id"]) for p in history.payments}
        got_draws = {str(d["entitlement_id"]) for d in history.entitlement_draws}

        # Complete within scope ...
        assert got_sessions == in_sessions
        assert got_payments == in_payments
        assert got_draws == in_draws
        # ... and each draw carries its acting user + timestamp (Req 8.7).
        for d in history.entitlement_draws:
            assert str(d["logged_by"]) == str(aya)
            assert d["server_timestamp"] is not None

        # Founder sees everything (no leakage the other way for scoped roles).
        founder = Scope(role="founder", location_id=None, school_id=None)
        all_hist = service.player_history(conn, founder, player_id)
        assert len(all_hist.sessions) == n_in + n_out
    finally:
        conn.execute("ROLLBACK TO SAVEPOINT ex")
