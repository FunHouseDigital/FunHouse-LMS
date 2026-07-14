"""Public login endpoint: ``POST /auth/login`` (Req 1, 2.5).

Authenticates a staff member against the ``users`` table and, on success,
returns a signed JWT. The endpoint is public (no token required, Req 2.5).

Validation and error mapping (design § Error Handling):

* A missing ``identifier`` or ``password`` is a Pydantic validation error →
  ``422`` (Req 1.7).
* An unknown identifier (Req 1.3) or a password that fails the bcrypt check
  (Req 1.4) both return the *same* generic ``401`` with no detail, so an
  attacker cannot tell whether an identifier exists (no user enumeration).
* Otherwise a ``200`` :class:`LoginResponse` carrying the token and its expiry.

Identifier lookup: the Phase 0 ``users`` table has no ``phone`` column, so the
``identifier`` is matched against the columns that do exist — ``name`` (the
natural identity used by the seed) and ``email``. Passwords are verified against
``users.password_hash`` via the Auth_Service.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.exceptions import HTTPException
from pydantic import BaseModel

from funhouse_api.auth.service import AuthUser, issue_token, verify_password
from funhouse_api.config import ApiConfig
from funhouse_api.db import get_connection
from funhouse_api.dependencies import get_api_config

router = APIRouter(tags=["auth"])


class LoginRequest(BaseModel):
    """Login payload. Both fields required → 422 if either is missing (Req 1.7)."""

    identifier: str
    password: str


class LoginResponse(BaseModel):
    """Successful login response (Req 1.1)."""

    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


def _lookup_user(conn: Any, identifier: str) -> tuple[AuthUser, str | None] | None:
    """Return ``(user, password_hash)`` for a matching row, or ``None``.

    Matches ``identifier`` against ``users.name`` or ``users.email``. The
    ``users`` table carries no ``school_id`` column, so ``school_id`` is left as
    ``None`` on the issued identity.
    """
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, role, location_id, password_hash
            FROM users
            WHERE name = %s OR email = %s
            LIMIT 1
            """,
            (identifier, identifier),
        )
        row = cursor.fetchone()

    if row is None:
        return None

    user_id, role, location_id, password_hash = row
    user = AuthUser(
        id=str(user_id),
        role=role,
        location_id=None if location_id is None else str(location_id),
        school_id=None,
    )
    return user, password_hash


@router.post("/auth/login", response_model=LoginResponse, responses={401: {"description": "Authentication failed"}})
def login(
    body: LoginRequest,
    config: ApiConfig = Depends(get_api_config),
    conn: Any = Depends(get_connection),
) -> LoginResponse:
    """Authenticate and issue a JWT (Req 1.1–1.5, 2.5)."""
    found = _lookup_user(conn, body.identifier)
    # Generic 401 for both unknown identifier and bad password (no enumeration).
    if found is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    user, password_hash = found
    if not verify_password(body.password, password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    now = datetime.now(timezone.utc)
    token = issue_token(
        user,
        now=now,
        secret=config.jwt_secret,
        ttl_seconds=config.jwt_ttl_seconds,
    )
    expires_at = datetime.fromtimestamp(
        int(now.timestamp()) + config.jwt_ttl_seconds, tz=timezone.utc
    )
    return LoginResponse(access_token=token, token_type="bearer", expires_at=expires_at)
