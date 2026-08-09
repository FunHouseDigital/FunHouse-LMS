"""Verify production API read/write access for every seeded staff role.

The probe talks only to the deployed HTTPS API. It keeps credentials and JWTs
in memory, refuses redirects, and prints no response bodies or identifiers.
Successful writes use stable canary identities so reruns do not duplicate
business rows. Replays still append audit entries. The retained canary is
intentionally synthetic and must never be associated with a real learner.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

_DEFAULT_API_URL = "https://fun-house-lms.vercel.app"
_CANARY_FIRST_NAME = "API Verification"
_CANARY_LAST_NAME = "Canary v1"
_CANARY_PLAYER_CLIENT_ID = "prod-api-rbac-canary-player-v1"
_CANARY_PLAYER_CREATED_AT = "2024-01-01T00:00:00Z"
_METRIC_TIMESTAMPS = {
    "founder": "2024-01-01T00:00:01Z",
    "manager": "2024-01-01T00:00:02Z",
    "facilitator": "2024-01-01T00:00:03Z",
}


class LiveApiProbeError(RuntimeError):
    """Raised when a production API acceptance assertion fails."""


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Prevent credentials or bearer tokens from crossing an HTTP redirect."""

    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


@dataclass(frozen=True)
class _RoleSession:
    expected_role: str
    token: str
    claims: Mapping[str, Any]


def _required_env(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "")
    if not value:
        raise LiveApiProbeError(f"required production setting {name} is missing")
    return value


