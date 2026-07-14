"""Property-based tests for the RBAC_Enforcer (Tasks 5.2, 5.3, 5.4).

Implements design Properties 5, 6, and 7 against the real Phase 0 schema. These
require a reachable PostgreSQL server and skip automatically otherwise. Each
property runs a minimum of 100 Hypothesis iterations.

The ``players`` table is used as the representative resource: it carries both
``location_id`` (NOT NULL) and a nullable ``school_id``, so it exercises the
manager (location-only) and facilitator (location + school) scopes as well as
the unrestricted founder.

Each example runs inside a SAVEPOINT that is rolled back afterwards, so examples
sharing the connection stay isolated.
"""

from __future__ import annotations

import uuid

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from funhouse_api.auth.dependencies import Principal
from funhouse_api.rbac import AuthzError, Scope
from funhouse_pipeline.db.migrations import run_migrations

pytestmark = [pytest.mark.db, pytest.mark.property]

_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


@pytest.fixture
def migrated_db(db_connection):
    run_migrations(db_connection)
    return db_connection


def _make_location(conn) -> str:
    row = conn.execute(
        "INSERT INTO locations (name) VALUES (%s) RETURNING id",
        (f"loc-{uuid.uuid4().hex[:10]}",),
    ).fetchone()
    return str(row[0])


def _make_school(conn, location_id: str) -> str:
    row = conn.execute(
        "INSERT INTO schools (name, contract_status, location_id) VALUES (%s, %s, %s) RETURNING id",
        (f"school-{uuid.uuid4().hex[:10]}", "partner", location_id),
    ).fetchone()
    return str(row[0])


def _make_player(conn, location_id: str, school_id: str | None) -> str:
    row = conn.execute(
        "INSERT INTO players (first_name, location_id, school_id) VALUES (%s, %s, %s) RETURNING id",
        (f"p-{uuid.uuid4().hex[:6]}", location_id, school_id),
    ).fetchone()
    return str(row[0])


def _count_players(conn) -> int:
    return conn.execute("SELECT count(*) FROM players").fetchone()[0]


# A layout: 2 locations, each with 2 schools; players spread across them.
def _build_dataset(conn) -> dict:
    locations = [_make_location(conn) for _ in range(2)]
    schools = {loc: [_make_school(conn, loc) for _ in range(2)] for loc in locations}
    return {"locations": locations, "schools": schools}


# --------------------------------------------------------------------------- #
# Property 5 — read scope containment
# --------------------------------------------------------------------------- #

# Feature: funhouse-api, Property 5: No response ever contains an out-of-scope
# record — for any multi-location/multi-school dataset and any principal, every
# record in any collection or single-record response is within scope (founder
# all; manager same location_id; facilitator same location_id and school_id); a
# direct read of an out-of-scope id is rejected.
# Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.6, 3.7, 6.1, 8.6, 10.3, 12.6, 15.1, 15.2
@_SETTINGS
@given(
    role=st.sampled_from(["founder", "manager", "facilitator"]),
    placements=st.lists(
        st.tuples(st.integers(0, 1), st.integers(0, 1)), min_size=1, max_size=12
    ),
)
def test_property_5_read_scope_containment(migrated_db, role: str, placements) -> None:
    conn = migrated_db
    conn.execute("SAVEPOINT ex")
    try:
        data = _build_dataset(conn)
        locations = data["locations"]
        schools = data["schools"]

        # Insert players at chosen (location, school) placements; record scope.
        players: list[tuple[str, str, str]] = []  # (player_id, loc, school)
        for loc_idx, sch_idx in placements:
            loc = locations[loc_idx]
            sch = schools[loc][sch_idx]
            pid = _make_player(conn, loc, sch)
            players.append((pid, loc, sch))

        # Choose the principal's scope from the dataset.
        principal_loc = locations[0]
        principal_school = schools[principal_loc][0]
        principal = Principal(
            user_id="u",
            role=role,
            location_id=None if role == "founder" else principal_loc,
            school_id=principal_school if role == "facilitator" else None,
        )
        scope = Scope.derive(principal)

        fragment, params = scope.read_filter()
        returned = {
            str(r[0])
            for r in conn.execute(
                f"SELECT id FROM players WHERE {fragment}", params
            ).fetchall()
        }

        # Expected in-scope set computed independently in Python.
        if role == "founder":
            expected = {pid for pid, _, _ in players}
        elif role == "manager":
            expected = {pid for pid, loc, _ in players if loc == principal_loc}
        else:  # facilitator
            expected = {
                pid
                for pid, loc, sch in players
                if loc == principal_loc and sch == principal_school
            }

        assert returned == expected
        # No returned row is outside scope (containment).
        out_of_scope = {pid for pid, _, _ in players} - expected
        assert returned.isdisjoint(out_of_scope)

        # Direct read of an out-of-scope id yields no row (router → 403).
        for pid in out_of_scope:
            row = conn.execute(
                f"SELECT id FROM players WHERE id = %s AND ({fragment})",
                [pid, *params],
            ).fetchone()
            assert row is None
    finally:
        conn.execute("ROLLBACK TO SAVEPOINT ex")


