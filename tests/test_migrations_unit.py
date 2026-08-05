"""Unit tests for the migration runner that do not require a database.

These validate the packaged migration assets and the runner's static metadata
so the harness has meaningful coverage even where no PostgreSQL server exists.
"""

from __future__ import annotations

from funhouse_pipeline.db import migrations


def test_expected_tables_are_the_fourteen_design_tables():
    assert len(migrations.EXPECTED_TABLES) == 14
    assert set(migrations.EXPECTED_TABLES) == {
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
    }


def test_migration_files_are_present_and_ordered():
    files = migrations.migration_files()
    names = [f.name for f in files]
    assert names == [
        "001_schema.sql",
        "002_consents_append_only.sql",
        "003_role_facilitator.sql",
        "004_users_school_id.sql",
        "005_public_schema_lockdown.sql",
        "006_consents_function_search_path.sql",
    ]


def test_schema_sql_creates_every_expected_table_idempotently():
    schema_sql = (migrations.sql_dir() / "001_schema.sql").read_text(encoding="utf-8")
    for table in migrations.EXPECTED_TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in schema_sql


def test_metric_type_check_constraint_lists_allowed_values():
    schema_sql = (migrations.sql_dir() / "001_schema.sql").read_text(encoding="utf-8")
    for value in migrations.ALLOWED_METRIC_TYPES:
        assert f"'{value}'" in schema_sql


def test_consents_append_only_migration_declares_trigger():
    sql = (migrations.sql_dir() / "002_consents_append_only.sql").read_text(encoding="utf-8")
    assert "trg_consents_append_only" in sql
    assert "BEFORE UPDATE OR DELETE ON consents" in sql


def test_school_associated_tables_have_school_id_column_in_sql():
    schema_sql = (migrations.sql_dir() / "001_schema.sql").read_text(encoding="utf-8")
    # A light structural check: each school-associated table's block references school_id.
    for table in migrations.SCHOOL_ASSOCIATED_TABLES:
        start = schema_sql.index(f"CREATE TABLE IF NOT EXISTS {table}")
        block = schema_sql[start : schema_sql.index(");", start)]
        assert "school_id" in block, f"{table} should define school_id (Req 1.4)"
