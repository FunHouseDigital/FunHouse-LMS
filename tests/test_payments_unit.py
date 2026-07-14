"""Example/unit test for the payments endpoint (Task 10.2, Req 10.5).

A payment request that omits the amount is a validation error -> ``422``.
Requires a reachable PostgreSQL; skips otherwise.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from funhouse_api.auth.service import AuthUser, issue_token
from funhouse_api.config import ApiConfig
from funhouse_pipeline.config import Config, DatabaseConfig
from funhouse_pipeline.db.migrations import run_migrations
from funhouse_pipeline.db.seed import seed
from tests.api_helpers import build_client

pytestmark = [pytest.mark.db]

_CONFIG = ApiConfig(
    pipeline=Config(database=DatabaseConfig()),
    jwt_secret="payments-unit-secret",
    jwt_ttl_seconds=3600,
)


@pytest.fixture
def seeded_db(db_connection):
    run_migrations(db_connection)
    seed(db_connection)
    return db_connection


def _founder_headers(user_id) -> dict[str, str]:
    token = issue_token(
        AuthUser(id=str(user_id), role="founder", location_id=None, school_id=None),
        now=datetime.now(timezone.utc),
        secret=_CONFIG.jwt_secret,
        ttl_seconds=_CONFIG.jwt_ttl_seconds,
    )
    return {"Authorization": f"Bearer {token}"}


def test_missing_amount_returns_422(seeded_db):
    conn = seeded_db
    aya = conn.execute("SELECT id FROM users WHERE name = 'Aya'").fetchone()[0]
    loc = conn.execute("SELECT id FROM locations LIMIT 1").fetchone()[0]
    player_id = conn.execute(
        "INSERT INTO players (first_name, location_id) VALUES ('Zola', %s) RETURNING id",
        (loc,),
    ).fetchone()[0]
    conn.commit()

    headers = _founder_headers(aya)
    with build_client(connection=conn, config=_CONFIG) as client:
        resp = client.post(
            "/payments",
            json={"player_id": str(player_id)},  # amount_cents omitted
            headers=headers,
        )
    assert resp.status_code == 422, resp.text