# --------------------------------------------------------------------------- #
# Property 6 — out-of-scope writes rejected and never persisted
# --------------------------------------------------------------------------- #

# Feature: funhouse-api, Property 6: Out-of-scope writes are rejected and never
# persisted — for any write targeting a location_id/school_id outside the
# caller's scope, the request is rejected and the target table row count is
# unchanged.
# Validates: Requirements 3.5, 4.7, 7.4
@_SETTINGS
@given(
    role=st.sampled_from(["founder", "manager", "facilitator"]),
    target_loc_idx=st.integers(0, 1),
    target_sch_idx=st.integers(0, 1),
)
def test_property_6_out_of_scope_write_rejected(
    migrated_db, role: str, target_loc_idx: int, target_sch_idx: int
) -> None:
    conn = migrated_db
    conn.execute("SAVEPOINT ex")
    try:
        data = _build_dataset(conn)
        locations = data["locations"]
        schools = data["schools"]

        principal_loc = locations[0]
        principal_school = schools[principal_loc][0]
        principal = Principal(
            user_id="u",
            role=role,
            location_id=None if role == "founder" else principal_loc,
            school_id=principal_school if role == "facilitator" else None,
        )
        scope = Scope.derive(principal)

        target_loc = locations[target_loc_idx]
        target_school = schools[target_loc][target_sch_idx]

        # Independently determine whether the target is outside scope.
        if role == "founder":
            out_of_scope = False
        elif role == "manager":
            out_of_scope = target_loc != principal_loc
        else:
            out_of_scope = not (
                target_loc == principal_loc and target_school == principal_school
            )

        before = _count_players(conn)
        rejected = False
        try:
            scope.assert_can_write(target_loc, target_school)
        except AuthzError:
            rejected = True
        else:
            # Authorized → the write proceeds.
            _make_player(conn, target_loc, target_school)

        after = _count_players(conn)

        # Rejection happens exactly for out-of-scope targets.
        assert rejected == out_of_scope
        if rejected:
            # Rejected write persisted nothing (count unchanged).
            assert after == before
        else:
            assert after == before + 1
    finally:
        conn.execute("ROLLBACK TO SAVEPOINT ex")


# --------------------------------------------------------------------------- #
# Property 7 — created rows stamped to the caller's scope
# --------------------------------------------------------------------------- #

# Feature: funhouse-api, Property 7: Created rows are stamped to the caller's
# scope — for any create request, the persisted row's location_id (and
# school_id where school-associated) equals the caller's scope.
# Validates: Requirements 6.2, 7.1, 15.3
@_SETTINGS
@given(
    role=st.sampled_from(["founder", "manager", "facilitator"]),
    supplied_loc_idx=st.integers(0, 1),
    supplied_sch_idx=st.integers(0, 1),
)
def test_property_7_created_rows_stamped_to_scope(
    migrated_db, role: str, supplied_loc_idx: int, supplied_sch_idx: int
) -> None:
    conn = migrated_db
    conn.execute("SAVEPOINT ex")
    try:
        data = _build_dataset(conn)
        locations = data["locations"]
        schools = data["schools"]

        principal_loc = locations[0]
        principal_school = schools[principal_loc][0]
        principal = Principal(
            user_id="u",
            role=role,
            location_id=None if role == "founder" else principal_loc,
            school_id=principal_school if role == "facilitator" else None,
        )
        scope = Scope.derive(principal)

        # A create payload that supplies (possibly different) location/school.
        supplied_loc = locations[supplied_loc_idx]
        supplied_school = schools[supplied_loc][supplied_sch_idx]
        new_row: dict = {
            "first_name": "stamped",
            "location_id": supplied_loc,
            "school_id": supplied_school,
        }
        scope.stamp(new_row)

        pid = conn.execute(
            "INSERT INTO players (first_name, location_id, school_id) VALUES (%s, %s, %s) RETURNING id",
            (new_row["first_name"], new_row["location_id"], new_row["school_id"]),
        ).fetchone()[0]

        persisted = conn.execute(
            "SELECT location_id, school_id FROM players WHERE id = %s", (pid,)
        ).fetchone()
        persisted_loc, persisted_school = str(persisted[0]), (
            None if persisted[1] is None else str(persisted[1])
        )

        if role == "founder":
            # Founder scope stamps nothing; supplied values are preserved.
            assert persisted_loc == supplied_loc
            assert persisted_school == supplied_school
        elif role == "manager":
            # location stamped to scope; school left as supplied.
            assert persisted_loc == principal_loc
            assert persisted_school == supplied_school
        else:  # facilitator
            assert persisted_loc == principal_loc
            assert persisted_school == principal_school
    finally:
        conn.execute("ROLLBACK TO SAVEPOINT ex")
