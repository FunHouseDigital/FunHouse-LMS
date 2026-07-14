"""Integration tests for the session endpoint (Task 9.2, Req 7.1, 7.4, 7.5).

Exercises the full stack over the FastAPI ``TestClient``:

* Happy path -- logging a session sets ``logged_by`` to the acting user and
  appends a ``sync_log`` entry (Req 7.1, 7.5).
* A session referencing a player outside the caller's scope -> ``403`` (Req 7.4).

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
    jwt_secret="sessions-integration-secret",
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


def test_session_happy_path_sets_logged_by_and_audits(seeded_db):
    conn = seeded_db
    aya = conn.execute("SELECT id FROM users WHERE name = 'Aya'").fetchone()[0]
    loc = conn.execute("SELECT id FROM locations WHERE name = 'Smithfield'").fetchone()[0]
    player_id = conn.execute(
        "INSERT INTO players (first_name, location_id) VALUES ('Zola', %s) RETURNING id",
        (loc,),
    ).fetchone()[0]
    conn.commit()

    headers = _headers(aya, "founder")
    with build_client(connection=conn, config=_CONFIG) as client:
        resp = client.post(
            "/sessions",
            json={
                "player_id": str(player_id),
                "session_type": "lounge",
                "duration_minutes": 30,
            },
            headers=headers,
        )
    assert resp.status_code == 201, resp.text
    session_id = resp.json()["id"]
    assert str(resp.json()["logged_by"]) == str(aya)

    # logged_by persisted and a sync_log insert entry appended (Req 7.1, 7.5).
    row = conn.execute(
        "SELECT logged_by FROM sessions WHERE id = %s", (session_id,)
    ).fetchone()
    assert str(row[0]) == str(aya)
    audit = conn.execute(
        "SELECT count(*) FROM sync_log WHERE entity = 'sessions' "
        "AND record_id = %s AND action = 'insert'",
        (session_id,),
    ).fetchone()[0]
    assert audit == 1


def test_session_out_of_scope_player_rejected(seeded_db):
    conn = seeded_db
    aya = conn.execute("SELECT id FROM users WHERE name = 'Aya'").fetchone()[0]
    loc_a = conn.execute("SELECT id FROM locations WHERE name = 'Smithfield'").fetchone()[0]
    loc_b = conn.execute(
        "INSERT INTO locations (name) VALUES ('FarLoc') RETURNING id"
    ).fetchone()[0]
    # Player at location A.
    player_id = conn.execute(
        "INSERT INTO players (first_name, location_id) VALUES ('Zola', %s) RETURNING id",
        (loc_a,),
    ).fetchone()[0]
    conn.commit()

    # Manager scoped to location B may not log a session for the A player.
    headers = _headers(aya, "manager", location_id=loc_b)
    before = conn.execute("SELECT count(*) FROM sessions").fetchone()[0]
    with build_client(connection=conn, config=_CONFIG) as client:
        resp = client.post(
            "/sessions",
            json={
                "player_id": str(player_id),
                "session_type": "lounge",
                "duration_minutes": 30,
            },
            headers=headers,
        )
    assert resp.status_code == 403, resp.text
    after = conn.execute("SELECT count(*) FROM sessions").fetchone()[0]
    assert after == before  # nothing persisted
