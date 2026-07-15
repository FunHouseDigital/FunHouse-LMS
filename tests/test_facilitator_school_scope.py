"""Tests for facilitator school scope at login (Task 18.3).

Closes the end-to-end gap wired by migration ``004_users_school_id.sql`` +
login selection of ``users.school_id`` (Req 1.8, 3.3):

* **Migration** — after ``run_migrations`` the ``users`` table has a
  ``school_id`` column, and re-running is safe (idempotent, additive, no data
  loss). The expected 14-table count is unchanged.
* **Auth** — a facilitator with an assigned ``school_id`` logs in and receives a
  JWT whose decoded ``school_id`` claim equals that school (mirrors Property 1);
  a founder/manager with no school gets a null claim.
* **RBAC** — a facilitator :class:`Scope` built from that token is scoped to the
  school: ``read_filter`` constrains by both ``location_id`` and ``school_id``.

DB-backed tests reuse the Phase 0 ``db_connection`` fixture and skip gracefully
when no PostgreSQL server is reachable.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from funhouse_api.auth.dependencies import Principal
from funhouse_api.auth.service import decode_token, hash_password
from funhouse_api.config import ApiConfig
from funhouse_api.rbac import Scope
from funhouse_pipeline.config import Config, DatabaseConfig
from funhouse_pipeline.db import migrations
from funhouse_pipeline.db.migrations import run_migrations
from tests.api_helpers import build_client

_CONFIG = ApiConfig(
    pipeline=Config(database=DatabaseConfig()),
    jwt_secret="facilitator-school-scope-secret",
    jwt_ttl_seconds=3600,
)


def _make_location(conn) -> str:
    row = conn.execute(
        "INSERT INTO locations (name) VALUES (%s) RETURNING id",
        (f"loc-{uuid.uuid4().hex[:8]}",),
    ).fetchone()
    return str(row[0])


def _make_school(conn, location_id: str) -> str:
    row = conn.execute(
        """
        INSERT INTO schools (name, contract_status, location_id)
        VALUES (%s, 'partner', %s) RETURNING id
        """,
        (f"school-{uuid.uuid4().hex[:8]}", location_id),
    ).fetchone()
    return str(row[0])


# --------------------------------------------------------------------------- #
# Migration 004: users.school_id additive column
# --------------------------------------------------------------------------- #


@pytest.mark.db
def test_migration_004_adds_users_school_id_column(db_connection):
    """After migrations, users carries a nullable school_id column (Req 1.8, 3.3)."""
    result = run_migrations(db_connection)
    assert "004_users_school_id.sql" in result.applied_files
    assert "school_id" in migrations.table_columns(db_connection, "users")


@pytest.mark.db
def test_migration_004_is_idempotent_when_run_twice(db_connection):
    """Re-running is safe (ADD COLUMN IF NOT EXISTS) and preserves the users table."""
    run_migrations(db_connection)
    second = run_migrations(db_connection)
    assert "004_users_school_id.sql" in second.applied_files
    assert "users" in second.already_present()
    assert "school_id" in migrations.table_columns(db_connection, "users")


@pytest.mark.db
def test_migration_004_keeps_expected_table_count(db_connection):
    """The additive column adds no table: the 14-table schema is unchanged."""
    result = run_migrations(db_connection)
    present = set(result.created()) | set(result.already_present())
    assert present == set(migrations.EXPECTED_TABLES)
    assert len(migrations.EXPECTED_TABLES) == 14


@pytest.mark.db
def test_users_school_id_is_nullable_for_non_facilitators(db_connection):
    """Founders/managers insert with no school_id (the column stays NULL)."""
    run_migrations(db_connection)
    location_id = _make_location(db_connection)
    row = db_connection.execute(
        """
        INSERT INTO users (name, role, location_id)
        VALUES (%s, 'manager', %s) RETURNING school_id
        """,
        (f"mgr-{uuid.uuid4().hex[:6]}", location_id),
    ).fetchone()
    assert row[0] is None


# --------------------------------------------------------------------------- #
# Auth: facilitator login sources school_id into the JWT
# --------------------------------------------------------------------------- #


@pytest.mark.db
def test_facilitator_login_populates_school_id_claim(db_connection):
    """A facilitator's JWT carries school_id == users.school_id (Req 1.8, 3.3)."""
    run_migrations(db_connection)
    location_id = _make_location(db_connection)
    school_id = _make_school(db_connection, location_id)

    identifier, password = "fran", "s3cret-pass"
    user_row = db_connection.execute(
        """
        INSERT INTO users (name, role, password_hash, location_id, school_id)
        VALUES (%s, 'facilitator', %s, %s, %s) RETURNING id
        """,
        (identifier, hash_password(password), location_id, school_id),
    ).fetchone()
    user_id = str(user_row[0])
    db_connection.commit()

    with build_client(connection=db_connection, config=_CONFIG) as client:
        response = client.post(
            "/auth/login", json={"identifier": identifier, "password": password}
        )

    assert response.status_code == 200, response.text
    claims = decode_token(
        response.json()["access_token"],
        now=datetime.now(timezone.utc),
        secret=_CONFIG.jwt_secret,
    )
    assert claims.sub == user_id
    assert claims.role == "facilitator"
    assert claims.location_id == location_id
    assert claims.school_id == school_id


