"""Verify the live FastAPI PostgreSQL role before a secret cutover.

Run this command with the prospective runtime ``DB_*`` credentials. It performs
catalog-only checks and never mutates data or prints connection secrets::

    python -m funhouse_pipeline.db.verify_runtime_role
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import Any

from funhouse_pipeline.config import load_config
from funhouse_pipeline.db import connect

RUNTIME_ROLE = "funhouse_runtime"
_SUPABASE_CREATOR_MEMBER = "postgres"
_SUPABASE_CREATOR_GRANTOR = "supabase_admin"

EXPECTED_PRIVILEGES: dict[str, frozenset[str]] = {
    "locations": frozenset(),
    "schools": frozenset(),
    "users": frozenset({"SELECT"}),
    "guardians": frozenset(),
    "players": frozenset({"SELECT", "INSERT", "UPDATE"}),
    "consents": frozenset({"SELECT", "INSERT"}),
    "products": frozenset({"SELECT"}),
    "entitlements": frozenset({"SELECT", "INSERT", "UPDATE"}),
    "sessions": frozenset({"SELECT", "INSERT", "UPDATE"}),
    "attendance": frozenset({"SELECT", "INSERT", "UPDATE"}),
    "payments": frozenset({"SELECT", "INSERT", "UPDATE"}),
    "lessons": frozenset(),
    "student_metrics": frozenset({"SELECT", "INSERT", "UPDATE"}),
    "sync_log": frozenset({"SELECT", "INSERT"}),
}

_TABLE_PRIVILEGES = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)

_POLICY_COMMANDS = {
    "SELECT": ("funhouse_runtime_select", "r", "true", None),
    "INSERT": ("funhouse_runtime_insert", "a", None, "true"),
    "UPDATE": ("funhouse_runtime_update", "w", "true", "true"),
}


class RuntimeRoleVerificationError(RuntimeError):
    """Raised when the prospective runtime identity violates its contract."""

    def __init__(self, failures: Sequence[str]) -> None:
        self.failures = tuple(failures)
        super().__init__("; ".join(self.failures))


def _verify(conn: Any) -> tuple[str, str]:
    failures: list[str] = []

    with conn.cursor() as cursor:
        cursor.execute("SELECT current_user, current_schema(), current_database()")
        current_user, target_schema, database_name = cursor.fetchone()
        if current_user != RUNTIME_ROLE:
            failures.append(
                f"connected as {current_user!r}, expected PostgreSQL role {RUNTIME_ROLE!r}"
            )
        if target_schema is None:
            failures.append("current_schema() is NULL")

        cursor.execute(
            """
            SELECT oid, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
                   rolreplication, rolbypassrls
            FROM pg_roles
            WHERE rolname = %s
            """,
            (RUNTIME_ROLE,),
        )
        role_row = cursor.fetchone()
        if role_row is None:
            failures.append(f"required role {RUNTIME_ROLE!r} does not exist")
            runtime_oid = None
        else:
            (
                runtime_oid,
                can_login,
                is_superuser,
                can_create_db,
                can_create_role,
                can_replicate,
                bypasses_rls,
            ) = role_row
            if not can_login:
                failures.append("runtime role is not LOGIN")
            if is_superuser:
                failures.append("runtime role is SUPERUSER")
            if can_create_db:
                failures.append("runtime role has CREATEDB")
            if can_create_role:
                failures.append("runtime role has CREATEROLE")
            if can_replicate:
                failures.append("runtime role has REPLICATION")
            if bypasses_rls:
                failures.append("runtime role has BYPASSRLS")

        if runtime_oid is not None:
            # PostgreSQL 16+ automatically grants roles created by a
            # non-superuser CREATEROLE principal back to their creator. Hosted
            # Supabase records that administrative edge as supabase_admin
            # granting funhouse_runtime to postgres with ADMIN true but
            # INHERIT/SET false. It cannot be removed by postgres. Those option
            # values prevent immediate inheritance or SET ROLE access, while
            # postgres remains the trusted offline administrator and could
            # deliberately regrant access. Allow only that exact relationship.
            # to_jsonb keeps the query portable to PostgreSQL 15, where the
            # option keys are absent and therefore cannot match the exception.
            cursor.execute(
                """
                SELECT granted_role.rolname,
                       member_role.rolname,
                       grantor_role.rolname,
                       membership.admin_option,
                       (to_jsonb(membership) ->> 'inherit_option')::boolean,
                       (to_jsonb(membership) ->> 'set_option')::boolean
                FROM pg_auth_members AS membership
                JOIN pg_roles AS granted_role
                  ON granted_role.oid = membership.roleid
                JOIN pg_roles AS member_role
                  ON member_role.oid = membership.member
                JOIN pg_roles AS grantor_role
                  ON grantor_role.oid = membership.grantor
                WHERE membership.member = %s
                   OR membership.roleid = %s
                ORDER BY 1, 2, 3
                """,
                (runtime_oid, runtime_oid),
            )
            forbidden_memberships = []
            for (
                granted_role,
                member_role,
                grantor_role,
                admin_option,
                inherit_option,
                set_option,
            ) in cursor.fetchall():
                allowed_creator_administration = (
                    granted_role == RUNTIME_ROLE
                    and member_role == _SUPABASE_CREATOR_MEMBER
                    and grantor_role == _SUPABASE_CREATOR_GRANTOR
                    and admin_option is True
                    and inherit_option is False
                    and set_option is False
                )
                if not allowed_creator_administration:
                    forbidden_memberships.append(
                        f"{granted_role}->{member_role} "
                        f"(grantor={grantor_role}, admin={admin_option}, "
                        f"inherit={inherit_option}, set={set_option})"
                    )
            if forbidden_memberships:
                failures.append(
                    "runtime role has forbidden role relationships: "
                    + ", ".join(forbidden_memberships)
                )

        cursor.execute(
            """
            SELECT c.relname, c.oid, c.relrowsecurity, c.relforcerowsecurity,
                   pg_get_userbyid(c.relowner)
            FROM pg_class AS c
            JOIN pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname = %s
              AND c.relname = ANY(%s)
              AND c.relkind IN ('r', 'p')
            ORDER BY c.relname
            """,
            (target_schema, list(EXPECTED_PRIVILEGES)),
        )
        table_rows = {row[0]: row[1:] for row in cursor.fetchall()}

        missing_tables = sorted(set(EXPECTED_PRIVILEGES) - set(table_rows))
        if missing_tables:
            failures.append("missing FunHouse tables: " + ", ".join(missing_tables))

        for table_name, expected_privileges in EXPECTED_PRIVILEGES.items():
            table_row = table_rows.get(table_name)
            if table_row is None or runtime_oid is None:
                continue
            table_oid, rls_enabled, rls_forced, owner_name = table_row
            if owner_name == RUNTIME_ROLE:
                failures.append(f"{table_name}: runtime role owns the table")
            if not rls_enabled:
                failures.append(f"{table_name}: RLS is disabled")
            if rls_forced:
                failures.append(f"{table_name}: RLS is unexpectedly forced in this rollout")

            for privilege in _TABLE_PRIVILEGES:
                cursor.execute(
                    "SELECT has_table_privilege(%s, %s, %s)",
                    (runtime_oid, table_oid, privilege),
                )
                actual = cursor.fetchone()[0]
                expected = privilege in expected_privileges
                if actual is not expected:
                    failures.append(f"{table_name}: {privilege} is {actual}, expected {expected}")

            cursor.execute(
                """
                SELECT polname, polcmd, polroles,
                       pg_get_expr(polqual, polrelid),
                       pg_get_expr(polwithcheck, polrelid)
                FROM pg_policy
                WHERE polrelid = %s
                ORDER BY polname
                """,
                (table_oid,),
            )
            actual_policies = {
                (name, command, tuple(roles), using_expression, check_expression)
                for name, command, roles, using_expression, check_expression in cursor.fetchall()
            }
            expected_policies = {
                (
                    _POLICY_COMMANDS[privilege][0],
                    _POLICY_COMMANDS[privilege][1],
                    (runtime_oid,),
                    _POLICY_COMMANDS[privilege][2],
                    _POLICY_COMMANDS[privilege][3],
                )
                for privilege in expected_privileges
                if privilege in _POLICY_COMMANDS
            }
            if actual_policies != expected_policies:
                failures.append(f"{table_name}: runtime RLS policy set is not exact")

        if runtime_oid is not None and target_schema is not None:
            cursor.execute(
                """
                SELECT sequence_class.oid, sequence_class.relname, table_class.relname
                FROM pg_class AS sequence_class
                JOIN pg_namespace AS sequence_namespace
                  ON sequence_namespace.oid = sequence_class.relnamespace
                JOIN pg_depend AS dependency
                  ON dependency.classid = 'pg_class'::regclass
                 AND dependency.objid = sequence_class.oid
                 AND dependency.deptype IN ('a', 'i')
                JOIN pg_class AS table_class
                  ON dependency.refclassid = 'pg_class'::regclass
                 AND dependency.refobjid = table_class.oid
                JOIN pg_namespace AS table_namespace
                  ON table_namespace.oid = table_class.relnamespace
                WHERE sequence_class.relkind = 'S'
                  AND sequence_namespace.nspname = %s
                  AND table_namespace.nspname = %s
                  AND table_class.relname = ANY(%s)
                ORDER BY sequence_class.relname
                """,
                (target_schema, target_schema, list(EXPECTED_PRIVILEGES)),
            )
            for sequence_oid, sequence_name, table_name in cursor.fetchall():
                expected_sequence_privileges = (
                    frozenset({"USAGE", "SELECT"})
                    if "INSERT" in EXPECTED_PRIVILEGES[table_name]
                    else frozenset()
                )
                for privilege in ("USAGE", "SELECT", "UPDATE"):
                    cursor.execute(
                        "SELECT has_sequence_privilege(%s, %s, %s)",
                        (runtime_oid, sequence_oid, privilege),
                    )
                    actual = cursor.fetchone()[0]
                    expected = privilege in expected_sequence_privileges
                    if actual is not expected:
                        failures.append(
                            f"{sequence_name}: sequence {privilege} is {actual}, "
                            f"expected {expected} for {table_name}"
                        )

            cursor.execute(
                """
                SELECT has_database_privilege(%s, current_database(), 'CONNECT'),
                       has_database_privilege(%s, current_database(), 'CREATE'),
                       has_schema_privilege(%s, %s, 'USAGE'),
                       has_schema_privilege(%s, %s, 'CREATE')
                """,
                (
                    runtime_oid,
                    runtime_oid,
                    runtime_oid,
                    target_schema,
                    runtime_oid,
                    target_schema,
                ),
            )
            db_connect, db_create, schema_usage, schema_create = cursor.fetchone()
            if not db_connect:
                failures.append("runtime role lacks database CONNECT")
            if db_create:
                failures.append("runtime role has forbidden database CREATE")
            if not schema_usage:
                failures.append(f"runtime role lacks USAGE on schema {target_schema!r}")
            if schema_create:
                failures.append(f"runtime role has forbidden CREATE on schema {target_schema!r}")

            cursor.execute(
                """
                SELECT p.oid
                FROM pg_proc AS p
                JOIN pg_namespace AS n ON n.oid = p.pronamespace
                WHERE n.nspname = %s
                  AND p.proname = 'funhouse_reject_consents_mutation'
                  AND pg_get_function_identity_arguments(p.oid) = ''
                """,
                (target_schema,),
            )
            function_row = cursor.fetchone()
            if function_row is None:
                failures.append("consent mutation trigger function is missing")
            else:
                cursor.execute(
                    "SELECT has_function_privilege(%s, %s, 'EXECUTE')",
                    (runtime_oid, function_row[0]),
                )
                if cursor.fetchone()[0]:
                    failures.append(
                        "runtime role can directly execute the consent trigger function"
                    )

    if failures:
        raise RuntimeRoleVerificationError(failures)
    return target_schema, database_name


def verify(config_path: str | None = None) -> int:
    """Connect with ``DB_*`` and verify the runtime role contract."""
    config = load_config(config_path)
    print(
        f"Verifying runtime role at {config.database.host}:{config.database.port}"
        f"/{config.database.dbname} (sslmode={config.database.sslmode})"
    )

    conn = connect(config)
    try:
        schema_name, database_name = _verify(conn)
    finally:
        conn.close()

    print(
        f"Runtime role {RUNTIME_ROLE!r} passed least-privilege checks on "
        f"{database_name!r}.{schema_name!r}."
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI wrapper: optional first argument is a config-file path."""
    args = list(sys.argv[1:] if argv is None else argv)
    config_path = args[0] if args else None
    try:
        return verify(config_path)
    except RuntimeRoleVerificationError as exc:
        print("Runtime role verification failed:", file=sys.stderr)
        for failure in exc.failures:
            print(f"- {failure}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover - exercised through module CLI
    raise SystemExit(main())
