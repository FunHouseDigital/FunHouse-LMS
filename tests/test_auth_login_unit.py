"""Example/unit tests for login failures (Task 3.6).

Concrete cases not suited to property testing (design § Testing Strategy):

* An unknown identifier is rejected with a generic ``401`` and no token
  (Req 1.3) — needs an empty ``users`` table, so it is a DB-backed test.
* A request missing the identifier or password is a validation error → ``422``
  (Req 1.7) — Pydantic rejects the body before any DB access, so this runs
  without a database.
"""

from __future__ import annotations

import pytest

from funhouse_api.auth.router import _lookup_user
from funhouse_api.auth.service import hash_password
from funhouse_pipeline.db.migrations import run_migrations
from tests.api_helpers import build_client


class _StubCursor:
    """Minimal cursor returning one configured row batch per query."""

    def __init__(self, batches):
        self._batches = iter(batches)
        self._current = []
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, query, params):
        self.calls.append((query, params))
        self._current = next(self._batches)

    def fetchall(self):
        return self._current


class _StubConnection:
    def __init__(self, batches):
        self.stub_cursor = _StubCursor(batches)

    def cursor(self):
        return self.stub_cursor


@pytest.mark.db
def test_unknown_identifier_returns_401_and_no_token(db_connection):
    """An identifier matching no users row → 401, no access token (Req 1.3)."""
    run_migrations(db_connection)

    with build_client(connection=db_connection) as client:
        response = client.post(
            "/auth/login", json={"identifier": "nobody", "password": "whatever"}
        )

    assert response.status_code == 401
    assert "access_token" not in response.json()


def test_missing_password_returns_422():
    """Omitting the password is a validation error → 422 (Req 1.7)."""
    # A sentinel connection: validation fails before the endpoint body runs, so
    # the connection is never used (no database required).
    with build_client(connection=object()) as client:
        response = client.post("/auth/login", json={"identifier": "aya"})

    assert response.status_code == 422


def test_missing_identifier_returns_422():
    """Omitting the identifier is a validation error → 422 (Req 1.7)."""
    with build_client(connection=object()) as client:
        response = client.post("/auth/login", json={"password": "secret"})

    assert response.status_code == 422



def test_lookup_trims_identifier_and_accepts_one_case_insensitive_match():
    """A harmless case/whitespace difference resolves one account safely."""
    row = ("user-1", "manager", None, None, "password-hash")
    conn = _StubConnection([[], [row]])

    found = _lookup_user(conn, "  LOYISO\t")

    assert found is not None
    user, password_hash = found
    assert user.id == "user-1"
    assert password_hash == "password-hash"
    assert len(conn.stub_cursor.calls) == 2
    assert conn.stub_cursor.calls[0][1] == ("LOYISO", "LOYISO")
    assert "LOWER(name)" in conn.stub_cursor.calls[1][0]
    assert conn.stub_cursor.calls[1][1] == ("LOYISO", "LOYISO")


def test_lookup_fails_closed_for_ambiguous_case_insensitive_matches():
    """Case-insensitive lookup never selects an arbitrary duplicate account."""
    rows = [
        ("user-1", "manager", None, None, "hash-1"),
        ("user-2", "manager", None, None, "hash-2"),
    ]
    conn = _StubConnection([[], rows])

    assert _lookup_user(conn, "LOYISO") is None


def test_lookup_prefers_one_exact_match_without_case_insensitive_fallback():
    """An exact identifier remains deterministic if case variants exist."""
    row = ("user-1", "manager", None, None, "password-hash")
    conn = _StubConnection([[row]])

    found = _lookup_user(conn, "Loyiso")

    assert found is not None
    assert found[0].id == "user-1"
    assert len(conn.stub_cursor.calls) == 1



@pytest.mark.db
def test_login_accepts_trimmed_case_insensitive_identifier_and_preserves_password(db_connection):
    """The endpoint normalises only the identifier, never the password."""
    run_migrations(db_connection)
    location_id = db_connection.execute(
        "INSERT INTO locations (name) VALUES (%s) RETURNING id",
        ("Login normalisation location",),
    ).fetchone()[0]
    password = " secret with spaces "
    db_connection.execute(
        """
        INSERT INTO users (name, role, password_hash, location_id)
        VALUES (%s, %s, %s, %s)
        """,
        ("Loyiso", "manager", hash_password(password), location_id),
    )

    with build_client(connection=db_connection) as client:
        success = client.post(
            "/auth/login",
            json={"identifier": "  loyiso\t", "password": password},
        )
        altered_password = client.post(
            "/auth/login",
            json={"identifier": "Loyiso", "password": password.strip()},
        )

    assert success.status_code == 200, success.text
    assert altered_password.status_code == 401
    assert altered_password.json() == {"detail": "Invalid credentials"}


@pytest.mark.db
def test_login_prefers_exact_identifier_and_rejects_ambiguous_case_fallback(db_connection):
    """Exact matching is deterministic; case-folded cross-field ambiguity is denied."""
    run_migrations(db_connection)
    location_id = db_connection.execute(
        "INSERT INTO locations (name) VALUES (%s) RETURNING id",
        ("Login ambiguity location",),
    ).fetchone()[0]
    db_connection.execute(
        """
        INSERT INTO users (name, role, email, password_hash, location_id)
        VALUES (%s, %s, %s, %s, %s), (%s, %s, %s, %s, %s)
        """,
        (
            "Loyiso",
            "manager",
            None,
            hash_password("first-password"),
            location_id,
            "Another user",
            "manager",
            "loyiso",
            hash_password("second-password"),
            location_id,
        ),
    )

    with build_client(connection=db_connection) as client:
        exact = client.post(
            "/auth/login",
            json={"identifier": "Loyiso", "password": "first-password"},
        )
        ambiguous = client.post(
            "/auth/login",
            json={"identifier": "LOYISO", "password": "first-password"},
        )

    assert exact.status_code == 200, exact.text
    assert ambiguous.status_code == 401
    assert ambiguous.json() == {"detail": "Invalid credentials"}
