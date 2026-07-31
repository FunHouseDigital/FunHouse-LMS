"""API configuration (Spec 2).

Extends the reused Phase 0 :class:`funhouse_pipeline.config.Config` with the
settings the API layer needs (JWT secret/lifetime, alert horizon, TLS
enforcement, and the location timezone used by the deterministic recurring
reset). The database connection is *not* redefined here: the API reuses the
Phase 0 ``DatabaseConfig`` and its ``dsn()`` (a standard libpq connection
string) so the database host stays interchangeable (Req 13.4).

All settings have sensible defaults so the app can be imported and unit tested
without external configuration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping

from funhouse_pipeline.config import Config as PipelineConfig
from funhouse_pipeline.config import load_config

# --------------------------------------------------------------------------- #
# Defaults
# --------------------------------------------------------------------------- #

# 8 hours, matching the design's example token lifetime (Req 1.5, 14.5).
DEFAULT_JWT_TTL_SECONDS = 8 * 60 * 60
DEFAULT_ALERT_EXPIRY_HORIZON_DAYS = 7
DEFAULT_LOCATION_TIMEZONE = "Africa/Johannesburg"
# A development-only fallback secret; production MUST set JWT_SECRET.
_DEV_JWT_SECRET = "dev-insecure-secret-change-me"


def _coerce_int(value: object, *, field_name: str, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer, got {value!r}") from exc


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class ApiConfig:
    """Top-level API configuration.

    Wraps the reused pipeline ``Config`` (which carries the portable
    ``DatabaseConfig``) and adds API-specific settings.
    """

    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    jwt_secret: str = _DEV_JWT_SECRET
    jwt_ttl_seconds: int = DEFAULT_JWT_TTL_SECONDS
    alert_expiry_horizon_days: int = DEFAULT_ALERT_EXPIRY_HORIZON_DAYS
    tls_required: bool = False
    cors_origins: tuple[str, ...] = ()
    location_timezone: str = DEFAULT_LOCATION_TIMEZONE

    def dsn(self) -> str:
        """Return the portable libpq DSN from the reused DatabaseConfig (Req 13.4)."""
        return self.pipeline.database.dsn()


def load_api_config(
    config_path: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> ApiConfig:
    """Load API configuration from the reused pipeline loader plus API env vars.

    Args:
        config_path: Optional YAML path forwarded to the reused
            ``funhouse_pipeline.config.load_config`` for the DB settings.
        env: Environment mapping (defaults to ``os.environ``); injectable for
            tests.

    Returns:
        A validated :class:`ApiConfig`.
    """
    env = os.environ if env is None else env

    pipeline = load_config(config_path, env=env)

    jwt_secret = env.get("JWT_SECRET") or _DEV_JWT_SECRET
    jwt_ttl_seconds = _coerce_int(
        env.get("JWT_TTL_SECONDS"),
        field_name="JWT_TTL_SECONDS",
        default=DEFAULT_JWT_TTL_SECONDS,
    )
    alert_expiry_horizon_days = _coerce_int(
        env.get("ALERT_EXPIRY_HORIZON_DAYS"),
        field_name="ALERT_EXPIRY_HORIZON_DAYS",
        default=DEFAULT_ALERT_EXPIRY_HORIZON_DAYS,
    )
    tls_required = _coerce_bool(env.get("TLS_REQUIRED"))
    cors_origins = tuple(
        origin.strip()
        for origin in env.get("FUNHOUSE_CORS_ORIGINS", "").split(",")
        if origin.strip()
    )
    location_timezone = env.get("LOCATION_TIMEZONE") or DEFAULT_LOCATION_TIMEZONE

    return ApiConfig(
        pipeline=pipeline,
        jwt_secret=jwt_secret,
        jwt_ttl_seconds=jwt_ttl_seconds,
        alert_expiry_horizon_days=alert_expiry_horizon_days,
        tls_required=tls_required,
        cors_origins=cors_origins,
        location_timezone=location_timezone,
    )
