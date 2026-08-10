"""Example tests for seed correctness (Task 3.3).

Two layers:

* Pure unit checks on the seed data definitions (no database), so the founding
  values are pinned even where no PostgreSQL server exists.
* DB-backed checks asserting each expected location, school (with correct
  ``contract_status``), product (correct ``price_cents`` and ``rules`` JSONB),
  and user (correct ``role``) exists after seeding. These require a reachable
  server and skip automatically otherwise.

Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7
"""

from __future__ import annotations

import pytest

from funhouse_pipeline.db.migrations import run_migrations
from funhouse_pipeline.db.seed import (
    PARTNER_SCHOOLS,
    PROPOSED_SCHOOLS,
    SEED_PRODUCTS,
    SEED_USERS,
    SMITHFIELD_LOCATION,
    seed,
)

# --------------------------------------------------------------------------- #
# Pure unit checks on the seed definitions (no database required).
# --------------------------------------------------------------------------- #


def test_location_row_is_smithfield():
    # Req 2.1
    assert SMITHFIELD_LOCATION == "Smithfield"


def test_partner_and_proposed_school_sets():
    # Req 2.2 / 2.3
    assert set(PARTNER_SCHOOLS) == {"Mofulatshepe", "Relebohile-Sibulele", "Smithfield Primary"}
    assert set(PROPOSED_SCHOOLS) == {"Thabo-Vuyo", "Naledi", "Rouxville Primary", "JB Tyu"}


def test_product_definitions_match_design():
    # Req 2.4 / 2.5 / 2.6
    by_name = {p.name: p for p in SEED_PRODUCTS}
    assert by_name["PayPerUse-20min"].type == "pay_per_use"
    assert by_name["PayPerUse-20min"].price_cents == 1000
    assert by_name["PayPerUse-1hr"].price_cents == 3000
    assert by_name["PayPerUse-2hr"].price_cents == 5000

    subscription = by_name["Subscription"]
    assert subscription.type == "subscription"
    assert subscription.price_cents == 35000
    assert subscription.rules == {"members": 4, "hours_per_week": 2, "min_term_months": 3}

    holiday = by_name["Holiday Special"]
    assert holiday.type == "once_off_pass"
    assert holiday.price_cents == 25000
    assert holiday.rules == {
        "hours_per_week": 3,
        "reset": "sunday",
        "rollover": False,
        "fixed_window": True,
    }


def test_seed_users_have_expected_roles():
    # Req 2.7
    roles = {u.name: u.role for u in SEED_USERS}
    assert roles == {
        "Aya": "founder",
        "Loyiso": "manager",
        "Facilitator": "facilitator",
    }


# --------------------------------------------------------------------------- #
# DB-backed checks: the seeded rows exist with the correct values.
# --------------------------------------------------------------------------- #

pytestmark_db = pytest.mark.db


@pytest.fixture
def seeded_db(db_connection):
    """Migrate then seed the isolated schema; return the connection."""
    run_migrations(db_connection)
    seed(db_connection)
    return db_connection


@pytest.mark.db
def test_smithfield_location_seeded(seeded_db):
    # Req 2.1
    count = seeded_db.execute(
        "SELECT count(*) FROM locations WHERE name = %s", (SMITHFIELD_LOCATION,)
    ).fetchone()[0]
    assert count == 1


@pytest.mark.db
def test_partner_schools_seeded_with_status(seeded_db):
    # Req 2.2
    for name in PARTNER_SCHOOLS:
        status = seeded_db.execute(
            "SELECT contract_status FROM schools WHERE name = %s", (name,)
        ).fetchone()
        assert status is not None, f"partner school {name} missing"
        assert status[0] == "partner"


@pytest.mark.db
def test_proposed_schools_seeded_with_status(seeded_db):
    # Req 2.3
    for name in PROPOSED_SCHOOLS:
        status = seeded_db.execute(
            "SELECT contract_status FROM schools WHERE name = %s", (name,)
        ).fetchone()
        assert status is not None, f"proposed school {name} missing"
        assert status[0] == "proposed"


@pytest.mark.db
def test_products_seeded_with_prices_and_rules(seeded_db):
    # Req 2.4 / 2.5 / 2.6
    for product in SEED_PRODUCTS:
        row = seeded_db.execute(
            "SELECT type, price_cents, rules FROM products WHERE name = %s", (product.name,)
        ).fetchone()
        assert row is not None, f"product {product.name} missing"
        ptype, price_cents, rules = row
        assert ptype == product.type
        assert price_cents == product.price_cents
        # psycopg decodes JSONB into a Python dict.
        assert rules == dict(product.rules)


@pytest.mark.db
def test_users_seeded_with_roles(seeded_db):
    # Req 2.7
    for user in SEED_USERS:
        row = seeded_db.execute(
            "SELECT role FROM users WHERE name = %s", (user.name,)
        ).fetchone()
        assert row is not None, f"user {user.name} missing"
        assert row[0] == user.role


@pytest.mark.db
def test_all_non_location_rows_reference_smithfield(seeded_db):
    # Every seeded school/product/user resolves location_id to Smithfield.
    loc_id = seeded_db.execute(
        "SELECT id FROM locations WHERE name = %s", (SMITHFIELD_LOCATION,)
    ).fetchone()[0]
    for table in ("schools", "products", "users"):
        bad = seeded_db.execute(
            f"SELECT count(*) FROM {table} WHERE location_id <> %s", (loc_id,)
        ).fetchone()[0]
        assert bad == 0, f"{table} has rows not referencing the Smithfield location"
