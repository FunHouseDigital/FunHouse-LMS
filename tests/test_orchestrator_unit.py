"""Unit tests for the orchestrator (Task 14): retries, manifest, CLI wiring.

These are concrete example tests complementing the property tests
(``test_orchestrator_e2e_properties.py`` for Property 23 idempotency and
``test_orchestrator_offline_properties.py`` for Property 30 offline stages).
None of these require a database or network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from funhouse_pipeline.orchestrator.manifest import (
    STAGE_COMPLETED,
    STATUS_DONE,
    RunManifest,
)
from funhouse_pipeline.orchestrator.pipeline import _stages_to_run
from funhouse_pipeline.orchestrator.retry import RetryPolicy, retry_call, with_retry


# --------------------------------------------------------------------------- #
# Retry helper
# --------------------------------------------------------------------------- #


def test_retry_call_succeeds_after_transient_failures():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("transient")
        return "ok"

    slept: list[float] = []
    result = retry_call(
        flaky,
        policy=RetryPolicy(max_attempts=5, base_delay=1, factor=2),
        sleep=slept.append,
    )
    assert result == "ok"
    assert calls["n"] == 3
    # Two backoff sleeps with exponential growth: 1, 2.
    assert slept == [1, 2]



def test_retry_call_raises_after_exhausting_attempts():
    def always_fails():
        raise TimeoutError("nope")

    with pytest.raises(TimeoutError):
        retry_call(
            always_fails,
            policy=RetryPolicy(max_attempts=3, base_delay=0),
            sleep=lambda _d: None,
        )


def test_retry_call_does_not_retry_non_retryable():
    calls = {"n": 0}

    def raises_value_error():
        calls["n"] += 1
        raise ValueError("fatal")

    with pytest.raises(ValueError):
        retry_call(
            raises_value_error,
            policy=RetryPolicy(max_attempts=5, base_delay=0, retryable=(KeyError,)),
            sleep=lambda _d: None,
        )
    assert calls["n"] == 1  # not retried


def test_retry_policy_delay_is_exponential_and_capped():
    policy = RetryPolicy(base_delay=1, factor=3, max_delay=10)
    assert policy.delay_for(1) == 1
    assert policy.delay_for(2) == 3
    assert policy.delay_for(3) == 9
    assert policy.delay_for(4) == 10  # capped


def test_with_retry_wraps_a_callable():
    attempts = {"n": 0}

    def fn(x):
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise OSError("blip")
        return x * 2

    wrapped = with_retry(fn, policy=RetryPolicy(max_attempts=3, base_delay=0), sleep=lambda _d: None)
    assert wrapped(21) == 42
    assert attempts["n"] == 2



# --------------------------------------------------------------------------- #
# Stage selection (--stage semantics)
# --------------------------------------------------------------------------- #


def test_stages_to_run_default_is_all_five():
    assert _stages_to_run(None) == ("collect", "extract", "validate", "load", "archive")


def test_stages_to_run_prefixes_for_linear_stages():
    assert _stages_to_run("collect") == ("collect",)
    assert _stages_to_run("extract") == ("collect", "extract")
    assert _stages_to_run("validate") == ("collect", "extract", "validate")
    assert _stages_to_run("load") == ("collect", "extract", "validate", "load")


def test_stages_to_run_archive_only_needs_collect():
    assert _stages_to_run("archive") == ("collect", "archive")


def test_stages_to_run_rejects_unknown():
    with pytest.raises(ValueError):
        _stages_to_run("bogus")


# --------------------------------------------------------------------------- #
# Run manifest
# --------------------------------------------------------------------------- #


def test_manifest_round_trip(tmp_path: Path):
    m = RunManifest.create("run123", source_folder="/src", base=tmp_path)
    m.register_file("/src/photos/a.jpg", handler="image_extract", subfolder="photos")
    m.set_file_status("/src/photos/a.jpg", "collect", STATUS_DONE)
    m.mark_record("rec1", table="players", status="loaded", source_file="/src/photos/a.jpg")
    m.record_skip("load", "rec2", "duplicate")
    m.record_failure("archive", "/src/photos/a.jpg", "s3 down")
    m.start_stage("collect")
    m.complete_stage("collect")
    path = m.save(tmp_path)
    assert path.exists()

    loaded = RunManifest.load("run123", tmp_path)
    assert loaded.source_folder == "/src"
    assert loaded.is_file_done("/src/photos/a.jpg", "collect")
    assert loaded.records["rec1"]["status"] == "loaded"
    assert loaded.skips == [{"stage": "load", "target": "rec2", "reason": "duplicate"}]
    assert loaded.failures[0]["reason"] == "s3 down"
    assert loaded.stage_status("collect") == STAGE_COMPLETED


def test_manifest_load_missing_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        RunManifest.load("does-not-exist", tmp_path)


def test_manifest_is_valid_json(tmp_path: Path):
    m = RunManifest.create("r", base=tmp_path)
    path = m.save(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["run_id"] == "r"
    assert set(data["stages"]) == {"collect", "extract", "validate", "load", "archive"}



# --------------------------------------------------------------------------- #
# CLI + run_pipeline offline wiring (no DB, no network)
# --------------------------------------------------------------------------- #


def _make_source_folder(root: Path) -> Path:
    (root / "photos").mkdir(parents=True)
    (root / "photos" / "a.jpg").write_bytes(b"img")
    (root / "lessons").mkdir(parents=True)
    (root / "notes.txt").write_bytes(b"loose")  # not in a subfolder -> ignored
    (root / "sheets").mkdir(parents=True)
    (root / "sheets" / "bad.exe").write_bytes(b"x")  # unsupported -> skipped
    return root


def test_cli_collect_only_offline(tmp_path: Path, capsys):
    from funhouse_pipeline.orchestrator.cli import main

    src = _make_source_folder(tmp_path / "src")
    state = tmp_path / "state"
    code = main(
        ["run", "--source-folder", str(src), "--stage", "collect", "--state-dir", str(state)]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "collected: 1" in out  # the one photos/a.jpg
    assert "Run manifest:" in out
    # A manifest file was persisted.
    assert list(state.glob("*.json"))


def test_run_pipeline_collect_records_skips_and_absent(tmp_path: Path):
    from funhouse_pipeline.config import load_config
    from funhouse_pipeline.orchestrator.pipeline import run_pipeline

    src = _make_source_folder(tmp_path / "src")
    result = run_pipeline(
        load_config(),
        src,
        stage="collect",
        state_dir=str(tmp_path / "state"),
    )
    assert result.summary["collected"] == 1
    # Unsupported file + absent subfolders are recorded as skips.
    reasons = {s["reason"] for s in result.manifest.skips}
    assert any("unsupported" in r for r in reasons)
    assert "subfolder absent" in reasons



def test_run_pipeline_extract_only_with_fake_llm(tmp_path: Path):
    """Extract stage runs offline with a fake LLM and no database."""
    import json as _json

    from funhouse_pipeline.config import Config, DatabaseConfig
    from funhouse_pipeline.llm.base import LLMResult, LLMResultItem
    from funhouse_pipeline.orchestrator.pipeline import run_pipeline

    src = _make_source_folder(tmp_path / "src")

    def fake_llm(task, context, *, provider=None, options=None):
        payload = {"records": [
            {"target_table": "players", "confidence": 0.9,
             "payload": {"first_name": "Zola", "last_name": "M"}}
        ]}
        items = [LLMResultItem(custom_id=r["custom_id"], content=_json.dumps(payload))
                 for r in context["records"]]
        return LLMResult(task=task, provider=provider or "bedrock", items=tuple(items))

    def no_db(_config):
        raise ConnectionError("no db in this test")

    config = Config(s3_bucket="b", region="af-south-1", llm_provider="bedrock")
    result = run_pipeline(
        config,
        src,
        stage="extract",
        state_dir=str(tmp_path / "state"),
        llm_generate_fn=fake_llm,
        connection_factory=no_db,
    )
    assert result.summary["extracted"] == 1
    assert result.records[0].target_table == "players"


def test_run_pipeline_archive_only_with_moto(tmp_path: Path):
    """Archive stage uploads originals to a moto-backed S3 (idempotent re-run)."""
    moto = pytest.importorskip("moto")
    from funhouse_pipeline.config import Config
    from funhouse_pipeline.orchestrator.pipeline import run_pipeline

    src = _make_source_folder(tmp_path / "src")
    config = Config(s3_bucket="funhouse-archive-unit", region="af-south-1")

    with moto.mock_aws():
        import boto3

        s3 = boto3.client("s3", region_name="af-south-1")
        s3.create_bucket(
            Bucket=config.s3_bucket,
            CreateBucketConfiguration={"LocationConstraint": "af-south-1"},
        )
        first = run_pipeline(
            config, src, stage="archive", state_dir=str(tmp_path / "state"),
            s3_client=s3, sleep=lambda _d: None,
        )
        assert first.summary["archived"] == 1
        assert len(first.archive_result.uploaded) == 1

        second = run_pipeline(
            config, src, stage="archive", state_dir=str(tmp_path / "state2"),
            s3_client=s3, sleep=lambda _d: None,
        )
        # Same key + hash -> idempotent no-op skip on the second archival.
        assert len(second.archive_result.skipped) == 1



def test_run_pipeline_archive_requires_bucket(tmp_path: Path):
    from funhouse_pipeline.config import Config
    from funhouse_pipeline.orchestrator.pipeline import (
        UnrecoverablePipelineError,
        run_pipeline,
    )

    src = _make_source_folder(tmp_path / "src")
    config = Config(s3_bucket=None, region="af-south-1")
    with pytest.raises(UnrecoverablePipelineError):
        run_pipeline(config, src, stage="archive", state_dir=str(tmp_path / "state"))


def test_run_pipeline_load_db_failure_is_unrecoverable(tmp_path: Path):
    from funhouse_pipeline.config import Config
    from funhouse_pipeline.orchestrator.pipeline import (
        UnrecoverablePipelineError,
        run_pipeline,
    )

    src = _make_source_folder(tmp_path / "src")

    def no_db(_config):
        raise ConnectionError("db unreachable")

    config = Config(s3_bucket="b", region="af-south-1")
    with pytest.raises(UnrecoverablePipelineError):
        run_pipeline(
            config,
            src,
            stage="load",
            state_dir=str(tmp_path / "state"),
            connection_factory=no_db,
        )
