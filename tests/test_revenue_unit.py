"""Example test for the revenue summary (Task 11.3, Req 11.3).

With no school-contract payment, the ``school_contracts`` stream is R0 (0 cents).
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
    jwt_secret="revenue-unit-secret",
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


def test_school_contracts_is_zero_with_no_payment(seeded_db):
    conn = seeded_db
    aya = conn.execute("SELECT id FROM users WHERE name = 'Aya'").fetchone()[0]
    headers = _founder_headers(aya)
    with build_client(connection=conn, config=_CONFIG) as client:
        resp = client.get("/revenue/summary", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["school_contracts_cents"] == 0
    assert body["pay_per_use_cents"] == 0
    assert body["subscription_cents"] == 0
