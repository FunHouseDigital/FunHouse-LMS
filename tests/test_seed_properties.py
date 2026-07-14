"""Property-based test for idempotent reference-data seeding (Task 3.2).

Implements design Property 4. Requires a reachable PostgreSQL server and is
skipped automatically otherwise (see conftest's ``db_connection`` fixture). The
property runs a minimum of 100 Hypothesis iterations, per the design's Testing
Strategy.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from funhouse_pipeline.db.migrations import run_migrations
from funhouse_pipeline.db.seed import (
    PARTNER_SCHOOLS,
    PROPOSED_SCHOOLS,
    SEED_PRODUCTS,
    SEED_USERS,
    TOTAL_SEED_ROWS,
    seed,
)

pytestmark = [pytest.mark.db, pytest.mark.property]

_DB_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# The rows that may be independently removed and re-seeded. The Smithfield
# location is intentionally excluded because every other row references it via
# location_id, so it must remain present as the FK parent.
_DELETABLE_ROWS: tuple[tuple[str, str], ...] = (
    *[("schools", name) for name in (*PARTNER_SCHOOLS, *PROPOSED_SCHOOLS)],
    *[("products", p.name) for p in SEED_PRODUCTS],
    *[("users", u.name) for u in SEED_USERS],
)


@pytest.fixture
def migrated_db(db_connection):
    """Apply all migrations once against the isolated schema; return the conn."""
    run_migrations(db_connection)
    return db_connection


def _snapshot(conn, table: str) -> dict[str, tuple]:
    """Map natural identity (name) -> (id, created_at) for every row in table."""
    rows = conn.execute(f"SELECT name, id, created_at FROM {table}").fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def _table_count(conn, table: str) -> int:
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


# Feature: phase0-data-foundation, Property 4: Seeding is idempotent. For any
# subset of seed rows already present in the database, re-running the seed step
# creates no duplicate rows and leaves the existing rows unchanged.
# Validates: Requirements 2.8
@_DB_SETTINGS
@given(present_mask=st.lists(st.booleans(), min_size=len(_DELETABLE_ROWS), max_size=len(_DELETABLE_ROWS)))
def test_property_4_seeding_is_idempotent(migrated_db, present_mask):
    conn = migrated_db

    # Establish a full seed, then remove a Hypothesis-chosen subset so that an
    # arbitrary subset of seed rows is "already present" before we re-seed.
    seed(conn)

    for keep_present, (table, name) in zip(present_mask, _DELETABLE_ROWS):
        if not keep_present:
            conn.execute(f"DELETE FROM {table} WHERE name = %s", (name,))
    conn.commit()

    # Snapshot the rows that remain present; these must be untouched by re-seed.
    before = {t: _snapshot(conn, t) for t in ("locations", "schools", "products", "users")}

    # Re-run the seed: it must fill only the gaps and touch nothing present.
    result = seed(conn)

    # No duplicates: every table converges to exactly the full expected count.
    assert _table_count(conn, "locations") == 1
    assert _table_count(conn, "schools") == len(PARTNER_SCHOOLS) + len(PROPOSED_SCHOOLS)
    assert _table_count(conn, "products") == len(SEED_PRODUCTS)
    assert _table_count(conn, "users") == len(SEED_USERS)

    total_rows = sum(_table_count(conn, t) for t in ("locations", "schools", "products", "users"))
    assert total_rows == TOTAL_SEED_ROWS

    # Existing rows unchanged: same id and created_at as before the re-seed.
    after = {t: _snapshot(conn, t) for t in ("locations", "schools", "products", "users")}
    for table, snap in before.items():
        for name, identity in snap.items():
            assert after[table][name] == identity, (
                f"re-seed altered existing row {table}:{name}"
            )

    # The re-seed must skip every row that was still present.
    reinserted = {(r.table, r.identity) for r in result.inserted()}
    for keep_present, (table, name) in zip(present_mask, _DELETABLE_ROWS):
        if keep_present:
            assert (table, name) not in reinserted
