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

from funhouse_pipeline.db.migrations import run_migrations
from tests.api_helpers import build_client


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
