"""Thin migration entry point for the production schema apply (Spec 3.5, C6).

This is the command the deployment runbook invokes from an **ephemeral in-VPC
one-off** to apply the FunHouse schema to the private RDS instance:

    python -m funhouse_pipeline.db.apply_migrations

It adds **no** new migration logic. It reuses, unchanged:

* :func:`funhouse_pipeline.config.load_config` to read configuration from the
  environment (App Runner / the one-off task supplies ``DB_HOST``, ``DB_USER``,
  ``DB_PASSWORD``, ``DB_NAME``, and ``DB_SSLMODE=require`` — the DSN is built by
  :meth:`DatabaseConfig.dsn`);
* :func:`funhouse_pipeline.db.connect` to open the psycopg connection;
* :func:`funhouse_pipeline.db.run_migrations` to apply the packaged ``sql/*.sql``
  files idempotently and report per-table created / already-present status.

The migration runner is idempotent by construction, so re-running this command
is a safe no-op (Req 3.2).
"""

from __future__ import annotations

import sys
from typing import Sequence

from funhouse_pipeline.config import load_config
from funhouse_pipeline.db import connect, run_migrations
from funhouse_pipeline.db.maintenance import assume_maintenance_role


def apply(config_path: str | None = None) -> int:
    """Load config, connect, run migrations, and print the summary.

    Args:
        config_path: Optional path to a YAML config file. When ``None`` the
            configuration comes from environment variables alone (the runbook
            supplies ``DB_*`` including ``DB_SSLMODE=require``).

    Returns:
        Process exit code: ``0`` on success.
    """
    config = load_config(config_path)

    # Surface the target + TLS mode without ever printing the password.
    print(
        f"Applying migrations to {config.database.host}:{config.database.port}"
        f"/{config.database.dbname} (sslmode={config.database.sslmode})"
    )

    conn = connect(config)
    try:
        assume_maintenance_role(conn)
        result = run_migrations(conn)
    finally:
        conn.close()

    print(result.summary())
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI wrapper: optional first arg is a config-file path."""
    args = list(sys.argv[1:] if argv is None else argv)
    config_path = args[0] if args else None
    return apply(config_path)


if __name__ == "__main__":  # pragma: no cover - exercised via the module CLI
    raise SystemExit(main())
