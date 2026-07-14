"""Property test: end-to-end run is idempotent (Task 14.4).

Feature: phase0-data-foundation, Property 23: For any Source_Folder, running the
Documented_Command twice yields the same database state as running it once
(duplicate records are skipped and the skip recorded), across all target tables.

Validates: Requirements 9.5, 13.3

The whole pipeline runs with a deterministic fake ``llm_generate`` (extraction is
derived from fixture files), a moto-backed S3, and an ephemeral PostgreSQL schema
per example -- so there are no live AWS calls. We run the command twice over the
same Source_Folder and assert row counts across all five target tables are
identical after 1 vs 2 runs, and that the second run records duplicate skips.
"""

from __future__ import annotations

import json
import uuid
from datetime import date

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from funhouse_pipeline.config import Config, DatabaseConfig
from funhouse_pipeline.llm.base import LLMResult, LLMResultItem
from funhouse_pipeline.orchestrator.pipeline import run_pipeline
from funhouse_pipeline.orchestrator.retry import RetryPolicy

moto = pytest.importorskip("moto")
from moto import mock_aws  # noqa: E402

_MIN_ITERATIONS = 100
_TARGET_TABLES = ("players", "sessions", "payments", "lessons", "student_metrics")
_REFERENCE_DATE = date(2025, 1, 1)
_NAME_POOL = ["Kabelo", "Naledi", "Thabo", "Lerato", "Sipho", "Ayanda", "Zola"]
_TIER_AMOUNTS = ["R10", "R30", "R50", "R350"]



# --------------------------------------------------------------------------- #
# Generated scenario
# --------------------------------------------------------------------------- #


@st.composite
def _scenario(draw) -> dict:
    """A small, deterministic set of extractable records for one image file."""
    names = draw(
        st.lists(st.sampled_from(_NAME_POOL), min_size=1, max_size=4, unique=True)
    )
    records: list[dict] = []
    for name in names:
        records.append(
            {
                "target_table": "players",
                "confidence": 0.95,
                "payload": {
                    "first_name": name,
                    "last_name": "Test",
                    "birth_date": "2015-06-01",
                },
            }
        )
    # At least one payment (an insertable, natural-keyed row) referencing the
    # first player with a valid tier amount, so it loads on run 1 and is a
    # recorded duplicate skip on run 2.
    records.append(
        {
            "target_table": "payments",
            "confidence": 0.95,
            "payload": {
                "player_name": f"{names[0]} Test",
                "amount": draw(st.sampled_from(_TIER_AMOUNTS)),
            },
        }
    )
    # A lesson (natural-keyed by title) to exercise a second target table.
    records.append(
        {
            "target_table": "lessons",
            "confidence": 0.95,
            "payload": {
                "title": "Lesson " + draw(st.sampled_from(["Alpha", "Beta", "Gamma"])),
                "topic": "typing",
                "phenomenon": "keyboard",
            },
        }
    )
    return {"records": records}



# --------------------------------------------------------------------------- #
# Fakes / helpers
# --------------------------------------------------------------------------- #


def _make_fake_llm(records_payload: dict):
    """Return a deterministic ``llm_generate`` stub for the image path.

    It maps every input record (one per image file) to the same generated
    ``{"records": [...]}`` content, so extraction is fully deterministic and
    issues no network call.
    """

    def fake_llm(task, context, *, provider=None, options=None):
        items = []
        for rec in context["records"]:
            items.append(
                LLMResultItem(
                    custom_id=rec["custom_id"],
                    content=json.dumps(records_payload),
                )
            )
        return LLMResult(task=task, provider=provider or "bedrock", items=tuple(items))

    return fake_llm


def _build_source_folder(tmp_path):
    """Create a Source_Folder with one image file under photos/."""
    photos = tmp_path / "photos"
    photos.mkdir(parents=True, exist_ok=True)
    (photos / "scan.png").write_bytes(b"\x89PNG\r\n\x1a\n fake image bytes")
    return tmp_path


def _table_counts(conn) -> dict[str, int]:
    counts: dict[str, int] = {}
    with conn.cursor() as cur:
        for table in _TARGET_TABLES:
            cur.execute(f"SELECT count(*) FROM {table}")
            counts[table] = cur.fetchone()[0]
    return counts



def _connection_factory(dsn: str, schema: str):
    import psycopg

    def factory(_config: Config):
        # Autocommit so each loader per-record transaction persists (matches the
        # orchestrator's default connection behavior).
        conn = psycopg.connect(dsn, autocommit=True)
        conn.execute(f'SET search_path TO "{schema}", public')
        return conn

    return factory


# --------------------------------------------------------------------------- #
# Property 23
# --------------------------------------------------------------------------- #


@settings(
    max_examples=_MIN_ITERATIONS,
    deadline=None,
    suppress_health_check=[
        HealthCheck.function_scoped_fixture,
        HealthCheck.too_slow,
    ],
)
@given(scenario=_scenario())
def test_end_to_end_run_is_idempotent(scenario, pg_dsn, tmp_path_factory):
    import psycopg

    schema = f"e2e_{uuid.uuid4().hex}"
    admin = psycopg.connect(pg_dsn, autocommit=True)
    admin.execute(f'CREATE SCHEMA "{schema}"')

    source_folder = _build_source_folder(tmp_path_factory.mktemp("src"))
    factory = _connection_factory(pg_dsn, schema)

    config = Config(
        database=DatabaseConfig(host="127.0.0.1", dbname="funhouse_test", user="funhouse"),
        s3_bucket="funhouse-archive-e2e",
        region="af-south-1",
        llm_provider="bedrock",
        confidence_threshold=0.7,
    )
    fake_llm = _make_fake_llm(scenario)
    no_sleep = lambda _delay: None  # noqa: E731 - keep retries instant
    policy = RetryPolicy(max_attempts=2, base_delay=0)


    try:
        with mock_aws():
            import boto3

            s3 = boto3.client("s3", region_name="af-south-1")
            s3.create_bucket(
                Bucket=config.s3_bucket,
                CreateBucketConfiguration={"LocationConstraint": "af-south-1"},
            )

            def _run():
                return run_pipeline(
                    config,
                    source_folder,
                    state_dir=str(tmp_path_factory.mktemp("state")),
                    llm_generate_fn=fake_llm,
                    s3_client=s3,
                    connection_factory=factory,
                    retry_policy=policy,
                    sleep=no_sleep,
                    reference_date=_REFERENCE_DATE,
                )

            first = _run()
            read_conn = factory(config)
            try:
                counts_after_first = _table_counts(read_conn)
            finally:
                read_conn.close()

            second = _run()
            read_conn = factory(config)
            try:
                counts_after_second = _table_counts(read_conn)
            finally:
                read_conn.close()
    finally:
        admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        admin.close()

    # Run 1 actually loaded something (players + payment + lesson).
    assert first.load_result is not None and len(first.load_result.loaded) >= 1
    # Idempotency: DB state after two runs equals state after one, per table.
    assert counts_after_second == counts_after_first
    # The second run recorded duplicate skips (Req 9.5 / 13.3).
    assert len(second.load_result.skipped) >= 1
