"""Database access: connection helpers and the migration runner.

PostgreSQL is the only database system used by the pipeline (Req 6.4).
"""

from funhouse_pipeline.db.connection import (
    can_connect,
    connect,
)
from funhouse_pipeline.db.migrations import (
    ALLOWED_METRIC_TYPES,
    EXPECTED_TABLES,
    SCHOOL_ASSOCIATED_TABLES,
    UNIVERSAL_COLUMNS,
    MigrationResult,
    TableStatus,
    migration_files,
    run_migrations,
    sql_dir,
    table_columns,
    table_exists,
)
from funhouse_pipeline.db.seed import (
    PARTNER_SCHOOLS,
    PROPOSED_SCHOOLS,
    SEED_PRODUCTS,
    SEED_USERS,
    SMITHFIELD_LOCATION,
    TOTAL_SEED_ROWS,
    SeedProduct,
    SeedResult,
    SeedRowResult,
    SeedUser,
    seed,
)

__all__ = [
    "connect",
    "can_connect",
    "ALLOWED_METRIC_TYPES",
    "EXPECTED_TABLES",
    "SCHOOL_ASSOCIATED_TABLES",
    "UNIVERSAL_COLUMNS",
    "MigrationResult",
    "TableStatus",
    "migration_files",
    "run_migrations",
    "sql_dir",
    "table_columns",
    "table_exists",
    # Seeding (Req 2)
    "seed",
    "SeedResult",
    "SeedRowResult",
    "SeedProduct",
    "SeedUser",
    "PARTNER_SCHOOLS",
    "PROPOSED_SCHOOLS",
    "SEED_PRODUCTS",
    "SEED_USERS",
    "SMITHFIELD_LOCATION",
    "TOTAL_SEED_ROWS",
]
