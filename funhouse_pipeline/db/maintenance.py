"""Controlled PostgreSQL role assumption for maintenance commands.

FastAPI never imports or calls this module. Migration, seed, and credential
bootstrap commands may opt into ``DB_MAINTENANCE_ROLE`` so a login-only
migrator can assume the NOLOGIN object-owner role for one controlled session.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

MAINTENANCE_ROLE_ENV = "DB_MAINTENANCE_ROLE"


def configured_maintenance_role(env: Mapping[str, str] | None = None) -> str | None:
    """Return the configured maintenance role, or ``None`` when unset."""
    source = os.environ if env is None else env
    role = source.get(MAINTENANCE_ROLE_ENV, "").strip()
    return role or None


def assume_maintenance_role(
    conn: Any,
    *,
    env: Mapping[str, str] | None = None,
) -> str | None:
    """Safely ``SET ROLE`` to the optional maintenance owner.

    The role identifier is composed with psycopg's SQL identifier support, not
    string interpolation. PostgreSQL enforces membership/SET ROLE permission;
    the explicit ``current_user`` check prevents a silent partial transition.
    """
    role = configured_maintenance_role(env)
    if role is None:
        return None

    from psycopg import sql

    with conn.cursor() as cursor:
        cursor.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
        cursor.execute("SELECT current_user")
        active_role = cursor.fetchone()[0]

    if active_role != role:
        raise RuntimeError(
            f"Failed to assume DB_MAINTENANCE_ROLE {role!r}; active role is {active_role!r}"
        )

    print(f"Assumed maintenance database role {role!r}.")
    return role
