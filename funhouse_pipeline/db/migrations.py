"""Deterministic schema migration runner.

Applies the packaged ``.sql`` migration files against a PostgreSQL database and
reports, per table, whether it was newly created or was already present
(Req 1.6). Because the schema uses ``CREATE TABLE IF NOT EXISTS`` and the
consents enforcement is written with ``CREATE OR REPLACE`` / ``DROP ... IF
EXISTS``, the runner is safe to re-run: existing tables and their rows are left
intact (Req 1.6).

The runner accepts any DB-API 2.0 connection (psycopg in production) so it can
be unit tested without binding to a specific driver instance.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

# The 14 tables the schema deploys (Req 1.1). Order matches the design.
EXPECTED_TABLES: tuple[str, ...] = (
    "locations",
    "schools",
    "users",
    "players",
    "guardians",
    "consents",
    "products",
    "entitlements",
    "sessions",
    "attendance",
    "payments",
    "lessons",
    "student_metrics",
    "sync_log",
)

# Columns the design mandates on every table (Req 1.2, 1.3).
UNIVERSAL_COLUMNS: tuple[str, ...] = ("id", "created_at", "updated_at", "location_id")

# Tables that represent school-associated data and therefore carry school_id (Req 1.4).
SCHOOL_ASSOCIATED_TABLES: tuple[str, ...] = ("players", "sessions", "attendance", "lessons")

# The only values permitted for student_metrics.metric_type (Req 1.7).
ALLOWED_METRIC_TYPES: tuple[str, ...] = (
    "typing_wpm",
    "typing_accuracy",
    "homework_done",
    "quiz_score",
    "observation",
)

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


@dataclass(frozen=True)
class TableStatus:
    """Outcome for a single table after a migration run."""

    name: str
    status: str  # "created" | "already_present"


@dataclass(frozen=True)
class MigrationResult:
    """Summary of a migration run."""

    tables: tuple[TableStatus, ...]
    applied_files: tuple[str, ...]

    def created(self) -> list[str]:
        return [t.name for t in self.tables if t.status == "created"]

    def already_present(self) -> list[str]:
        return [t.name for t in self.tables if t.status == "already_present"]

    def summary(self) -> str:
        created = ", ".join(self.created()) or "(none)"
        present = ", ".join(self.already_present()) or "(none)"
        return (
            f"Applied migrations: {', '.join(self.applied_files)}\n"
            f"  Created: {created}\n"
            f"  Already present: {present}"
        )


def sql_dir() -> Path:
    """Return the directory containing packaged ``.sql`` migration files."""
    return _SQL_DIR


def migration_files() -> list[Path]:
    """Return migration ``.sql`` files sorted by filename (lexical = ordinal)."""
    return sorted(_SQL_DIR.glob("*.sql"))


def _existing_tables(cursor: Any, names: Sequence[str]) -> set[str]:
    """Return the subset of ``names`` that already exist in the current schema."""
    cursor.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = current_schema()
          AND table_name = ANY(%s)
        """,
        (list(names),),
    )
    return {row[0] for row in cursor.fetchall()}


def table_exists(conn: Any, table: str) -> bool:
    """Return True if ``table`` exists in the connection's current schema."""
    with conn.cursor() as cursor:
        return bool(_existing_tables(cursor, [table]))


def table_columns(conn: Any, table: str) -> set[str]:
    """Return the set of column names for ``table`` in the current schema."""
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = %s
            """,
            (table,),
        )
        return {row[0] for row in cursor.fetchall()}


def run_migrations(
    conn: Any,
    *,
    sql_files: Iterable[Path] | None = None,
    expected_tables: Sequence[str] = EXPECTED_TABLES,
) -> MigrationResult:
    """Apply migrations against ``conn`` and report per-table status.

    The presence of each expected table is sampled BEFORE any SQL runs; tables
    present beforehand are reported as ``already_present`` and are left intact
    (Req 1.6), while tables that appear only afterwards are reported as
    ``created``.

    Args:
        conn: An open DB-API connection (psycopg in production).
        sql_files: Migration files to apply, in order. Defaults to the packaged
            files returned by :func:`migration_files`.
        expected_tables: Table names whose create/present status is reported.

    Returns:
        A :class:`MigrationResult` describing what happened. The transaction is
        committed on success.
    """
    files = list(sql_files) if sql_files is not None else migration_files()

    with conn.cursor() as cursor:
        before = _existing_tables(cursor, expected_tables)
        for path in files:
            statements = Path(path).read_text(encoding="utf-8")
            cursor.execute(statements)
        after = _existing_tables(cursor, expected_tables)

    conn.commit()

    statuses: list[TableStatus] = []
    for name in expected_tables:
        if name in before:
            statuses.append(TableStatus(name=name, status="already_present"))
        elif name in after:
            statuses.append(TableStatus(name=name, status="created"))
        # A table neither before nor after would indicate a failed create; it is
        # simply omitted from the report so callers can detect the gap.

    return MigrationResult(
        tables=tuple(statuses),
        applied_files=tuple(Path(p).name for p in files),
    )
