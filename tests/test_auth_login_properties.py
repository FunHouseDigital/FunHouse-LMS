"""Property-based test for login token round-trip (Task 3.5).

Implements design Property 1. Requires a reachable PostgreSQL server and skips
automatically otherwise (see conftest's ``db_connection`` fixture). Runs a
minimum of 100 Hypothesis iterations. Exercises the full login path over the
FastAPI ``TestClient`` — real bcrypt, real PyJWT, real Postgres — with no
mocking.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from funhouse_api.auth.service import decode_token, hash_password
from funhouse_api.config import ApiConfig
from funhouse_pipeline.config import Config, DatabaseConfig
from funhouse_pipeline.db.migrations import run_migrations
from tests.api_helpers import build_client

pytestmark = [pytest.mark.db, pytest.mark.property]

_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# A config with a fixed, known secret so the test can decode the issued token.
_CONFIG = ApiConfig(
    pipeline=Config(database=DatabaseConfig()),
    jwt_secret="login-property-secret",
    jwt_ttl_seconds=3600,
)

# Identifiers/passwords: printable, non-empty, bounded (bcrypt 72-byte limit).
_identifiers = st.text(
    alphabet=st.characters(min_codepoint=48, max_codepoint=122), min_size=1, max_size=24
)
_passwords = st.text(min_size=1, max_size=60)
_roles = st.sampled_from(["founder", "manager", "facilitator"])


@pytest.fixture
def migrated_db(db_connection):
    """Apply all migrations once against the isolated schema; return the conn."""
    run_migrations(db_connection)
    return db_connection


def _make_location(conn) -> str:
    row = conn.execute(
        "INSERT INTO locations (name) VALUES (%s) RETURNING id",
        (f"loc-{uuid.uuid4().hex[:8]}",),
    ).fetchone()
    return str(row[0])


# Feature: funhouse-api, Property 1: Login token round-trips identity, role, and
# scope — for any users row with a known password, correct login yields a JWT
# whose decoded claims contain exactly that user's id, role, and location scope.
# Validates: Requirements 1.1, 2.1
@_SETTINGS
@given(identifier=_identifiers, password=_passwords, role=_roles)
def test_property_1_login_token_round_trips_identity_role_scope(
    migrated_db, identifier: str, password: str, role: str
) -> None:
    conn = migrated_db
    # Each example works inside a savepoint so inserted rows are rolled back
    # afterwards, keeping examples isolated on the shared connection. The login
    # request reads through the same connection, so the uncommitted row is
    # visible without a commit.
    conn.execute("SAVEPOINT ex")
    try:
        location_id = _make_location(conn)
        user_row = conn.execute(
            """
            INSERT INTO users (name, role, password_hash, location_id)
            VALUES (%s, %s, %s, %s) RETURNING id
            """,
            (identifier, role, hash_password(password), location_id),
        ).fetchone()
        user_id = str(user_row[0])

        with build_client(connection=conn, config=_CONFIG) as client:
            response = client.post(
                "/auth/login", json={"identifier": identifier, "password": password}
            )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["token_type"] == "bearer"

        claims = decode_token(
            body["access_token"],
            now=datetime.now(timezone.utc),
            secret=_CONFIG.jwt_secret,
        )
        # Claims round-trip the stored identity, role, and location scope.
        assert claims.sub == user_id
        assert claims.role == role
        assert claims.location_id == location_id
    finally:
        conn.execute("ROLLBACK TO SAVEPOINT ex")