def _validate_api_url(raw_url: str) -> str:
    url = raw_url.rstrip("/")
    parsed = urllib.parse.urlsplit(url)
    if (
        url != _DEFAULT_API_URL
        or parsed.scheme != "https"
        or parsed.hostname != "fun-house-lms.vercel.app"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise LiveApiProbeError("VERCEL_API_URL must be the approved production API origin")
    return url


def _request_json(
    opener: urllib.request.OpenerDirector,
    api_url: str,
    method: str,
    path: str,
    *,
    token: str | None = None,
    payload: Mapping[str, Any] | None = None,
) -> tuple[int, Any]:
    headers = {"Accept": "application/json"}
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(
        api_url + path,
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with opener.open(request, timeout=30) as response:
            status = response.status
            raw_body = response.read()
    except urllib.error.HTTPError as exc:
        # Never include a response body: it may contain sensitive roster data or tokens.
        raise LiveApiProbeError(f"{method} {path} returned HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError):
        raise LiveApiProbeError(f"{method} {path} could not reach the production API") from None

    try:
        decoded = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise LiveApiProbeError(f"{method} {path} returned a non-JSON response") from None
    return status, decoded


def _decode_claims(token: str) -> Mapping[str, Any]:
    """Decode claims for scope discovery; the API still verifies token authenticity."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError
        encoded = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(encoded.encode("ascii")))
    except (UnicodeError, ValueError, json.JSONDecodeError):
        raise LiveApiProbeError("login returned a malformed JWT") from None
    if not isinstance(claims, dict):
        raise LiveApiProbeError("login returned JWT claims in an invalid shape")
    return claims


def _login(
    opener: urllib.request.OpenerDirector,
    api_url: str,
    *,
    name: str,
    password: str,
    expected_role: str,
) -> _RoleSession:
    status, response = _request_json(
        opener,
        api_url,
        "POST",
        "/auth/login",
        payload={"identifier": name, "password": password},
    )
    if status != 200 or not isinstance(response, dict):
        raise LiveApiProbeError(f"{expected_role} login returned an invalid response")
    token = response.get("access_token")
    if (
        not isinstance(token, str)
        or not token
        or response.get("token_type") != "bearer"
        or not isinstance(response.get("expires_at"), str)
    ):
        raise LiveApiProbeError(f"{expected_role} login response is incomplete")

    claims = _decode_claims(token)
    if claims.get("role") != expected_role or not isinstance(claims.get("sub"), str):
        raise LiveApiProbeError(f"{expected_role} login returned unexpected identity claims")
    if expected_role in {"manager", "facilitator"} and not isinstance(
        claims.get("location_id"), str
    ):
        raise LiveApiProbeError(f"{expected_role} login is missing its location scope")
    if expected_role == "facilitator" and not isinstance(claims.get("school_id"), str):
        raise LiveApiProbeError("facilitator login is missing its school scope")

    print(f"{expected_role}: login PASS")
    return _RoleSession(expected_role=expected_role, token=token, claims=claims)


def _list_players(
    opener: urllib.request.OpenerDirector,
    api_url: str,
    session: _RoleSession,
) -> list[Mapping[str, Any]]:
    status, response = _request_json(
        opener,
        api_url,
        "GET",
        "/players",
        token=session.token,
    )
    if status != 200 or not isinstance(response, list):
        raise LiveApiProbeError(f"{session.expected_role} player read returned an invalid response")

    players: list[Mapping[str, Any]] = []
    for row in response:
        if not isinstance(row, dict):
            raise LiveApiProbeError(f"{session.expected_role} player read returned an invalid row")
        if session.expected_role in {"manager", "facilitator"} and row.get(
            "location_id"
        ) != session.claims.get("location_id"):
            raise LiveApiProbeError(f"{session.expected_role} player read leaked another location")
        if session.expected_role == "facilitator" and row.get("school_id") != session.claims.get(
            "school_id"
        ):
            raise LiveApiProbeError("facilitator player read leaked another school")
        players.append(row)
    return players


def _sync_action(
    opener: urllib.request.OpenerDirector,
    api_url: str,
    session: _RoleSession,
    action: Mapping[str, Any],
) -> tuple[str, str]:
    status, response = _request_json(
        opener,
        api_url,
        "POST",
        "/sync",
        token=session.token,
        payload={"actions": [action]},
    )
    results = response.get("results") if isinstance(response, dict) else None
    if status != 200 or not isinstance(results, list) or len(results) != 1:
        raise LiveApiProbeError(f"{session.expected_role} write returned an invalid response")
    result = results[0]
    if not isinstance(result, dict):
        raise LiveApiProbeError(f"{session.expected_role} write returned an invalid result")
    if result.get("client_id") != action["client_id"] or result.get("entity") != action["entity"]:
        raise LiveApiProbeError(f"{session.expected_role} write result did not match its action")
    result_status = result.get("status")
    record_id = result.get("record_id")
    if result_status not in {"applied", "skipped"} or not isinstance(record_id, str):
        raise LiveApiProbeError(
            f"{session.expected_role} write was not applied or idempotently reused"
        )
    try:
        uuid.UUID(record_id)
    except ValueError:
        raise LiveApiProbeError(
            f"{session.expected_role} write returned an invalid record ID"
        ) from None
    return result_status, record_id


def _find_or_create_canary(
    opener: urllib.request.OpenerDirector,
    api_url: str,
    founder: _RoleSession,
    facilitator: _RoleSession,
) -> str:
    existing = [
        row
        for row in _list_players(opener, api_url, founder)
        if row.get("first_name") == _CANARY_FIRST_NAME and row.get("last_name") == _CANARY_LAST_NAME
    ]
    if len(existing) > 1:
        raise LiveApiProbeError("multiple production API canary players exist")
    if existing:
        canary = existing[0]
        if (
            canary.get("grade") != "SYSTEM_TEST_DO_NOT_REPORT"
            or canary.get("location_id") != facilitator.claims.get("location_id")
            or canary.get("school_id") != facilitator.claims.get("school_id")
        ):
            raise LiveApiProbeError("the production API canary marker or scope is invalid")
        record_id = canary.get("id")
        if not isinstance(record_id, str):
            raise LiveApiProbeError("the production API canary has an invalid ID")
        return record_id

    _, record_id = _sync_action(
        opener,
        api_url,
        founder,
        {
            "client_id": _CANARY_PLAYER_CLIENT_ID,
            "entity": "player",
            "created_at": _CANARY_PLAYER_CREATED_AT,
            "payload": {
                "first_name": _CANARY_FIRST_NAME,
                "last_name": _CANARY_LAST_NAME,
                "grade": "SYSTEM_TEST_DO_NOT_REPORT",
                "location_id": facilitator.claims["location_id"],
                "school_id": facilitator.claims["school_id"],
            },
        },
    )
    return record_id


def _verify_role_read(
    opener: urllib.request.OpenerDirector,
    api_url: str,
    session: _RoleSession,
    canary_id: str,
) -> None:
    players = _list_players(opener, api_url, session)
    if not any(row.get("id") == canary_id for row in players):
        raise LiveApiProbeError(f"{session.expected_role} cannot read the in-scope API canary")
    print(f"{session.expected_role}: scoped read PASS")


def _verify_role_write(
    opener: urllib.request.OpenerDirector,
    api_url: str,
    session: _RoleSession,
    canary_id: str,
) -> None:
    timestamp = _METRIC_TIMESTAMPS[session.expected_role]
    action = {
        "client_id": f"prod-api-rbac-{session.expected_role}-metric-v1",
        "entity": "student_metrics",
        "created_at": timestamp,
        "payload": {
            "player_id": canary_id,
            "metric_type": "observation",
            "measured_at": timestamp,
            "value": f"production-api-rbac-canary-v1:{session.expected_role}",
        },
    }
    status, record_id = _sync_action(opener, api_url, session, action)
    if status == "applied":
        replay_status, replay_record_id = _sync_action(opener, api_url, session, action)
        if replay_status != "skipped" or replay_record_id != record_id:
            raise LiveApiProbeError(
                f"{session.expected_role} write could not be read back by idempotent replay"
            )
        outcome = "INSERT and read-back replay"
    else:
        outcome = "existing row read-back"
    print(f"{session.expected_role}: scoped write PASS ({outcome})")


def verify(env: Mapping[str, str] | None = None) -> int:
    """Run the production role acceptance probe using environment-only secrets."""
    env = os.environ if env is None else env
    api_url = _validate_api_url(env.get("VERCEL_API_URL", _DEFAULT_API_URL))
    founder_password = _required_env(env, "BOOTSTRAP_USER_PASSWORD")
    manager_password = _required_env(env, "LOYISO_BOOTSTRAP_PASSWORD")
    facilitator_password = _required_env(env, "FACILITATOR_BOOTSTRAP_PASSWORD")
    opener = urllib.request.build_opener(_NoRedirects)

    sessions = (
        _login(
            opener,
            api_url,
            name="Aya",
            password=founder_password,
            expected_role="founder",
        ),
        _login(
            opener,
            api_url,
            name="Loyiso",
            password=manager_password,
            expected_role="manager",
        ),
        _login(
            opener,
            api_url,
            name="Facilitator",
            password=facilitator_password,
            expected_role="facilitator",
        ),
    )
    founder, manager, facilitator = sessions
    if manager.claims.get("location_id") != facilitator.claims.get("location_id"):
        raise LiveApiProbeError("manager and facilitator are not scoped to the same seed location")

    canary_id = _find_or_create_canary(opener, api_url, founder, facilitator)
    for session in sessions:
        _verify_role_read(opener, api_url, session, canary_id)
        _verify_role_write(opener, api_url, session, canary_id)

    print("Production API RBAC verification PASS for founder, manager, and facilitator.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI wrapper; no arguments are accepted to avoid unsafe target overrides."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        print("Production API RBAC verification accepts no arguments.", file=sys.stderr)
        return 2
    try:
        return verify()
    except LiveApiProbeError as exc:
        print(f"Production API RBAC verification failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover - exercised as an operational command
    raise SystemExit(main())
