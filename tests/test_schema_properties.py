"""Property-based tests for the PostgreSQL schema and migration runner.

These implement design Properties 1-3 (Task 2 sub-tasks 2.4-2.6). They require a
reachable PostgreSQL server and are skipped automatically otherwise (see
conftest's ``db_connection`` fixture). Each property runs a minimum of 100
Hypothesis iterations, per the design's Testing Strategy.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from funhouse_pipeline.db import migrations
from funhouse_pipeline.db.migrations import (
    ALLOWED_METRIC_TYPES,
    EXPECTED_TABLES,
    UNIVERSAL_COLUMNS,
    run_migrations,
)

pytestmark = [pytest.mark.db, pytest.mark.property]

# Hypothesis settings shared by the DB properties: >=100 iterations, no deadline
# (DB round-trips vary), and tolerate the function-scoped connection fixture.
_DB_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


@pytest.fixture
def migrated_db(db_connection):
    """Apply all migrations once against the isolated schema, return the conn."""
    run_migrations(db_connection)
    return db_connection


@pytest.fixture
def migrated_db_with_player(migrated_db):
    """Seed a location + player so student_metrics inserts satisfy FKs."""
    conn = migrated_db
    loc_id = conn.execute(
        "INSERT INTO locations (name) VALUES ('prop-loc') RETURNING id"
    ).fetchone()[0]
    player_id = conn.execute(
        "INSERT INTO players (first_name, location_id) VALUES ('Prop', %s) RETURNING id",
        (loc_id,),
    ).fetchone()[0]
    conn.commit()
    return conn, player_id, loc_id


# Feature: phase0-data-foundation, Property 1: Universal schema column presence
# For any table in the deployed schema, that table has id, created_at,
# updated_at, and location_id columns.
# Validates: Requirements 1.2, 1.3
@_DB_SETTINGS
@given(table=st.sampled_from(EXPECTED_TABLES))
def test_property_1_universal_columns_present(migrated_db, table):
    columns = migrations.table_columns(migrated_db, table)
    for required in UNIVERSAL_COLUMNS:
        assert required in columns, f"{table} missing universal column {required}"


# Feature: phase0-data-foundation, Property 2: Schema deploy is idempotent and
# non-destructive. For any pre-existing database state, running schema
# deployment again leaves every existing table and all its rows intact and
# reports already-present tables as present.
# Validates: Requirements 1.6
@_DB_SETTINGS
@given(sentinel_rows=st.integers(min_value=0, max_value=5))
def test_property_2_deploy_idempotent_and_nondestructive(migrated_db, sentinel_rows):
    conn = migrated_db
    # Establish a known state: insert N sentinel location rows.
    before_count = conn.execute("SELECT count(*) FROM locations").fetchone()[0]
    for i in range(sentinel_rows):
        conn.execute("INSERT INTO locations (name) VALUES (%s)", (f"sentinel-{i}-{before_count}",))
    conn.commit()
    expected_count = before_count + sentinel_rows

    # Re-run deployment: it must be non-destructive and report tables present.
    result = run_migrations(conn)
    assert set(result.already_present()) == set(EXPECTED_TABLES)
    assert result.created() == []

    after_count = conn.execute("SELECT count(*) FROM locations").fetchone()[0]
    assert after_count == expected_count, "re-deploy must not drop or alter existing rows"


# Feature: phase0-data-foundation, Property 3: metric_type domain is enforced.
# For any string value, inserting a student_metrics row succeeds if and only if
# the value is one of the allowed metric types.
# Validates: Requirements 1.7
@_DB_SETTINGS
@given(
    metric_type=st.one_of(
        st.sampled_from(ALLOWED_METRIC_TYPES),
        st.text(min_size=0, max_size=24),
    )
)
def test_property_3_metric_type_domain_enforced(migrated_db_with_player, metric_type):
    conn, player_id, loc_id = migrated_db_with_player
    should_succeed = metric_type in set(ALLOWED_METRIC_TYPES)

    inserted = True
    try:
        with conn.transaction():
            conn.execute(
                "INSERT INTO student_metrics (player_id, metric_type, value, location_id) "
                "VALUES (%s, %s, %s, %s)",
                (player_id, metric_type, "some-value", loc_id),
            )
    except Exception:
        inserted = False

    assert inserted == should_succeed
