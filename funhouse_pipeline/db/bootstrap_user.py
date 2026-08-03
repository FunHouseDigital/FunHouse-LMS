"""Safely initialize a seeded user's password without implicit rotation.

This one-off command is intended for controlled deployment automation after the
schema and reference seed have been applied. It reads the plaintext password
only from the environment and never prints or stores it; only the bcrypt hash is
persisted.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence
from typing import Any

from funhouse_api.auth.service import hash_password, verify_password
from funhouse_pipeline.config import load_config
from funhouse_pipeline.db import SEED_USERS, SMITHFIELD_LOCATION, connect

_MIN_PASSWORD_CHARS = 12
_MAX_BCRYPT_BYTES = 72
_EXPECTED_USER_ROLES = {user.name: user.role for user in SEED_USERS}
_EXPECTED_USER_SCHOOLS = {user.name: user.school_name for user in SEED_USERS}
_ALLOWED_USER_NAMES = frozenset(_EXPECTED_USER_ROLES)


class BootstrapError(RuntimeError):
    """Raised when bootstrap cannot proceed safely."""


def _required_env(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "")
    if not value:
        raise BootstrapError(f"{name} must be set and non-empty")
    return value



def bootstrap_user(
    conn: Any,
    *,
    name: str,
    password: str,
) -> str:
    """Initialize one seeded user's bcrypt hash and return the outcome.

    A same-password rerun is a no-op. An existing different password always
    fails closed; credential rotation uses a separate recovery procedure.
    """
    if name not in _ALLOWED_USER_NAMES:
        allowed = ", ".join(sorted(_ALLOWED_USER_NAMES))
        raise BootstrapError(f"BOOTSTRAP_USER_NAME must be one of: {allowed}")
    expected_role = _EXPECTED_USER_ROLES[name]
    if len(password) < _MIN_PASSWORD_CHARS:
        raise BootstrapError(
            f"BOOTSTRAP_USER_PASSWORD must contain at least {_MIN_PASSWORD_CHARS} characters"
        )
    if len(password.encode("utf-8")) > _MAX_BCRYPT_BYTES:
        raise BootstrapError(
            f"BOOTSTRAP_USER_PASSWORD must be at most {_MAX_BCRYPT_BYTES} UTF-8 bytes"
        )

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT users.id, users.role, users.password_hash, locations.name,
                       schools.name
                FROM users
                JOIN locations ON locations.id = users.location_id
                LEFT JOIN schools ON schools.id = users.school_id
                WHERE users.name = %s
                FOR UPDATE OF users
                """,
                (name,),
            )
            rows = cursor.fetchall()
            if not rows:
                raise BootstrapError(
                    f"Seeded user {name!r} was not found; run migrations and seed first"
                )
            if len(rows) != 1:
                raise BootstrapError(
                    f"Seeded user {name!r} is ambiguous ({len(rows)} rows); repair the data first"
                )

            user_id, role, existing_hash, location_name, school_name = rows[0]
            expected_school = _EXPECTED_USER_SCHOOLS[name]
            if (
                role != expected_role
                or location_name != SMITHFIELD_LOCATION
                or school_name != expected_school
            ):
                raise BootstrapError(
                    f"{name!r} does not match the expected seeded role, location, and school"
                )
            if existing_hash and verify_password(password, existing_hash):
                outcome = "already initialized; unchanged"
            elif existing_hash:
                raise BootstrapError(
                    f"{name!r} already has a different password; bootstrap will not replace it"
                )
            else:
                cursor.execute(
                    """
                    UPDATE users
                    SET password_hash = %s, updated_at = now()
                    WHERE id = %s
                    """,
                    (hash_password(password), user_id),
                )
                outcome = "initialized"
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    print(f"Bootstrap user {name!r} ({role}): {outcome}.")
    return outcome


def apply(
    config_path: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> int:
    """Load configuration and bootstrap the requested seeded user."""
    env = os.environ if env is None else env
    name = _required_env(env, "BOOTSTRAP_USER_NAME")
    password = _required_env(env, "BOOTSTRAP_USER_PASSWORD")
    config = load_config(config_path, env=env)

    print(
        f"Bootstrapping {name!r} in {config.database.host}:{config.database.port}"
        f"/{config.database.dbname} (sslmode={config.database.sslmode})"
    )

    conn = connect(config)
    try:
        bootstrap_user(conn, name=name, password=password)
    finally:
        conn.close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI wrapper: optional first argument is a config-file path."""
    args = list(sys.argv[1:] if argv is None else argv)
    config_path = args[0] if args else None
    try:
        return apply(config_path)
    except BootstrapError as exc:
        print(f"Bootstrap refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover - exercised through module CLI
    raise SystemExit(main())
