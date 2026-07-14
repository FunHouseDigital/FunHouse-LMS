"""Example/unit tests for the players endpoints (Task 8.4, 8.7; Req 6.6, 6.9, 6.8).

* Missing ``first_name`` -> 422 (Req 6.6).
* Zero consents -> 422 (Req 6.9).
* A player history read that falls entirely outside the caller's scope returns
  an empty history (Req 6.8).

These exercise the full stack over the FastAPI ``TestClient`` and require a
reachable PostgreSQL (the endpoints resolve the DB dependency); they skip
otherwise.
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
    jwt_secret="players-unit-secret",
    jwt_ttl_seconds=3600,
)


@pytest.fixture
def seeded_db(db_connection):
    run_migrations(db_connection)
    seed(db_connection)
    return db_connection


def _headers(user_id, role, location_id=None, school_id=None) -> dict[str, str]:
    token = issue_token(
        AuthUser(
            id=str(user_id),
            role=role,
            location_id=None if location_id is None else str(location_id),
            school_id=None if school_id is None else str(school_id),
        ),
        now=datetime.now(timezone.utc),
        secret=_CONFIG.jwt_secret,
        ttl_seconds=_CONFIG.jwt_ttl_seconds,
    )
    return {"Authorization": f"Bearer {token}"}


def test_missing_first_name_returns_422(seeded_db):
    conn = seeded_db
    aya = conn.execute("SELECT id FROM users WHERE name = 'Aya'").fetchone()[0]
    headers = _headers(aya, "founder")
    loc = conn.execute("SELECT id FROM locations LIMIT 1").fetchone()[0]
    with build_client(connection=conn, config=_CONFIG) as client:
        resp = client.post(
            "/players",
            json={
                "location_id": str(loc),
                "consents": [{"consent_type": "data_processing"}],
            },
            headers=headers,
        )
    assert resp.status_code == 422, resp.text


def test_zero_consents_returns_422(seeded_db):
    conn = seeded_db
    aya = conn.execute("SELECT id FROM users WHERE name = 'Aya'").fetchone()[0]
    loc = conn.execute("SELECT id FROM locations LIMIT 1").fetchone()[0]
    headers = _headers(aya, "founder")
    with build_client(connection=conn, config=_CONFIG) as client:
        resp = client.post(
            "/players",
            json={"first_name": "Zola", "location_id": str(loc), "consents": []},
            headers=headers,
        )
    assert resp.status_code == 422, resp.text


def test_out_of_scope_history_is_empty(seeded_db):
    conn = seeded_db
    aya = conn.execute("SELECT id FROM users WHERE name = 'Aya'").fetchone()[0]
    loc_a = conn.execute("SELECT id FROM locations LIMIT 1").fetchone()[0]
    loc_b = conn.execute(
        "INSERT INTO locations (name) VALUES ('OtherLoc') RETURNING id"
    ).fetchone()[0]
    # Player + a session at location A.
    player_id = conn.execute(
        "INSERT INTO players (first_name, location_id) VALUES ('Zola', %s) RETURNING id",
        (loc_a,),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO sessions (player_id, session_type, location_id) "
        "VALUES (%s, 'lounge', %s)",
        (player_id, loc_a),
    )
    conn.commit()

    # A manager scoped to location B sees an empty history for the A player.
    headers = _headers(aya, "manager", location_id=loc_b)
    with build_client(connection=conn, config=_CONFIG) as client:
        resp = client.get(f"/players/{player_id}/history", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sessions"] == []
    assert body["payments"] == []
    assert body["entitlement_draws"] == []
