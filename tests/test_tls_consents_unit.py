"""Example tests for the TLS backstop and the append-only consents trigger.

Covers Task 15.6:

* TLS-unavailable rejection when ``tls_required`` -- a request that did not
  arrive over HTTPS (no/other ``X-Forwarded-Proto``) is rejected rather than
  served (Req 14.7); a request marked HTTPS by the edge proxy is served; and
  when TLS is not required the middleware is a no-op.
* The ``consents`` ``UPDATE``/``DELETE`` trigger backstop -- the append-only
  ledger rejects any attempt to mutate an existing consent row (Req 14.4).

The TLS cases need no database (public ``/health``); the consents cases require a
reachable PostgreSQL and skip otherwise.
"""

from __future__ import annotations

import pytest

from funhouse_api.config import ApiConfig
from funhouse_pipeline.config import Config, DatabaseConfig
from funhouse_pipeline.db.migrations import run_migrations
from funhouse_pipeline.db.seed import seed
from tests.api_helpers import build_client

_TLS_CONFIG = ApiConfig(
    pipeline=Config(database=DatabaseConfig()),
    jwt_secret="tls-example-secret",
    tls_required=True,
)
_NO_TLS_CONFIG = ApiConfig(
    pipeline=Config(database=DatabaseConfig()),
    jwt_secret="tls-example-secret",
    tls_required=False,
)


def test_tls_required_rejects_non_https_request():
    """When TLS is required, a plain-HTTP request is rejected (Req 14.7)."""
    with build_client(config=_TLS_CONFIG) as client:
        # No X-Forwarded-Proto header -> not HTTPS -> rejected.
        resp = client.get("/health")
        assert resp.status_code == 403
        assert resp.json()["detail"] == "TLS required"

        # An http forwarded-proto is likewise rejected.
        resp_http = client.get("/health", headers={"X-Forwarded-Proto": "http"})
        assert resp_http.status_code == 403


def test_tls_required_allows_https_forwarded_request():
    """A request the edge marked as HTTPS is served (Req 14.3)."""
    with build_client(config=_TLS_CONFIG) as client:
        resp = client.get("/health", headers={"X-Forwarded-Proto": "https"})
        assert resp.status_code == 200


def test_tls_not_required_is_noop():
    """When TLS is not required, plain HTTP is served (local/dev default)."""
    with build_client(config=_NO_TLS_CONFIG) as client:
        resp = client.get("/health")
        assert resp.status_code == 200


@pytest.mark.db
class TestConsentsAppendOnlyTrigger:
    """The DB trigger backstop rejects UPDATE/DELETE on consents (Req 14.4)."""

    @pytest.fixture
    def consent_row(self, db_connection):
        run_migrations(db_connection)
        seed(db_connection)
        loc = db_connection.execute(
            "SELECT id FROM locations WHERE name = 'Smithfield'"
        ).fetchone()[0]
        player_id = db_connection.execute(
            "INSERT INTO players (first_name, consent_status, location_id) "
            "VALUES ('Trig', 'pending', %s) RETURNING id",
            (loc,),
        ).fetchone()[0]
        consent_id = db_connection.execute(
            "INSERT INTO consents (player_id, consent_type, granted, granted_at, "
            "location_id) VALUES (%s, 'data_processing', true, now(), %s) RETURNING id",
            (player_id, loc),
        ).fetchone()[0]
        db_connection.commit()
        return db_connection, consent_id

    def test_update_consents_raises(self, consent_row):
        conn, consent_id = consent_row
        with pytest.raises(Exception):
            conn.execute(
                "UPDATE consents SET granted = false WHERE id = %s", (consent_id,)
            )
        conn.rollback()

    def test_delete_consents_raises(self, consent_row):
        conn, consent_id = consent_row
        with pytest.raises(Exception):
            conn.execute("DELETE FROM consents WHERE id = %s", (consent_id,))
        conn.rollback()
