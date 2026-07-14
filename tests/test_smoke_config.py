"""Smoke / configuration checks (Task 15.2).

Single-execution checks (not property tests) that assert one-time deployment and
configuration facts drawn from the design's Testing Strategy:

* All 14 tables exist after a deploy (Req 1.1).
* Region defaults/pins to ``af-south-1`` for the DB and S3 (Req 1.5, 12.2, 14.4);
  TLS is used in transit (Req 14.3); encryption at rest (Req 14.2) is asserted at
  the documentation level, since a local PostgreSQL cannot emulate RDS storage
  encryption.
* No dependency on Pinpoint / DynamoDB / Cognito / Lambda-as-architecture, and
  only a PostgreSQL driver is present (Req 6.3, 6.4).
* Command documentation exists (Req 13.2).
* The container/CLI runs locally on the operator machine, fully offline (Req 15.1).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from funhouse_pipeline.archive.archiver import DEFAULT_REGION as ARCHIVE_DEFAULT_REGION
from funhouse_pipeline.config import Config, load_config
from funhouse_pipeline.config.settings import DEFAULT_REGION
from funhouse_pipeline.db.migrations import EXPECTED_TABLES, run_migrations

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PACKAGE_ROOT = _REPO_ROOT / "funhouse_pipeline"
_DOCS = _REPO_ROOT / "docs" / "pipeline-command.md"


# --------------------------------------------------------------------------- #
# All 14 tables exist after deploy (Req 1.1)
# --------------------------------------------------------------------------- #


@pytest.mark.db
def test_all_fourteen_tables_exist_after_deploy(db_connection):
    """After a real migration deploy, exactly the 14 design tables exist (Req 1.1)."""
    run_migrations(db_connection)

    with db_connection.cursor() as cur:
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = current_schema()
              AND table_type = 'BASE TABLE'
            """
        )
        present = {row[0] for row in cur.fetchall()}

    assert len(EXPECTED_TABLES) == 14
    for table in EXPECTED_TABLES:
        assert table in present, f"table {table!r} was not created by deploy (Req 1.1)"


# --------------------------------------------------------------------------- #
# Region + encryption (Req 1.5, 12.2, 14.2, 14.3, 14.4)
# --------------------------------------------------------------------------- #


def test_region_defaults_to_af_south_1_everywhere():
    """DB + S3 default to af-south-1 (Req 1.5, 12.2, 14.4).

    We pass an explicit empty env so the *default* is asserted regardless of any
    ambient ``AWS_REGION`` in the shell running the suite.
    """
    config = load_config(env={})  # no file, no env overrides -> pure defaults
    assert config.region == "af-south-1"
    assert Config().region == "af-south-1"  # dataclass default
    assert DEFAULT_REGION == "af-south-1"
    # The Archiver's lazily-created S3 client is pinned to af-south-1 too.
    assert ARCHIVE_DEFAULT_REGION == "af-south-1"


def test_boto3_clients_are_created_for_af_south_1():
    """S3 (Archive) and Bedrock clients are created in af-south-1 (Req 12.2, 14.4)."""
    moto = pytest.importorskip("moto")
    from funhouse_pipeline.archive import Archiver

    with moto.mock_aws():
        # No injected client -> the Archiver builds a real boto3 S3 client,
        # pinned to the region it was constructed with.
        archiver = Archiver(bucket="smoke-bucket", region="af-south-1")
        assert archiver.s3.meta.region_name == "af-south-1"


def test_tls_in_transit_is_configured_for_the_database():
    """DB connections use TLS in transit (Req 14.3): sslmode is set in the DSN."""
    config = load_config(env={})
    # Local default is 'prefer'; RDS uses 'require'. Both request TLS.
    assert config.database.sslmode in ("prefer", "require", "verify-ca", "verify-full")
    assert "sslmode=" in config.database.dsn()


