"""Unit tests for configuration loading (no database required)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from funhouse_pipeline.config import ConfigError, load_config
from funhouse_pipeline.config.settings import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    DEFAULT_LLM_PROVIDER,
    DEFAULT_REGION,
)


def test_defaults_when_no_file_and_empty_env():
    cfg = load_config(config_path=None, env={})
    assert cfg.region == DEFAULT_REGION == "af-south-1"
    assert cfg.llm_provider == DEFAULT_LLM_PROVIDER == "bedrock"
    assert cfg.confidence_threshold == DEFAULT_CONFIDENCE_THRESHOLD
    assert cfg.s3_bucket is None
    assert cfg.database.host == "localhost"
    assert cfg.database.port == 5432


def _write_yaml(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def test_loads_values_from_yaml(tmp_path: Path):
    path = _write_yaml(
        tmp_path,
        """
        s3_bucket: funhouse-archive
        region: af-south-1
        llm_provider: bedrock
        confidence_threshold: 0.85
        database:
          host: db.internal
          port: 6543
          dbname: funhouse_prod
          user: pipeline
          password: secret
          sslmode: require
        """,
    )
    cfg = load_config(path, env={})
    assert cfg.s3_bucket == "funhouse-archive"
    assert cfg.confidence_threshold == 0.85
    assert cfg.database.host == "db.internal"
    assert cfg.database.port == 6543
    assert cfg.database.dbname == "funhouse_prod"
    assert cfg.database.user == "pipeline"
    assert cfg.database.password == "secret"
    assert cfg.database.sslmode == "require"


def test_env_overrides_yaml(tmp_path: Path):
    path = _write_yaml(
        tmp_path,
        """
        region: af-south-1
        llm_provider: bedrock
        confidence_threshold: 0.5
        database:
          host: yaml-host
          port: 5432
        """,
    )
    env = {
        "LLM_PROVIDER": "anthropic",
        "CONFIDENCE_THRESHOLD": "0.9",
        "DB_HOST": "env-host",
        "DB_PORT": "7000",
        "S3_BUCKET": "env-bucket",
    }
    cfg = load_config(path, env=env)
    assert cfg.llm_provider == "anthropic"
    assert cfg.confidence_threshold == 0.9
    assert cfg.database.host == "env-host"
    assert cfg.database.port == 7000
    assert cfg.s3_bucket == "env-bucket"


def test_missing_file_raises():
    with pytest.raises(ConfigError):
        load_config(config_path="/nonexistent/config.yaml", env={})


def test_invalid_confidence_threshold_rejected():
    with pytest.raises(ConfigError):
        load_config(config_path=None, env={"CONFIDENCE_THRESHOLD": "1.5"})


def test_invalid_llm_provider_rejected():
    with pytest.raises(ConfigError):
        load_config(config_path=None, env={"LLM_PROVIDER": "openai"})


def test_non_numeric_confidence_threshold_rejected():
    with pytest.raises(ConfigError):
        load_config(config_path=None, env={"CONFIDENCE_THRESHOLD": "high"})


def test_dsn_includes_password_only_when_set():
    with_pw = load_config(env={"DB_PASSWORD": "pw"})
    without_pw = load_config(env={})
    assert "password=pw" in with_pw.database.dsn()
    assert "password=" not in without_pw.database.dsn()
    # Core connection fields are always present.
    for token in ("host=", "port=", "dbname=", "user=", "sslmode="):
        assert token in without_pw.database.dsn()


def test_llm_provider_is_case_normalized():
    cfg = load_config(env={"LLM_PROVIDER": "Anthropic"})
    assert cfg.llm_provider == "anthropic"
