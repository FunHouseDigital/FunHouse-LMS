"""Tests for the deployment migration CLI shim (Spec 3.5, C6).

The shim (``funhouse_pipeline.db.apply_migrations``) is deliberately thin: it
must delegate to the existing ``run_migrations`` against a live connection and
print the created / already-present report. Two layers of coverage:

* A **delegation unit test** (no PostgreSQL required) that stubs the reused
  helpers and asserts the shim wires ``load_config -> connect -> run_migrations``
  correctly, prints the summary, closes the connection, and returns 0.
* A **DB-backed idempotency test** that runs the shim against the real schema via
  the shared ``db_connection`` fixture and confirms a second run is a safe no-op
  (Req 3.2). It skips gracefully when no PostgreSQL server is reachable.
"""

from __future__ import annotations

import pytest

from funhouse_pipeline.config import Config, DatabaseConfig
from funhouse_pipeline.db import apply_migrations
from funhouse_pipeline.db.migrations import MigrationResult, TableStatus


class _FakeConn:
    """Minimal connection double that records whether it was closed."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_apply_delegates_to_run_migrations_and_prints_summary(monkeypatch, capsys):
    """The shim reuses connect() + run_migrations() and prints the report."""
    fake_conn = _FakeConn()
    calls: dict[str, object] = {}

    cfg = Config(
        database=DatabaseConfig(
            host="rds.internal", port=5432, dbname="funhouse", user="u", sslmode="require"
        ),
        s3_bucket=None,
        region="af-south-1",
    )

    def fake_load_config(path=None):
        calls["config_path"] = path
        return cfg

    def fake_connect(config, **kwargs):
        calls["connected_with"] = config
        return fake_conn

    result = MigrationResult(
        tables=(
            TableStatus(name="locations", status="created"),
            TableStatus(name="users", status="already_present"),
        ),
        applied_files=("001_schema.sql",),
    )

    def fake_run_migrations(conn):
        calls["run_migrations_conn"] = conn
        return result

    monkeypatch.setattr(apply_migrations, "load_config", fake_load_config)
    monkeypatch.setattr(apply_migrations, "connect", fake_connect)
    monkeypatch.setattr(apply_migrations, "run_migrations", fake_run_migrations)

    rc = apply_migrations.apply()

    assert rc == 0
    # Delegated to the reused runner against the connection connect() returned.
    assert calls["connected_with"] is cfg
    assert calls["run_migrations_conn"] is fake_conn
    # Connection is always closed.
    assert fake_conn.closed is True

    out = capsys.readouterr().out
    # Prints the created/already-present report and never the password.
    assert "Created: locations" in out
    assert "Already present: users" in out
    assert "sslmode=require" in out


def test_main_passes_config_path_argument(monkeypatch):
    """The CLI wrapper forwards an optional config-file path to apply()."""
    seen: dict[str, object] = {}

    def fake_apply(config_path=None):
        seen["config_path"] = config_path
        return 0

    monkeypatch.setattr(apply_migrations, "apply", fake_apply)

    assert apply_migrations.main(["config.yaml"]) == 0
    assert seen["config_path"] == "config.yaml"

    assert apply_migrations.main([]) == 0
    assert seen["config_path"] is None


@pytest.mark.db
def test_apply_against_real_schema_is_idempotent(db_connection, monkeypatch, capsys):
    """Running the shim twice converges to the same schema (Req 3.2).

    Uses the shared disposable-schema fixture. The shim's config load and
    connect are redirected to the fixture connection so the real
    ``run_migrations`` executes the packaged SQL against a live PostgreSQL.
    """
    class _NoCloseProxy:
        """Delegates everything to the fixture conn but ignores close()."""

        def __init__(self, conn):
            self._conn = conn

        def __getattr__(self, name):
            return getattr(self._conn, name)

        def close(self):  # keep the fixture in charge of teardown
            pass

    proxy = _NoCloseProxy(db_connection)

    cfg = Config(database=DatabaseConfig(host="localhost", sslmode="require"))
    monkeypatch.setattr(apply_migrations, "load_config", lambda path=None: cfg)
    monkeypatch.setattr(apply_migrations, "connect", lambda config, **kw: proxy)

    assert apply_migrations.apply() == 0
    first = capsys.readouterr().out
    assert "Created:" in first

    # Second run: every expected table is already present (idempotent no-op).
    assert apply_migrations.apply() == 0
    second = capsys.readouterr().out
    assert "Created: (none)" in second
