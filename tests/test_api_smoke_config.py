"""Smoke / configuration checks for the FunHouse API (Task 1.5).

Single-execution portability checks (not property tests), per the design's
"Not property-tested" testing strategy:

* The API's DSN is a standard libpq connection string produced by the reused
  ``DatabaseConfig.dsn()`` (Req 13.4).
* The API package contains no Cognito / DynamoDB / Pinpoint imports or boto3
  client construction (Req 13.2, 13.3) -- PostgreSQL is the only persistence
  dependency and auth is self-managed.
"""

from __future__ import annotations

import re
from pathlib import Path

from funhouse_api.config import load_api_config

_REPO_ROOT = Path(__file__).resolve().parent.parent
_API_ROOT = _REPO_ROOT / "funhouse_api"


def test_api_dsn_is_a_standard_libpq_string():
    """The API DSN is the reused libpq string (Req 13.4): host= ... dbname= ..."""
    config = load_api_config(env={})
    dsn = config.dsn()

    # A standard libpq keyword/value connection string with interchangeable host.
    for token in ("host=", "port=", "dbname=", "user=", "sslmode="):
        assert token in dsn, f"DSN missing libpq token {token!r}: {dsn!r}"
    # It is exactly the reused pipeline DatabaseConfig.dsn(), not a bespoke URL.
    assert dsn == config.pipeline.database.dsn()


def test_api_declares_expected_config_defaults():
    """API config exposes the documented settings with sensible defaults."""
    config = load_api_config(env={})
    assert config.jwt_ttl_seconds == 8 * 60 * 60  # 8h default (Req 1.5)
    assert config.alert_expiry_horizon_days >= 1
    assert config.tls_required is False  # opt-in via TLS_REQUIRED
    assert config.location_timezone  # non-empty


def test_no_banned_aws_services_referenced_in_api_source():
    """No Cognito / DynamoDB / Pinpoint / Lambda-as-architecture in the API (Req 13.2, 13.3)."""
    banned_client = re.compile(
        r"""(?:client|resource)\(\s*['"]"""
        r"""(?:pinpoint|dynamodb|cognito-idp|cognito-identity|cognito|lambda)['"]""",
        re.IGNORECASE,
    )
    banned_import = re.compile(
        r"^\s*(?:import|from)\s+(?:boto3|botocore)\b", re.MULTILINE
    )

    offenders: list[str] = []
    for path in _API_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if banned_client.search(text):
            offenders.append(f"{path.name}: constructs a boto3 client for a banned service")
        if banned_import.search(text):
            offenders.append(f"{path.name}: imports an AWS SDK (auth must be self-managed)")
    assert not offenders, f"banned AWS usage in funhouse_api: {offenders}"
