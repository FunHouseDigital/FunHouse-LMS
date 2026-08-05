"""Thin reference-data seed entry point for production deploys (Spec 3.5).

Companion to :mod:`funhouse_pipeline.db.apply_migrations`. It is the command the
container start-up path (``funhouse_api/docker-entrypoint.sh``) invokes when
``RUN_SEED_ON_START`` is truthy, and it can also be run manually from an
ephemeral in-VPC one-off:

    python -m funhouse_pipeline.db.apply_seed

It adds **no** new seeding logic. It reuses, unchanged:

* :func:`funhouse_pipeline.config.load_config` to read configuration from the
  environment (the same ``DB_HOST`` / ``DB_USER`` / ``DB_PASSWORD`` / ``DB_NAME`` /
  ``DB_SSLMODE=require`` variables App Runner already injects);
* :func:`funhouse_pipeline.db.connect` to open the psycopg connection;
* :func:`funhouse_pipeline.db.seed` to insert the founding reference data
  idempotently (each row is inserted only when absent — Req 2.8).

Seeding is idempotent by construction, so re-running this command is a safe
no-op. The schema must already be deployed (run
:mod:`funhouse_pipeline.db.apply_migrations` first).
"""

from __future__ import annotations

import sys
from typing import Sequence

from funhouse_pipeline.config import load_config
from funhouse_pipeline.db import connect, seed
from funhouse_pipeline.db.maintenance import assume_maintenance_role


def apply(config_path: str | None = None) -> int:
    """Load config, connect, seed reference data, and print the summary.

    Args:
        config_path: Optional path to a YAML config file. When ``None`` the
            configuration comes from environment variables alone (the deploy
            path supplies ``DB_*`` including ``DB_SSLMODE=require``).

    Returns:
        Process exit code: ``0`` on success.
    """
    config = load_config(config_path)

    # Surface the target + TLS mode without ever printing the password.
    print(
        f"Seeding reference data into {config.database.host}:{config.database.port}"
        f"/{config.database.dbname} (sslmode={config.database.sslmode})"
    )

    conn = connect(config)
    try:
        assume_maintenance_role(conn)
        result = seed(conn)
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
