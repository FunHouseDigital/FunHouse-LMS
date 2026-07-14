"""Token_Verifier: the ``require_auth`` FastAPI dependency (Req 2).

``require_auth`` guards every protected endpoint. It extracts the bearer token
from the ``Authorization`` header, verifies it with the Auth_Service, and yields
a :class:`Principal` carrying the caller's id, role, and location scope for the
downstream RBAC_Enforcer. A missing, malformed, invalid-signature, or expired
token is rejected with ``401 Unauthorized`` (Req 2.2–2.4, 2.6, 2.7).

Public endpoints (``/auth/login``, ``/health``) simply omit this dependency, so
they are reachable without a token (Req 2.5).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, Request
from fastapi.exceptions import HTTPException

from funhouse_api.auth.service import AuthError, Claims, decode_token
from funhouse_api.config import ApiConfig
from funhouse_api.dependencies import get_api_config

_UNAUTHORIZED_HEADERS = {"WWW-Authenticate": "Bearer"}


@dataclass(frozen=True)
class Principal:
    """The authenticated caller derived from a verified JWT (Req 2.1).

    Made available to authorization: the RBAC_Enforcer builds a ``Scope`` from
    this. ``location_id``/``school_id`` may be ``None`` (a founder is
    unrestricted).
    """

    user_id: str
    role: str
    location_id: str | None
    school_id: str | None

    @classmethod
    def from_claims(cls, claims: Claims) -> "Principal":
        return cls(
            user_id=claims.sub,
            role=claims.role,
            location_id=claims.location_id,
            school_id=claims.school_id,
        )


def _extract_bearer_token(request: Request) -> str | None:
    """Return the raw token from an ``Authorization: Bearer <token>`` header."""
    header = request.headers.get("Authorization")
    if not header:
        return None
    parts = header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip()


def require_auth(
    request: Request,
    config: ApiConfig = Depends(get_api_config),
) -> Principal:
    """Authenticate a protected request and return its :class:`Principal`.

    Raises:
        HTTPException: ``401`` for a missing, malformed, invalid, or expired
            token (Req 2.2–2.4, 2.6, 2.7).
    """
    token = _extract_bearer_token(request)
    try:
        claims = decode_token(
            token,
            now=datetime.now(timezone.utc),
            secret=config.jwt_secret,
        )
    except AuthError as exc:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
            headers=_UNAUTHORIZED_HEADERS,
        ) from exc
    return Principal.from_claims(claims)
