"""PostgreSQL connection helpers built on psycopg (v3).

These helpers translate a :class:`~funhouse_pipeline.config.Config` into a live
connection. They are deliberately thin: the migration runner and later stages
accept any DB-API connection so they remain easy to test.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from funhouse_pipeline.config import Config

if TYPE_CHECKING:  # pragma: no cover - typing only
    import psycopg


def connect(config: Config, **overrides: Any) -> "psycopg.Connection":
    """Open a psycopg connection using the configured DSN.

    Args:
        config: Loaded pipeline configuration.
        **overrides: Extra keyword arguments forwarded to ``psycopg.connect``
            (e.g. ``autocommit=True``, ``connect_timeout=3``).

    Returns:
        An open psycopg connection. The caller owns closing it.
    """
    import psycopg  # imported lazily so importing this module never requires the driver

    return psycopg.connect(config.database.dsn(), **overrides)


def can_connect(config: Config, *, connect_timeout: int = 3) -> bool:
    """Return True if a PostgreSQL server is reachable with this config.

    Never raises: any connection error results in ``False``. Used by tests to
    decide whether DB-backed cases can run or must be skipped when no server is
    available (see the environment notes for this feature).
    """
    try:
        import psycopg
    except ModuleNotFoundError:
        return False

    try:
        conn = psycopg.connect(config.database.dsn(), connect_timeout=connect_timeout)
    except Exception:
        return False
    else:
        conn.close()
        return True
