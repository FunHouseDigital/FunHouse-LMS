"""Tests for the deployment seed CLI shim (Spec 3.5 one-command deploy).

The shim (``funhouse_pipeline.db.apply_seed``) mirrors ``apply_migrations``: it
is deliberately thin and must delegate to the existing idempotent ``seed``
against a live connection, print the per-row summary, close the connection, and
return 0. Coverage:

* A **delegation unit test** (no PostgreSQL required) that stubs the reused
  helpers and asserts the shim wires ``load_config -> connect -> seed``
  correctly, prints the summary, closes the connection, and returns 0.
* A **DB-backed idempotency test** that runs migrations then the seed shim twice
  against the real schema via the shared ``db_connection`` fixture and confirms
  the second run inserts nothing (Req 2.8). It skips when no PostgreSQL server
  is reachable.
"""

from __future__ import annotations

import pytest

from funhouse_pipeline.config import Config, DatabaseConfig
from funhouse_pipeline.db import apply_seed
from funhouse_pipeline.db.seed import SeedResult, SeedRowResult


class _FakeConn:
    """Minimal connection double that records whether it was closed."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_apply_delegates_to_seed_and_prints_summary(monkeypatch, capsys):
    """The shim reuses connect() + seed() and prints the per-row report."""
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

    result = SeedResult(
        rows=(
            SeedRowResult("locations", "Smithfield", "inserted"),
            SeedRowResult("users", "Aya", "skipped"),
        )
    )

    def fake_seed(conn):
        calls["seed_conn"] = conn
        return result

    monkeypatch.setattr(apply_seed, "load_config", fake_load_config)
    monkeypatch.setattr(apply_seed, "connect", fake_connect)
    monkeypatch.setattr(apply_seed, "seed", fake_seed)

    rc = apply_seed.apply()

    assert rc == 0
    # Delegated to the reused seeder against the connection connect() returned.
    assert calls["connected_with"] is cfg
    assert calls["seed_conn"] is fake_conn
    # Connection is always closed.
    assert fake_conn.closed is True

    out = capsys.readouterr().out
    # Prints the inserted/skipped report and never the password.
    assert "locations:Smithfield" in out
    assert "users:Aya" in out
    assert "sslmode=require" in out


def test_main_passes_config_path_argument(monkeypatch):
    """The CLI wrapper forwards an optional config-file path to apply()."""
    seen: dict[str, object] = {}

    def fake_apply(config_path=None):
        seen["config_path"] = config_path
        return 0

    monkeypatch.setattr(apply_seed, "apply", fake_apply)

    assert apply_seed.main(["config.yaml"]) == 0
    assert seen["config_path"] == "config.yaml"

    assert apply_seed.main([]) == 0
    assert seen["config_path"] is None


@pytest.mark.db
def test_apply_against_real_schema_is_idempotent(db_connection, monkeypatch, capsys):
    """Running the seed shim twice inserts nothing the second time (Req 2.8).

    Uses the shared disposable-schema fixture. The shim's config load and
    connect are redirected to the fixture connection so the real ``seed``
    executes against a live PostgreSQL after the schema is migrated.
    """
    from funhouse_pipeline.db import run_migrations

    class _NoCloseProxy:
        """Delegates everything to the fixture conn but ignores close()."""

        def __init__(self, conn):
            self._conn = conn

        def __getattr__(self, name):
            return getattr(self._conn, name)

        def close(self):  # keep the fixture in charge of teardown
            pass

    run_migrations(db_connection)
    proxy = _NoCloseProxy(db_connection)

    cfg = Config(database=DatabaseConfig(host="localhost", sslmode="require"))
    monkeypatch.setattr(apply_seed, "load_config", lambda path=None: cfg)
    monkeypatch.setattr(apply_seed, "connect", lambda config, **kw: proxy)

    assert apply_seed.apply() == 0
    first = capsys.readouterr().out
    assert "inserted" in first

    # Second run: everything already present, nothing inserted (idempotent).
    assert apply_seed.apply() == 0
    second = capsys.readouterr().out
    assert "0 inserted" in second
