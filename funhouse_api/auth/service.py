"""Auth_Service: password hashing and JWT lifecycle (Req 1, 2, 14.5).

This is the self-managed authentication core of the FunHouse Container API. It
runs entirely in-process — no AWS Cognito or external identity provider is ever
contacted (Req 13.3). Two concerns live here:

* **Passwords** — hashed and verified with bcrypt via ``passlib``. Only the
  bcrypt hash is ever persisted to ``users.password_hash`` (Req 1.6); the
  plaintext is never stored, and verification is a constant-time bcrypt compare
  (Req 1.2, 1.4).
* **Tokens** — signed and verified with PyJWT using HS256 and the configured
  server secret. Issued tokens carry the user's identity, role, and location
  scope plus ``iat``/``exp`` claims (Req 1.1, 1.5). ``decode_token`` verifies
  the signature and rejects missing, malformed, or expired tokens (Req 2.1–2.4,
  2.7) by raising :class:`AuthError`.

The token ``exp`` is checked against a caller-supplied ``now`` so both issuance
and verification are deterministic and unit-testable without wall-clock
dependence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

import bcrypt
import jwt

# --------------------------------------------------------------------------- #
# Password hashing
# --------------------------------------------------------------------------- #

# bcrypt is used directly (the design permits "passlib[bcrypt] (or bcrypt)").
# bcrypt only considers the first 72 bytes of a password, so inputs are
# truncated to 72 bytes consistently on both hash and verify — the standard,
# long-established bcrypt behaviour.
_BCRYPT_MAX_BYTES = 72


def _to_bcrypt_bytes(plaintext: str) -> bytes:
    """Encode and truncate a password to bcrypt's 72-byte input limit."""
    return plaintext.encode("utf-8")[:_BCRYPT_MAX_BYTES]

#: JWT signing algorithm (symmetric HMAC-SHA256; self-managed secret).
JWT_ALGORITHM = "HS256"

#: Roles recognised by the API (Req 3, Glossary).
VALID_ROLES: frozenset[str] = frozenset({"founder", "manager", "facilitator"})


class AuthError(Exception):
    """Raised when authentication fails.

    Covers a missing, malformed, invalid-signature, or expired token. The
    router/dependency layer maps this to ``401 Unauthorized`` with a generic
    message so no user enumeration is possible (Req 1.3, 1.4, 2.2–2.4).
    """


@dataclass(frozen=True)
class Claims:
    """Decoded, verified JWT claims (Req 1.1, 2.1).

    ``sub`` is the ``users.id`` as a string; ``location_id``/``school_id`` are
    the caller's scope (either may be ``None`` — a founder has neither, a
    manager has a location, a facilitator has both).
    """

    sub: str
    role: str
    location_id: str | None
    school_id: str | None
    iat: int
    exp: int


@dataclass(frozen=True)
class AuthUser:
    """The minimal user identity ``issue_token`` needs.

    Built from a ``users`` row at login. ``school_id`` is carried through the
    token model for facilitator scope; it defaults to ``None`` because the
    Phase 0 ``users`` table does not carry a ``school_id`` column (see login).
    """

    id: str
    role: str
    location_id: str | None = None
    school_id: str | None = None


def hash_password(plaintext: str) -> str:
    """Return a bcrypt hash of ``plaintext`` (Req 1.6).

    Only the returned hash should ever be persisted to ``users.password_hash``;
    the plaintext must never be stored.
    """
    hashed = bcrypt.hashpw(_to_bcrypt_bytes(plaintext), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plaintext: str, password_hash: str | None) -> bool:
    """Return True iff ``plaintext`` matches ``password_hash`` (Req 1.2, 1.4).

    A constant-time bcrypt comparison. A missing/empty stored hash (e.g. a
    backfilled user without a password) always returns ``False`` rather than
    raising, so the login path can respond with a generic authentication
    failure.
    """
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(_to_bcrypt_bytes(plaintext), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        # Malformed stored hash — treat as a non-match rather than a crash.
        return False


def _epoch(moment: datetime) -> int:
    """Return the integer POSIX timestamp for ``moment`` (assume UTC if naive)."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp())


def issue_token(user: AuthUser, *, now: datetime, secret: str, ttl_seconds: int) -> str:
    """Issue a signed JWT for ``user`` (Req 1.1, 1.5, 14.5).

    The token carries ``sub`` (user id), ``role``, ``location_id``,
    ``school_id``, ``iat``, and ``exp = iat + ttl_seconds`` and is signed with
    HS256 using ``secret``.

    Args:
        user: The authenticated user's identity and scope.
        now: Issue time; ``iat`` is derived from it.
        secret: The HS256 signing secret.
        ttl_seconds: Token lifetime; ``exp = iat + ttl_seconds``.

    Returns:
        The encoded JWT string.
    """
    iat = _epoch(now)
    exp = iat + int(ttl_seconds)
    payload = {
        "sub": str(user.id),
        "role": user.role,
        "location_id": None if user.location_id is None else str(user.location_id),
        "school_id": None if user.school_id is None else str(user.school_id),
        "iat": iat,
        "exp": exp,
    }
    token = jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)
    # PyJWT >= 2 returns str already; guard for older behaviour.
    return token if isinstance(token, str) else token.decode("utf-8")


def decode_token(token: str | None, *, now: datetime, secret: str) -> Claims:
    """Verify ``token`` and return its :class:`Claims` (Req 2.1–2.4, 2.7).

    Verification steps, in order:

    1. A missing/empty token → :class:`AuthError` (Req 2.2).
    2. Signature and structural validity are checked by PyJWT; any failure →
       :class:`AuthError` (Req 2.3).
    3. The ``exp`` claim is compared against ``now`` explicitly (so verification
       is deterministic in tests); an ``exp`` at or before ``now`` → expired →
       :class:`AuthError` (Req 2.4).

    Args:
        token: The raw JWT (without the ``Bearer`` prefix), or ``None``.
        now: The current server time used for the expiry check.
        secret: The HS256 verification secret.

    Returns:
        The decoded :class:`Claims`.

    Raises:
        AuthError: On a missing, malformed, invalid-signature, or expired token.
    """
    if not token:
        raise AuthError("authentication required")

    try:
        # Verify the signature but check expiry ourselves against `now` for
        # deterministic, testable behaviour.
        payload: Mapping[str, Any] = jwt.decode(
            token,
            secret,
            algorithms=[JWT_ALGORITHM],
            options={"verify_exp": False},
        )
    except jwt.InvalidTokenError as exc:
        raise AuthError("invalid authentication token") from exc

    try:
        exp = int(payload["exp"])
        iat = int(payload["iat"])
        sub = str(payload["sub"])
        role = str(payload["role"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthError("invalid authentication token") from exc

    if exp <= _epoch(now):
        raise AuthError("authentication token expired")

    location_id = payload.get("location_id")
    school_id = payload.get("school_id")
    return Claims(
        sub=sub,
        role=role,
        location_id=None if location_id is None else str(location_id),
        school_id=None if school_id is None else str(school_id),
        iat=iat,
        exp=exp,
    )