@pytest.mark.db
def test_manager_login_has_null_school_id_claim(db_connection):
    """A manager (no assigned school) gets a null school_id claim."""
    run_migrations(db_connection)
    location_id = _make_location(db_connection)

    identifier, password = "loyiso", "manage-me"
    db_connection.execute(
        """
        INSERT INTO users (name, role, password_hash, location_id)
        VALUES (%s, 'manager', %s, %s)
        """,
        (identifier, hash_password(password), location_id),
    )
    db_connection.commit()

    with build_client(connection=db_connection, config=_CONFIG) as client:
        response = client.post(
            "/auth/login", json={"identifier": identifier, "password": password}
        )

    assert response.status_code == 200, response.text
    claims = decode_token(
        response.json()["access_token"],
        now=datetime.now(timezone.utc),
        secret=_CONFIG.jwt_secret,
    )
    assert claims.role == "manager"
    assert claims.school_id is None


# --------------------------------------------------------------------------- #
# RBAC: a facilitator principal from that token is scoped to the school
# --------------------------------------------------------------------------- #


@pytest.mark.db
def test_facilitator_token_yields_school_scoped_rbac(db_connection):
    """The token round-trips into a Scope constrained by location AND school (Req 3.3)."""
    run_migrations(db_connection)
    location_id = _make_location(db_connection)
    school_id = _make_school(db_connection, location_id)

    identifier, password = "fran-rbac", "another-pass"
    db_connection.execute(
        """
        INSERT INTO users (name, role, password_hash, location_id, school_id)
        VALUES (%s, 'facilitator', %s, %s, %s)
        """,
        (identifier, hash_password(password), location_id, school_id),
    )
    db_connection.commit()

    with build_client(connection=db_connection, config=_CONFIG) as client:
        response = client.post(
            "/auth/login", json={"identifier": identifier, "password": password}
        )
    assert response.status_code == 200, response.text

    claims = decode_token(
        response.json()["access_token"],
        now=datetime.now(timezone.utc),
        secret=_CONFIG.jwt_secret,
    )
    scope = Scope.derive(Principal.from_claims(claims))

    assert scope.role == "facilitator"
    assert scope.location_id == location_id
    assert scope.school_id == school_id
    assert not scope.unrestricted

    fragment, params = scope.read_filter()
    assert "location_id" in fragment and "school_id" in fragment
    assert params == [location_id, school_id]
    # A cross-school write is rejected; the assigned school is allowed.
    with pytest.raises(Exception):
        scope.assert_can_write(location_id, str(uuid.uuid4()))
    scope.assert_can_write(location_id, school_id)  # no raise
