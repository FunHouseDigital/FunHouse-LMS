"""Safely initialize seeded passwords and explicitly rotate seeded logins.

This command is intended for controlled deployment automation after the schema
and reference seed have been applied. It reads plaintext passwords only from the
environment and never prints or stores them; only bcrypt hashes are persisted.
Initialization fails closed when a different hash already exists. Rotation is a
separate, explicit CLI mode: ``--rotate-loyiso-password`` for the seeded Loyiso
manager and ``--rotate-founder-password`` for the seeded Aya founder. Each mode
is bound to its own account, so neither can change the other's credential.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from typing import Any

from funhouse_api.auth.service import hash_password, verify_password
from funhouse_pipeline.config import load_config
from funhouse_pipeline.db import SEED_USERS, SMITHFIELD_LOCATION, connect
from funhouse_pipeline.db.maintenance import assume_maintenance_role

_MIN_PASSWORD_CHARS = 12
_MAX_BCRYPT_BYTES = 72
_EXPECTED_USER_ROLES = {user.name: user.role for user in SEED_USERS}
_EXPECTED_USER_SCHOOLS = {user.name: user.school_name for user in SEED_USERS}
_ALLOWED_USER_NAMES = frozenset(_EXPECTED_USER_ROLES)
_ROTATABLE_USER_NAMES = frozenset({"Loyiso", "Aya"})


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
    rotate_existing: bool = False,
) -> str:
    """Initialize one seeded user's bcrypt hash and return the outcome.

    A same-password rerun is a no-op. An existing different password fails
    closed unless explicit rotation is enabled for an approved account. The
    rotation allowlist currently contains the seeded Loyiso manager and Aya
    founder accounts, each reachable only through its dedicated CLI mode.
    """
    if name not in _ALLOWED_USER_NAMES:
        allowed = ", ".join(sorted(_ALLOWED_USER_NAMES))
        raise BootstrapError(f"BOOTSTRAP_USER_NAME must be one of: {allowed}")
    if rotate_existing and name not in _ROTATABLE_USER_NAMES:
        rotatable = ", ".join(sorted(_ROTATABLE_USER_NAMES))
        raise BootstrapError(f"password rotation is restricted to seeded users: {rotatable}")
    expected_role = _EXPECTED_USER_ROLES[name]
    if "\r" in password or "\n" in password:
        raise BootstrapError(
            "BOOTSTRAP_USER_PASSWORD must be a single-line password without CR or LF characters"
        )
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
            # Block concurrent user inserts/updates while uniqueness, seeded
            # metadata, and the target UUID are checked and changed.
            cursor.execute("LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE")
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
            elif existing_hash and not rotate_existing:
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
                outcome = "rotated" if existing_hash else "initialized"
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
    rotate_loyiso_password: bool = False,
    rotate_founder_password: bool = False,
) -> int:
    """Load configuration and initialize or explicitly rotate a seeded login."""
    env = os.environ if env is None else env
    if rotate_loyiso_password and rotate_founder_password:
        raise BootstrapError(
            "choose only one rotation mode: --rotate-loyiso-password or "
            "--rotate-founder-password"
        )
    name = _required_env(env, "BOOTSTRAP_USER_NAME")
    password = _required_env(env, "BOOTSTRAP_USER_PASSWORD")
    if rotate_loyiso_password and name != "Loyiso":
        raise BootstrapError("--rotate-loyiso-password requires BOOTSTRAP_USER_NAME=Loyiso")
    if rotate_founder_password and name != "Aya":
        raise BootstrapError("--rotate-founder-password requires BOOTSTRAP_USER_NAME=Aya")
    rotate_existing = rotate_loyiso_password or rotate_founder_password
    config = load_config(config_path, env=env)

    action = "Rotating" if rotate_existing else "Bootstrapping"
    print(
        f"{action} {name!r} in {config.database.host}:{config.database.port}"
        f"/{config.database.dbname} (sslmode={config.database.sslmode})"
    )

    conn = connect(config)
    try:
        assume_maintenance_role(conn, env=env)
        bootstrap_user(
            conn,
            name=name,
            password=password,
            rotate_existing=rotate_existing,
        )
    finally:
        conn.close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI wrapper with explicit, account-bound password rotation modes."""
    parser = argparse.ArgumentParser(
        description="Initialize a seeded login or explicitly rotate the Loyiso or Aya password."
    )
    parser.add_argument("config_path", nargs="?", help="optional configuration file")
    parser.add_argument(
        "--rotate-loyiso-password",
        action="store_true",
        help="replace Loyiso's existing bcrypt hash after all account checks pass",
    )
    parser.add_argument(
        "--rotate-founder-password",
        action="store_true",
        help="replace Aya's existing bcrypt hash after all account checks pass",
    )
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        return apply(
            args.config_path,
            rotate_loyiso_password=args.rotate_loyiso_password,
            rotate_founder_password=args.rotate_founder_password,
        )
    except BootstrapError as exc:
        print(f"Bootstrap refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover - exercised through module CLI
    raise SystemExit(main())
