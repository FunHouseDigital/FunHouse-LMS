"""Property-based test for the Revenue_Reporter (Task 11.2, Req 11).

Property 20 -- the revenue summary equals the scoped integer-cents sum per
product type, and for a manager/facilitator the totals exclude out-of-scope
payments. Requires a reachable PostgreSQL; skips otherwise. Runs a minimum of
100 Hypothesis iterations.
"""

from __future__ import annotations

import uuid

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from funhouse_api.rbac import Scope
from funhouse_api.revenue import reporter
from funhouse_pipeline.db.migrations import run_migrations
from funhouse_pipeline.db.seed import seed

pytestmark = [pytest.mark.db, pytest.mark.property]

_DB_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


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


def _make_school(conn, location_id) -> str:
    return str(
        conn.execute(
            "INSERT INTO schools (name, contract_status, location_id) "
            "VALUES (%s, 'partner', %s) RETURNING id",
            (f"school-{uuid.uuid4().hex[:10]}", location_id),
        ).fetchone()[0]
    )


# Feature: funhouse-api, Property 20: Revenue summary equals the scoped sum per
# product type. For any set of payments, the pay_per_use and subscription stream
# totals each equal the integer-cents sum of the in-scope payments attributable
# to products of that type, and for a manager/facilitator the totals exclude all
# out-of-scope payments.
# Validates: Requirements 11.1, 11.2, 11.4, 11.5
@_DB_SETTINGS
@given(
    payments=st.lists(
        st.tuples(
            st.integers(0, 1),          # location index
            st.integers(0, 1),          # school index
            st.sampled_from(["ppu", "sub"]),
            st.integers(min_value=1, max_value=100_000),
        ),
        min_size=0,
        max_size=15,
    )
)
def test_property_20_scoped_per_type_sums(seeded_db, payments):
    conn, ppu_id, sub_id = seeded_db
    conn.execute("SAVEPOINT ex")
    try:
        locations = [_make_location(conn), _make_location(conn)]
        schools = {loc: [_make_school(conn, loc), _make_school(conn, loc)] for loc in locations}
        product_of = {"ppu": ppu_id, "sub": sub_id}

        # Independent expected accumulators.
        founder_exp = {"ppu": 0, "sub": 0}
        loc0 = locations[0]
        sch0 = schools[loc0][0]
        manager_exp = {"ppu": 0, "sub": 0}
        facilitator_exp = {"ppu": 0, "sub": 0}

        for loc_idx, sch_idx, ptype, amount in payments:
            loc = locations[loc_idx]
            sch = schools[loc][sch_idx]
            player_id = conn.execute(
                "INSERT INTO players (first_name, location_id, school_id) "
                "VALUES (%s, %s, %s) RETURNING id",
                (f"p-{uuid.uuid4().hex[:6]}", loc, sch),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO payments (player_id, product_id, amount_cents, location_id) "
                "VALUES (%s, %s, %s, %s)",
                (player_id, product_of[ptype], amount, loc),
            )
            founder_exp[ptype] += amount
            if loc == loc0:
                manager_exp[ptype] += amount
                if sch == sch0:
                    facilitator_exp[ptype] += amount

        founder = reporter.summary(conn, Scope("founder", None, None))
        assert founder.pay_per_use_cents == founder_exp["ppu"]
        assert founder.subscription_cents == founder_exp["sub"]
        assert founder.school_contracts_cents == 0  # no school-contract product (Req 11.3)

        manager = reporter.summary(conn, Scope("manager", loc0, None))
        assert manager.pay_per_use_cents == manager_exp["ppu"]
        assert manager.subscription_cents == manager_exp["sub"]

        facilitator = reporter.summary(conn, Scope("facilitator", loc0, sch0))
        assert facilitator.pay_per_use_cents == facilitator_exp["ppu"]
        assert facilitator.subscription_cents == facilitator_exp["sub"]
    finally:
        conn.execute("ROLLBACK TO SAVEPOINT ex")
