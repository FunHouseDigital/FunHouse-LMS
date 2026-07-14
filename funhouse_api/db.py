"""Database connection dependency for the API (Spec 2).

Reuses the Phase 0 ``funhouse_pipeline.db.connection.connect`` helper so the API
opens connections through the exact same portable, DSN-driven path as the
pipeline (Req 13.4). ``get_connection`` is a FastAPI dependency that yields a
live psycopg connection and always closes it afterwards.

In tests, ``get_connection`` is overridden (see ``tests/api_helpers.py``) to
hand back the disposable-schema connection from the Phase 0 ``db_connection``
fixture, so API tests run against an isolated schema and skip gracefully when no
PostgreSQL server is reachable.
"""

from __future__ import annotations

from typing import Any, Iterator

from funhouse_api.config import ApiConfig, load_api_config
from funhouse_pipeline.db.connection import connect


def get_connection() -> Iterator[Any]:
    """FastAPI dependency yielding an open psycopg connection.

    The connection is opened from the reused Phase 0 ``connect`` helper using the
    portable libpq DSN and closed when the request completes.
    """
    config: ApiConfig = load_api_config()
    conn = connect(config.pipeline)
    try:
        yield conn
    finally:
        conn.close()
