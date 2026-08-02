"""Configuration model and loader.

The pipeline is configured from two layers, applied in order of increasing
precedence:

1. A YAML configuration file (optional).
2. Environment variables (optional) - these OVERRIDE the YAML values.

Every setting has a sensible default so the pipeline can be imported and unit
tested without any external configuration. Validation runs after both layers
are merged so misconfiguration fails fast with an actionable message.

Design references:
- Region defaults to ``af-south-1`` (Req 1.5, 12.2, 14.4).
- ``LLM_PROVIDER`` selects the provider behind the LLM abstraction (Req 6.2).
- PostgreSQL is the only database system (Req 6.4).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # pyyaml is a runtime dependency; guard import for clearer errors.
    import yaml
except ModuleNotFoundError as exc:  # pragma: no cover - exercised only w/o pyyaml
    raise ModuleNotFoundError(
        "PyYAML is required for configuration loading. Install project dependencies."
    ) from exc


# --------------------------------------------------------------------------- #
# Defaults
# --------------------------------------------------------------------------- #

DEFAULT_REGION = "af-south-1"
DEFAULT_LLM_PROVIDER = "bedrock"
DEFAULT_CONFIDENCE_THRESHOLD = 0.7
DEFAULT_DB_HOST = "localhost"
DEFAULT_DB_PORT = 5432
DEFAULT_DB_NAME = "funhouse"
DEFAULT_DB_USER = "funhouse"
DEFAULT_DB_SSLMODE = "prefer"

VALID_LLM_PROVIDERS = ("bedrock", "anthropic")


class ConfigError(ValueError):
    """Raised when configuration is invalid."""


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DatabaseConfig:
    """PostgreSQL connection settings (Req 6.4: PostgreSQL is the only DB)."""

    host: str = DEFAULT_DB_HOST
    port: int = DEFAULT_DB_PORT
    dbname: str = DEFAULT_DB_NAME
    user: str = DEFAULT_DB_USER
    password: str | None = None
    # TLS in transit (Req 14.3). "prefer" locally; "require" against RDS.
    sslmode: str = DEFAULT_DB_SSLMODE

    def dsn(self) -> str:
        """Return a safely quoted libpq/psycopg connection string.

        ``make_conninfo`` handles spaces, quotes, and backslashes in Supabase
        credentials. The password is included only when configured.
        """
        from psycopg.conninfo import make_conninfo

        params: dict[str, str | int] = {
            "host": self.host,
            "port": self.port,
            "dbname": self.dbname,
            "user": self.user,
            "sslmode": self.sslmode,
        }
        if self.password is not None:
            params["password"] = self.password
        return make_conninfo(**params)


@dataclass(frozen=True)
class Config:
    """Top-level pipeline configuration."""

    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    s3_bucket: str | None = None
    region: str = DEFAULT_REGION
    llm_provider: str = DEFAULT_LLM_PROVIDER
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD

    def validate(self) -> Config:
        """Validate the merged configuration; raise ConfigError on problems."""
        errors: list[str] = []

        if not 0.0 <= self.confidence_threshold <= 1.0:
            errors.append(
                "confidence_threshold must be within [0, 1], got "
                f"{self.confidence_threshold!r}"
            )

        if self.llm_provider not in VALID_LLM_PROVIDERS:
            errors.append(
                f"llm_provider must be one of {VALID_LLM_PROVIDERS}, got "
                f"{self.llm_provider!r}"
            )

        if not self.region:
            errors.append("region must be a non-empty string")

        if not isinstance(self.database.port, int) or self.database.port <= 0:
            errors.append(f"database.port must be a positive integer, got {self.database.port!r}")

        if not self.database.host:
            errors.append("database.host must be a non-empty string")

        if not self.database.dbname:
            errors.append("database.dbname must be a non-empty string")

        if errors:
            raise ConfigError("Invalid configuration: " + "; ".join(errors))
        return self


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def _read_yaml(path: Path) -> Mapping[str, Any]:
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if data is None:
        return {}
    if not isinstance(data, Mapping):
        raise ConfigError(f"Config file {path} must contain a top-level mapping")
    return data


def _coerce_int(value: Any, *, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field_name} must be an integer, got {value!r}") from exc


def _coerce_float(value: Any, *, field_name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field_name} must be a number, got {value!r}") from exc


def _database_from_sources(
    yaml_db: Mapping[str, Any],
    env: Mapping[str, str],
) -> DatabaseConfig:
    """Build DatabaseConfig from YAML overlaid with environment variables."""

    def pick(env_key: str, yaml_key: str, default: Any) -> Any:
        if env_key in env and env[env_key] != "":
            return env[env_key]
        if yaml_key in yaml_db and yaml_db[yaml_key] is not None:
            return yaml_db[yaml_key]
        return default

    host = str(pick("DB_HOST", "host", DEFAULT_DB_HOST))
    port = _coerce_int(pick("DB_PORT", "port", DEFAULT_DB_PORT), field_name="database.port")
    dbname = str(pick("DB_NAME", "dbname", DEFAULT_DB_NAME))
    user = str(pick("DB_USER", "user", DEFAULT_DB_USER))
    sslmode = str(pick("DB_SSLMODE", "sslmode", DEFAULT_DB_SSLMODE))

    password_raw = pick("DB_PASSWORD", "password", None)
    password = None if password_raw in (None, "") else str(password_raw)

    return DatabaseConfig(
        host=host,
        port=port,
        dbname=dbname,
        user=user,
        password=password,
        sslmode=sslmode,
    )


def load_config(
    config_path: str | Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> Config:
    """Load configuration from YAML (optional) overlaid with environment vars.

    Args:
        config_path: Path to a YAML config file. If ``None``, only defaults and
            environment variables are used.
        env: Environment mapping to read overrides from. Defaults to
            ``os.environ``. Injectable for testing.

    Returns:
        A validated :class:`Config`.

    Raises:
        ConfigError: If the file is missing/malformed or values are invalid.
    """
    env = os.environ if env is None else env

    yaml_data: Mapping[str, Any] = {}
    if config_path is not None:
        yaml_data = _read_yaml(Path(config_path))

    yaml_db = yaml_data.get("database") or {}
    if not isinstance(yaml_db, Mapping):
        raise ConfigError("config 'database' section must be a mapping")

    database = _database_from_sources(yaml_db, env)

    def top(env_key: str, yaml_key: str, default: Any) -> Any:
        if env_key in env and env[env_key] != "":
            return env[env_key]
        if yaml_key in yaml_data and yaml_data[yaml_key] is not None:
            return yaml_data[yaml_key]
        return default

    s3_raw = top("S3_BUCKET", "s3_bucket", None)
    s3_bucket = None if s3_raw in (None, "") else str(s3_raw)

    region = str(top("AWS_REGION", "region", DEFAULT_REGION))
    llm_provider = str(top("LLM_PROVIDER", "llm_provider", DEFAULT_LLM_PROVIDER)).lower()
    confidence_threshold = _coerce_float(
        top("CONFIDENCE_THRESHOLD", "confidence_threshold", DEFAULT_CONFIDENCE_THRESHOLD),
        field_name="confidence_threshold",
    )

    config = Config(
        database=database,
        s3_bucket=s3_bucket,
        region=region,
        llm_provider=llm_provider,
        confidence_threshold=confidence_threshold,
    )
    return config.validate()
