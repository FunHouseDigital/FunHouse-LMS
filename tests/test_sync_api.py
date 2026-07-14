"""Integration test for the sync endpoint (Task 14.6, Req 4.1, 4.4).

Exercises the full stack over the FastAPI ``TestClient``: a real login yields a
JWT, then a single ``POST /sync`` submits a mixed batch (player, consent,
session, payment, entitlement). The test asserts the per-action results and that
the rows were persisted with their audit entries.

Requires a reachable PostgreSQL; skips otherwise.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from funhouse_api.auth.service import hash_password
from funhouse_api.config import ApiConfig
from funhouse_pipeline.config import Config, DatabaseConfig
from funhouse_pipeline.db.migrations import run_migrations
from funhouse_pipeline.db.seed import seed
from tests.api_helpers import build_client

pytestmark = [pytest.mark.db]

_CONFIG = ApiConfig(
    pipeline=Config(database=DatabaseConfig()),
    jwt_secret="sync-integration-secret",
    jwt_ttl_seconds=3600,
)


@pytest.fixture
def seeded_db(db_connection):
    run_migrations(db_connection)
    seed(db_connection)
    return db_connection


def test_sync_batch_round_trip(seeded_db):
    conn = seeded_db
    # Give the founder Aya a password so a real login works (login -> token).
    conn.execute(
        "UPDATE users SET password_hash = %s WHERE name = 'Aya'",
        (hash_password("s3cret-aya"),),
    )
    loc = conn.execute(
        "SELECT id FROM locations WHERE name = 'Smithfield'"
    ).fetchone()[0]
    product_id = conn.execute("SELECT id FROM products LIMIT 1").fetchone()[0]
    # A pre-existing player the consent/session/payment/entitlement reference.
    player_id = conn.execute(
        "INSERT INTO players (first_name, consent_status, location_id) "
        "VALUES ('Existing', 'pending', %s) RETURNING id",
        (loc,),
    ).fetchone()[0]
    conn.commit()

    now = "2024-06-01T12:00:00+00:00"
    batch = {
        "actions": [
            {"client_id": "a-player", "entity": "player", "created_at": now,
             "payload": {"first_name": "Newbie", "last_name": "Sync",
                         "birth_date": "2013-01-02", "location_id": str(loc)}},
            {"client_id": "a-consent", "entity": "consent", "created_at": now,
             "payload": {"player_id": str(player_id),
                         "consent_type": "data_processing", "granted": True}},
            {"client_id": "a-session", "entity": "session", "created_at": now,
             "payload": {"player_id": str(player_id), "session_type": "lounge",
                         "started_at": "2024-06-01T10:00:00+00:00",
                         "duration_minutes": 45}},
            {"client_id": "a-payment", "entity": "payment", "created_at": now,
             "payload": {"player_id": str(player_id), "product_id": str(product_id),
                         "amount_cents": 3000, "method": "cash"}},
            {"client_id": "a-entitlement", "entity": "entitlement", "created_at": now,
             "payload": {"player_id": str(player_id), "product_id": str(product_id)}},
        ]
    }

    with build_client(connection=conn, config=_CONFIG) as client:
        login = client.post(
            "/auth/login", json={"identifier": "Aya", "password": "s3cret-aya"}
        )
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.post("/sync", json=batch, headers=headers)

    assert resp.status_code == 200, resp.text
    results = {r["client_id"]: r for r in resp.json()["results"]}
    # Exactly one result per action, all applied.
    assert len(results) == 5
    assert all(r["status"] == "applied" for r in results.values()), results
    assert all(r["record_id"] for r in results.values())

    # Rows persisted (fresh read).
    conn.rollback()
    assert conn.execute(
        "SELECT count(*) FROM players WHERE first_name = 'Newbie'"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT count(*) FROM consents WHERE client_id = 'a-consent'"
    ).fetchone()[0] == 1
    session_id = results["a-session"]["record_id"]
    assert conn.execute(
        "SELECT duration_minutes FROM sessions WHERE id = %s", (session_id,)
    ).fetchone()[0] == 45
    payment_id = results["a-payment"]["record_id"]
    assert conn.execute(
        "SELECT amount_cents FROM payments WHERE id = %s", (payment_id,)
    ).fetchone()[0] == 3000
    ent_id = results["a-entitlement"]["record_id"]
    assert conn.execute(
        "SELECT count(*) FROM entitlements WHERE id = %s", (ent_id,)
    ).fetchone()[0] == 1

    # Every applied write carries a matching sync_log entry (Req 4.4).
    for entity, rid in (
        ("sessions", session_id),
        ("payments", payment_id),
        ("entitlements", ent_id),
    ):
        assert conn.execute(
            "SELECT count(*) FROM sync_log WHERE entity = %s AND record_id = %s",
            (entity, rid),
        ).fetchone()[0] >= 1


def test_sync_replay_is_idempotent_over_http(seeded_db):
    """Re-posting the same batch reports every action skipped (Req 4.2, 4.3)."""
    conn = seeded_db
    conn.execute(
        "UPDATE users SET password_hash = %s WHERE name = 'Aya'",
        (hash_password("pw"),),
    )
    loc = conn.execute(
        "SELECT id FROM locations WHERE name = 'Smithfield'"
    ).fetchone()[0]
    player_id = conn.execute(
        "INSERT INTO players (first_name, consent_status, location_id) "
        "VALUES ('Repeat', 'pending', %s) RETURNING id",
        (loc,),
    ).fetchone()[0]
    conn.commit()

    now = "2024-06-01T12:00:00+00:00"
    batch = {"actions": [
        {"client_id": "r-session", "entity": "session", "created_at": now,
         "payload": {"player_id": str(player_id), "session_type": "lounge",
                     "started_at": "2024-06-01T10:00:00+00:00",
                     "duration_minutes": 30}},
    ]}

    with build_client(connection=conn, config=_CONFIG) as client:
        token = client.post(
            "/auth/login", json={"identifier": "Aya", "password": "pw"}
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        first = client.post("/sync", json=batch, headers=headers).json()["results"]
        second = client.post("/sync", json=batch, headers=headers).json()["results"]

    assert first[0]["status"] == "applied"
    assert second[0]["status"] == "skipped"
    conn.rollback()
    assert conn.execute(
        "SELECT count(*) FROM sessions WHERE player_id = %s", (player_id,)
    ).fetchone()[0] == 1
