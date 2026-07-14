"""Shared helpers for FunHouse API tests (Spec 2).

Builds a FastAPI ``TestClient`` for the app returned by ``create_app()`` and
overrides the ``get_connection`` dependency so API tests run against the
disposable-schema connection provided by the Phase 0 ``db_connection`` fixture
(see ``tests/conftest.py``). Because ``db_connection`` skips gracefully when no
PostgreSQL server is reachable, DB-backed API tests skip cleanly too.

Endpoints that need no database (e.g. ``/health``) can use ``build_client()``
without providing a connection.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from fastapi.testclient import TestClient

from funhouse_api.app import create_app
from funhouse_api.config import ApiConfig, load_api_config
from funhouse_api.db import get_connection


@contextmanager
def build_client(
    *,
    connection: Any | None = None,
    config: ApiConfig | None = None,
) -> Iterator[TestClient]:
    """Yield a TestClient for a freshly-built app.

    Args:
        connection: When provided, the ``get_connection`` dependency is
            overridden to yield this connection (typically the ``db_connection``
            fixture's disposable-schema connection). The caller owns the
            connection's lifecycle; the override never closes it.
        config: Optional :class:`ApiConfig`; defaults to environment-loaded
            config.

    Yields:
        A configured ``TestClient``.
    """
    app = create_app(config if config is not None else load_api_config())

    if connection is not None:

        def _override() -> Iterator[Any]:
            # Yield the shared fixture connection without closing it: the
            # db_connection fixture owns teardown (rollback + schema drop).
            yield connection

        app.dependency_overrides[get_connection] = _override

    client = TestClient(app)
    try:
        yield client
    finally:
        app.dependency_overrides.clear()
