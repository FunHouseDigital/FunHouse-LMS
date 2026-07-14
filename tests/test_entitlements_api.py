"""Integration tests for the entitlement endpoints (Task 6.8, Req 8.1, 8.2, 8.6).

Exercises the full stack over the FastAPI ``TestClient`` (routing -> auth -> RBAC
-> Entitlement_Engine -> Postgres): create an entitlement, draw from it, and read
the scoped balance. Requires a reachable PostgreSQL server and skips otherwise.

These endpoints commit their writes, so (unlike the savepoint-isolated property
tests) this test relies on the ``db_connection`` fixture's schema-drop teardown
for cleanup rather than a transaction rollback.
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
    jwt_secret="entitlements-integration-secret",
    jwt_ttl_seconds=3600,
)


def _founder_headers(user_id: str) -> dict[str, str]:
    token = issue_token(
        AuthUser(id=user_id, role="founder", location_id=None, school_id=None),
        now=datetime.now(timezone.utc),
        secret=_CONFIG.jwt_secret,
        ttl_seconds=_CONFIG.jwt_ttl_seconds,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def seeded_db(db_connection):
    run_migrations(db_connection)
    seed(db_connection)
    return db_connection


def test_create_draw_and_balance_round_trip(seeded_db):
    conn = seeded_db
    loc = conn.execute("SELECT id FROM locations WHERE name = 'Smithfield'").fetchone()[0]
    aya = conn.execute("SELECT id FROM users WHERE name = 'Aya'").fetchone()[0]
    product_id = conn.execute(
        "SELECT id FROM products WHERE name = 'Holiday Special'"
    ).fetchone()[0]
    player_id = conn.execute(
        "INSERT INTO players (first_name, consent_status, location_id) "
        "VALUES ('Zola', 'pending', %s) RETURNING id",
        (loc,),
    ).fetchone()[0]
    conn.commit()

    headers = _founder_headers(str(aya))

    with build_client(connection=conn, config=_CONFIG) as client:
        # Create: Holiday Special is 3 hrs/week -> 180 minute-units.
        create = client.post(
            "/entitlements",
            json={"player_id": str(player_id), "product_id": str(product_id)},
            headers=headers,
        )
        assert create.status_code == 201, create.text
        created = create.json()
        assert created["remaining_units"] == 180
        entitlement_id = created["id"]

        # Draw 60 units -> 120 remaining.
        drew = client.post(
            f"/entitlements/{entitlement_id}/draw",
            json={"amount": 60},
            headers=headers,
        )
        assert drew.status_code == 200, drew.text
        assert drew.json()["remaining_units"] == 120

        # Balance reflects the decremented entitlement.
        bal = client.get(f"/players/{player_id}/entitlements", headers=headers)
        assert bal.status_code == 200, bal.text
        balances = bal.json()
        assert len(balances) == 1
        assert balances[0]["remaining_units"] == 120
        assert balances[0]["entitlement_id"] == entitlement_id

        # An over-draw is rejected with 409 and leaves units unchanged.
        over = client.post(
            f"/entitlements/{entitlement_id}/draw",
            json={"amount": 10_000},
            headers=headers,
        )
        assert over.status_code == 409, over.text

    # Digital signature recorded for the successful draw (Req 8.3, 8.8).
    sig_count = conn.execute(
        "SELECT count(*) FROM sync_log WHERE entity = 'entitlements' "
        "AND record_id = %s AND action = 'update' AND user_id = %s",
        (entitlement_id, aya),
    ).fetchone()[0]
    assert sig_count == 1
