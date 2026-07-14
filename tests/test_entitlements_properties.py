"""Property-based tests for the Entitlement_Engine (Task 6, Req 8, 9).

Implements the four design correctness properties for entitlements:

* Property 18 -- creation derives units/window from product rules (Req 8.1).
* Property 16 -- unit conservation on draw (Req 8.2, 8.4, 8.5).
* Property 17 -- decrement + digital signature are atomic and paired (Req 8.3,
  8.8, 8.9).
* Property 19 -- recurring reset restores the allowance with no rollover,
  computed deterministically and applied before the draw (Req 9).

All tests need a reachable PostgreSQL and skip automatically otherwise (see the
``db_connection`` fixture in ``conftest.py``). Each runs a minimum of 100
Hypothesis iterations per the design's Testing Strategy. The reused Phase 0
audit/POPIA logic runs for real -- nothing deterministic is mocked. Property 17's
signature-failure path is exercised by injecting a failing audit callable that
raises :class:`engine.SignatureAppendError`, which is the same rollback trigger
the production code reacts to.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from funhouse_api.entitlements import engine
from funhouse_api.rbac import Scope
from funhouse_pipeline.db.migrations import run_migrations
from funhouse_pipeline.db.seed import seed

pytestmark = [pytest.mark.db, pytest.mark.property]

_DB_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

_WEEKDAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
# 2024-01-01 is a Monday; used as a weekday anchor for building period dates.
_MONDAY_ANCHOR = date(2024, 1, 1)


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


def _make_product(conn, location_id, rules, ptype="once_off_pass"):
    """Insert a product with a unique name and return its id."""
    name = f"prod-{uuid.uuid4().hex}"
    pid = conn.execute(
        "INSERT INTO products (name, type, price_cents, rules, location_id) "
        "VALUES (%s, %s, %s, %s::jsonb, %s) RETURNING id",
        (name, ptype, 1000, _json(rules), location_id),
    ).fetchone()[0]
    conn.commit()
    return pid


def _json(obj):
    import json

    return json.dumps(obj)


def _make_player(conn, location_id, school_id=None):
    pid = conn.execute(
        "INSERT INTO players (first_name, consent_status, location_id, school_id) "
        "VALUES (%s, 'pending', %s, %s) RETURNING id",
        (f"P{uuid.uuid4().hex[:8]}", location_id, school_id),
    ).fetchone()[0]
    conn.commit()
    return pid


def _update_count(conn, entitlement_id):
    """Count digital-signature (update) sync_log entries for an entitlement."""
    return conn.execute(
        "SELECT count(*) FROM sync_log WHERE entity = 'entitlements' "
        "AND record_id = %s AND action = 'update'",
        (entitlement_id,),
    ).fetchone()[0]


def _independent_most_recent_weekday(d: date, weekday_name: str) -> date:
    """Independent reference: walk back day-by-day to the named weekday."""
    target = _WEEKDAY_NAMES.index(weekday_name)
    cur = d
    while cur.weekday() != target:
        cur -= timedelta(days=1)
    return cur


# --------------------------------------------------------------------------- #
# Property 18: creation derives units and window from product rules (Req 8.1)
# --------------------------------------------------------------------------- #

_rules_strategy = st.one_of(
    # Recurring weekly with a named reset day.
    st.builds(
        lambda h, day, fw: {"hours_per_week": h, "reset": day, "rollover": False, "fixed_window": fw},
        st.integers(min_value=1, max_value=8),
        st.sampled_from(_WEEKDAY_NAMES),
        st.booleans(),
    ),
    # Subscription-like: weekly allowance + minimum term, no explicit reset day.
    st.builds(
        lambda h, m: {"members": 4, "hours_per_week": h, "min_term_months": m},
        st.integers(min_value=1, max_value=6),
        st.integers(min_value=1, max_value=12),
    ),
    # Non-recurring discrete counts.
    st.builds(lambda n: {"units": n}, st.integers(min_value=0, max_value=500)),
    st.builds(lambda n: {"sessions": n}, st.integers(min_value=1, max_value=50)),
)


# Feature: funhouse-api, Property 18: Entitlement creation derives units and
# window from product rules. For any product, creating an entitlement yields
# remaining_units and a validity window equal to the deterministic function of
# that product's rules.
# Validates: Requirements 8.1
@_DB_SETTINGS
@given(rules=_rules_strategy, now_date=st.dates(min_value=date(2020, 1, 1), max_value=date(2030, 12, 31)))
def test_property_18_creation_derives_units_and_window(seeded_db, rules, now_date):
    conn, loc, aya = seeded_db
    now = datetime(now_date.year, now_date.month, now_date.day, 12, tzinfo=timezone.utc)
    product_id = _make_product(conn, loc, rules)
    player_id = _make_player(conn, loc)

    created = engine.create_entitlement(
        conn,
        player_id=player_id,
        product_id=product_id,
        location_id=loc,
        logged_by=aya,
        now=now,
    )

    # remaining_units follows the units convention (hours*60 / count / unlimited).
    if "hours_per_week" in rules:
        expected_units = rules["hours_per_week"] * 60
    elif "units" in rules:
        expected_units = rules["units"]
    elif "sessions" in rules:
        expected_units = rules["sessions"]
    else:
        expected_units = None
    assert created.remaining_units == expected_units

    recurring = ("reset" in rules) or ("hours_per_week" in rules)
    if recurring:
        reset_day = rules.get("reset", "monday")
        expected_from = _independent_most_recent_weekday(now_date, reset_day)
        assert created.valid_from == expected_from
        assert created.valid_from.weekday() == _WEEKDAY_NAMES.index(reset_day)
        assert 0 <= (now_date - created.valid_from).days <= 6
    else:
        assert created.valid_from == now_date

    # Validity window: term month end, fixed weekly window, or open-ended.
    if "min_term_months" in rules:
        assert created.valid_to is not None
        assert created.valid_to > created.valid_from
    elif recurring and rules.get("fixed_window"):
        assert created.valid_to == created.valid_from + timedelta(days=7)
    elif not recurring:
        assert created.valid_to is None

    # Determinism: the pure derivation is repeatable for the same rules + time.
    w1 = engine.derive_window(rules, now)
    w2 = engine.derive_window(rules, now)
    assert w1 == w2


# --------------------------------------------------------------------------- #
# Property 16: entitlement units are conserved (Req 8.2, 8.4, 8.5)
# --------------------------------------------------------------------------- #


# Feature: funhouse-api, Property 16: Entitlement units are conserved. A draw
# succeeds only when the entitlement is active and remaining_units >= amount, in
# which case remaining_units decreases by exactly amount; otherwise the draw is
# rejected and remaining_units is unchanged; remaining_units is never negative.
# Validates: Requirements 8.2, 8.4, 8.5
@_DB_SETTINGS
@given(
    initial=st.integers(min_value=0, max_value=500),
    amount=st.integers(min_value=1, max_value=600),
    status=st.sampled_from(["active", "expired", "cancelled"]),
)
def test_property_16_unit_conservation(seeded_db, initial, amount, status):
    conn, loc, aya = seeded_db
    # Non-recurring product so no reset interferes with the accounting.
    product_id = _make_product(conn, loc, {"units": initial})
    player_id = _make_player(conn, loc)
    created = engine.create_entitlement(
        conn, player_id=player_id, product_id=product_id, location_id=loc,
        logged_by=aya, now=datetime(2024, 6, 1, 12, tzinfo=timezone.utc),
    )
    assert created.remaining_units == initial

    if status != "active":
        conn.execute(
            "UPDATE entitlements SET status = %s WHERE id = %s", (status, created.id)
        )
        conn.commit()

    result = engine.draw(conn, created.id, amount, logged_by=aya,
                         now=datetime(2024, 6, 2, 12, tzinfo=timezone.utc))

    stored = conn.execute(
        "SELECT remaining_units FROM entitlements WHERE id = %s", (created.id,)
    ).fetchone()[0]

    should_succeed = status == "active" and initial >= amount
    if should_succeed:
        assert result.applied
        assert result.remaining_units == initial - amount
        assert stored == initial - amount
    else:
        assert not result.applied
        assert stored == initial  # unchanged
    # Never negative.
    assert stored >= 0


# --------------------------------------------------------------------------- #
# Property 17: decrement + digital signature are atomic and paired (Req 8.3, 8.8, 8.9)
# --------------------------------------------------------------------------- #


# Feature: funhouse-api, Property 17: A decrement and its digital signature are
# atomic and paired. A sync_log update entry recording the acting user and a
# server timestamp is written if and only if remaining_units was actually
# decremented; if the signature cannot be recorded the decrement is rolled back.
# Validates: Requirements 8.3, 8.8, 8.9
@_DB_SETTINGS
@given(
    initial=st.integers(min_value=0, max_value=300),
    amount=st.integers(min_value=1, max_value=300),
    fail_signature=st.booleans(),
)
def test_property_17_decrement_signature_atomic_and_paired(
    seeded_db, initial, amount, fail_signature
):
    conn, loc, aya = seeded_db
    product_id = _make_product(conn, loc, {"units": initial})
    player_id = _make_player(conn, loc)
    created = engine.create_entitlement(
        conn, player_id=player_id, product_id=product_id, location_id=loc,
        logged_by=aya, now=datetime(2024, 6, 1, 12, tzinfo=timezone.utc),
    )

    before_updates = _update_count(conn, created.id)

    def _failing_audit(*args, **kwargs):
        raise engine.SignatureAppendError("injected signature failure")

    result = engine.draw(
        conn, created.id, amount, logged_by=aya,
        now=datetime(2024, 6, 2, 12, tzinfo=timezone.utc),
        audit_append=_failing_audit if fail_signature else None,
    )

    stored = conn.execute(
        "SELECT remaining_units FROM entitlements WHERE id = %s", (created.id,)
    ).fetchone()[0]
    after_updates = _update_count(conn, created.id)
    decremented = stored == initial - amount
    signature_written = after_updates == before_updates + 1

    # The signature is written IFF the units were actually decremented.
    assert signature_written == decremented

    if fail_signature:
        # A failing signature only matters once the draw reaches the decrement
        # step (i.e. the entitlement is active with sufficient units). In every
        # fail case the units are left unchanged and no signature is written.
        assert not result.applied
        assert stored == initial
        assert not signature_written
        if initial >= amount:
            # Would have decremented -> signature failed -> decrement rolled back.
            assert result.reason == engine.REASON_SIGNATURE_FAILED
        else:
            # Rejected for insufficient units before the signature was attempted.
            assert result.reason == engine.REASON_INSUFFICIENT_UNITS
    elif initial >= amount:
        # Clean success -> decremented and signature paired.
        assert result.applied
        assert decremented
        assert signature_written
    else:
        # Insufficient units -> rejected, unchanged, no signature.
        assert not result.applied
        assert stored == initial
        assert not signature_written


# --------------------------------------------------------------------------- #
# Property 19: recurring reset restores allowance, no rollover, deterministic,
# applied before the draw (Req 9)
# --------------------------------------------------------------------------- #


# Feature: funhouse-api, Property 19: Recurring reset restores the allowance with
# no rollover, computed deterministically. For any recurring entitlement
# evaluated at a time in a later period, the remaining units reset to exactly the
# product's per-period allowance (unused prior-period units discarded), the reset
# is applied before any draw is computed, and the period boundary is a pure,
# repeatable function of the product rules and the current time.
# Validates: Requirements 9.1, 9.2, 9.3, 9.4
@_DB_SETTINGS
@given(
    hours=st.integers(min_value=1, max_value=8),
    weekday=st.sampled_from(_WEEKDAY_NAMES),
    weeks_later=st.integers(min_value=1, max_value=52),
    prior_units=st.integers(min_value=0, max_value=1000),
    draw_amount=st.integers(min_value=1, max_value=60),
)
def test_property_19_recurring_reset(
    seeded_db, hours, weekday, weeks_later, prior_units, draw_amount
):
    conn, loc, aya = seeded_db
    allowance = hours * 60
    rules = {"hours_per_week": hours, "reset": weekday, "rollover": False}

    # Build a period-start date on the reset weekday, and a `now` exactly
    # `weeks_later` whole weeks after it (same weekday -> guaranteed new period).
    target = _WEEKDAY_NAMES.index(weekday)
    old_period_start = _MONDAY_ANCHOR + timedelta(days=target)  # weekday == target
    assert old_period_start.weekday() == target
    new_period_start = old_period_start + timedelta(weeks=weeks_later)
    now = datetime(new_period_start.year, new_period_start.month,
                   new_period_start.day, 12, tzinfo=timezone.utc)

    # (a) Pure reset decision: resets to exactly the allowance, no rollover.
    ent = {"remaining_units": prior_units, "valid_from": old_period_start}
    outcome = engine.reset_if_new_period(ent, rules, now)
    assert outcome.changed is True
    assert outcome.remaining_units == allowance  # prior_units discarded
    assert outcome.valid_from == new_period_start

    # (b) Determinism: the boundary is a pure, repeatable function.
    assert engine.period_start(rules, now) == engine.period_start(rules, now)
    assert engine.period_start(rules, now) == new_period_start

    # (c) Applied before the draw: an entitlement stranded at low units in the
    # old period is reset first, so a draw within the allowance succeeds.
    product_id = _make_product(conn, loc, rules, ptype="subscription")
    player_id = _make_player(conn, loc)
    created = engine.create_entitlement(
        conn, player_id=player_id, product_id=product_id, location_id=loc,
        logged_by=aya,
        now=datetime(old_period_start.year, old_period_start.month,
                     old_period_start.day, 12, tzinfo=timezone.utc),
    )
    # Strand the entitlement in the old period with few/zero units.
    conn.execute(
        "UPDATE entitlements SET remaining_units = %s, valid_from = %s WHERE id = %s",
        (min(prior_units, 5), old_period_start, created.id),
    )
    conn.commit()

    result = engine.draw(conn, created.id, draw_amount, logged_by=aya, now=now)
    assert result.applied  # reset happened before the draw -> allowance available
    assert result.remaining_units == allowance - draw_amount

    stored_from = conn.execute(
        "SELECT valid_from FROM entitlements WHERE id = %s", (created.id,)
    ).fetchone()[0]
    assert stored_from == new_period_start