def test_encryption_at_rest_is_documented():
    """Encryption at rest (Req 14.2) is documented (a local PG cannot emulate RDS)."""
    text = _DOCS.read_text(encoding="utf-8").lower()
    assert "encryption at rest" in text
    assert "rds" in text and "encrypt" in text


# --------------------------------------------------------------------------- #
# No banned services; only a PostgreSQL driver (Req 6.3, 6.4)
# --------------------------------------------------------------------------- #


def _source_files() -> list[Path]:
    return [p for p in _PACKAGE_ROOT.rglob("*.py")]


def test_no_banned_aws_services_referenced_in_source():
    """Pinpoint / DynamoDB / Cognito / Lambda-as-architecture are never used (Req 6.3)."""
    # Precise boto3 client/resource construction patterns for the banned
    # services. (We do NOT grep for the bare word "lambda" -- Python lambda
    # expressions are legitimate and unrelated to AWS Lambda.)
    banned_client = re.compile(
        r"""(?:client|resource)\(\s*['"](?:pinpoint|dynamodb|cognito-idp|cognito-identity|cognito|lambda)['"]""",
        re.IGNORECASE,
    )

    # We check for actual *usage* (a boto3 client/resource for a banned service),
    # not mere mentions: the codebase legitimately names these services in
    # docstrings precisely to document that they are excluded.
    offenders: list[str] = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        if banned_client.search(text):
            offenders.append(f"{path.name}: constructs a boto3 client for a banned service")
    assert not offenders, f"banned AWS services referenced: {offenders}"


def test_only_postgresql_driver_is_declared():
    """PostgreSQL is the only database system (Req 6.4)."""
    pyproject = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()
    # The PostgreSQL driver is present.
    assert "psycopg" in pyproject
    # No other/relational database drivers are pulled in.
    for other_driver in ("asyncpg", "pymysql", "mysqlclient", "mysql-connector", "cx_oracle", "pyodbc"):
        assert other_driver not in pyproject, f"unexpected DB driver dependency: {other_driver}"
    # And no banned datastore SDKs are declared as dependencies.
    for banned in ("pinpoint", "dynamodb", "cognito"):
        assert banned not in pyproject


def test_extract_image_path_imports_no_provider_sdk():
    """The Extractor talks only to the LLM abstraction, never a provider SDK (Req 6.1)."""
    from funhouse_pipeline.extract import images as images_mod

    forbidden = {"boto3", "anthropic", "BedrockBatchProvider", "AnthropicProvider"}
    assert not forbidden.intersection(vars(images_mod))


# --------------------------------------------------------------------------- #
# Command documentation exists (Req 13.2)
# --------------------------------------------------------------------------- #


def test_command_documentation_exists_and_mentions_command_and_flags():
    """Written documentation of the Documented_Command exists (Req 13.2)."""
    assert _DOCS.is_file(), "docs/pipeline-command.md must exist (Req 13.2)"
    text = _DOCS.read_text(encoding="utf-8")
    assert "funhouse-pipeline run" in text
    for flag in ("--source-folder", "--stage", "--config"):
        assert flag in text, f"command docs should describe the {flag} flag"


# --------------------------------------------------------------------------- #
# CLI runs locally on the operator machine, offline (Req 15.1)
# --------------------------------------------------------------------------- #


def test_cli_runs_locally_offline_returns_zero(tmp_path):
    """The console entry point is invocable offline and returns 0 (Req 15.1).

    ``--stage collect`` needs no network and no database, so this exercises the
    "container/CLI runs locally on the operator machine" smoke check.
    """
    from funhouse_pipeline.orchestrator.cli import main

    src = tmp_path / "src"
    (src / "photos").mkdir(parents=True)
    (src / "photos" / "a.png").write_bytes(b"img-bytes")

    exit_code = main(
        ["run", "--source-folder", str(src), "--stage", "collect", "--state-dir", str(tmp_path / "state")]
    )
    assert exit_code == 0
