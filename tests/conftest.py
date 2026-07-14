"""Shared pytest fixtures for the FunHouse pipeline test suite.

Database strategy
-----------------
DB-backed tests need a real PostgreSQL because the schema relies on Postgres
features (``gen_random_uuid()``, JSONB, CHECK constraints, triggers). A live
server may not be available in every environment, so this module resolves a
connection in the following order and **skips DB-backed tests gracefully** when
none is reachable (rather than failing the whole suite):

1. ``FUNHOUSE_TEST_DSN`` environment variable (a libpq connection string), if set.
2. The default local configuration (``localhost:5432``), if reachable.

When a server IS reachable, the ``db_connection`` fixture gives each test an
isolated, disposable **schema** (created up front, dropped ``CASCADE`` at
teardown) and runs the test inside a transaction that is **rolled back**
afterwards. The unique-schema layer means even code that commits internally
(e.g. the migration runner) leaves nothing behind between tests.

To point the suite at a database, e.g.::

    FUNHOUSE_TEST_DSN="host=localhost port=5432 dbname=funhouse_test user=postgres" \\
        pytest -m db
"""

from __future__ import annotations

import os
import uuid
from typing import Iterator

import pytest

from funhouse_pipeline.config import Config, DatabaseConfig, load_config

_SKIP_REASON = (
    "No PostgreSQL server reachable. Set FUNHOUSE_TEST_DSN to a libpq "
    "connection string to enable DB-backed tests."
)


def _resolve_dsn() -> str | None:
    """Return a libpq DSN to test against, or ``None`` if none is reachable."""
    try:
        import psycopg  # noqa: F401
    except ModuleNotFoundError:
        return None

    dsn_env = os.environ.get("FUNHOUSE_TEST_DSN")
    if dsn_env:
        candidates = [dsn_env]
    else:
        # Fall back to the default local configuration.
        candidates = [load_config().database.dsn()]

    import psycopg

    for dsn in candidates:
        try:
            conn = psycopg.connect(dsn, connect_timeout=3)
        except Exception:
            continue
        else:
            conn.close()
            return dsn
    return None


@pytest.fixture(scope="session")
def pg_dsn() -> str:
    """Session-wide DSN for a reachable server, or skip the whole DB group."""
    dsn = _resolve_dsn()
    if dsn is None:
        pytest.skip(_SKIP_REASON)
    return dsn


@pytest.fixture
def db_connection(pg_dsn: str) -> Iterator["object"]:
    """Yield an isolated psycopg connection scoped to a disposable schema.

    Isolation model:
      * A unique schema ``test_<hex>`` is created and made first on the
        ``search_path`` so all unqualified DDL/DML lands there.
      * The test runs with autocommit off; at teardown the open transaction is
        rolled back (per-test transactional rollback) and the schema is dropped
        ``CASCADE`` to remove anything that was committed.
    """
    import psycopg

    schema = f"test_{uuid.uuid4().hex}"

    admin = psycopg.connect(pg_dsn, autocommit=True)
    admin.execute(f'CREATE SCHEMA "{schema}"')

    conn = psycopg.connect(pg_dsn)
    try:
        # Route unqualified names into the disposable schema; keep public for
        # shared objects/extensions.
        conn.execute(f'SET search_path TO "{schema}", public')
        conn.commit()
        yield conn
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()
            admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            admin.close()


@pytest.fixture
def default_config() -> Config:
    """A minimal valid Config for unit tests that do not touch a database."""
    return Config(
        database=DatabaseConfig(host="localhost", dbname="funhouse", user="funhouse"),
        s3_bucket="funhouse-archive-test",
        region="af-south-1",
        llm_provider="bedrock",
        confidence_threshold=0.7,
    )
