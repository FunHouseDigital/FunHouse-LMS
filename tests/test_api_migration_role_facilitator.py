"""DB-backed tests for migration 003_role_facilitator (Task 2.2).

Verify that:
  * Running ``run_migrations`` twice is safe (idempotent) -- the additive role
    widening uses DROP CONSTRAINT IF EXISTS + ADD CONSTRAINT (Req 13.2).
  * After the migration, a user with role ``facilitator`` inserts successfully,
    proving the widened CHECK admits the new role (Req 3.3).

These reuse the Phase 0 ``db_connection`` fixture, which skips gracefully when
no PostgreSQL server is reachable.
"""

from __future__ import annotations

import pytest

from funhouse_pipeline.db.migrations import run_migrations


def _insert_location(conn) -> str:
    """Insert a location row (users.location_id is NOT NULL) and return its id."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO locations (name) VALUES (%s) RETURNING id",
            ("Test Lounge",),
        )
        return cur.fetchone()[0]


@pytest.mark.db
def test_migration_003_is_idempotent_when_run_twice(db_connection):
    """Applying all migrations twice is safe and re-reports users as present."""
    first = run_migrations(db_connection)
    assert "003_role_facilitator.sql" in first.applied_files
    assert "users" in first.created()

    # Re-running must not raise (DROP CONSTRAINT IF EXISTS + ADD CONSTRAINT) and
    # must leave the existing users table intact.
    second = run_migrations(db_connection)
    assert "003_role_facilitator.sql" in second.applied_files
    assert "users" in second.already_present()


@pytest.mark.db
def test_facilitator_user_inserts_after_migration(db_connection):
    """A 'facilitator' user inserts successfully once the role CHECK is widened (Req 3.3)."""
    run_migrations(db_connection)
    location_id = _insert_location(db_connection)

    with db_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (name, role, email, location_id)
            VALUES (%s, %s, %s, %s)
            RETURNING role
            """,
            ("Facilitator Fran", "facilitator", "fran@example.test", location_id),
        )
        assert cur.fetchone()[0] == "facilitator"


@pytest.mark.db
def test_unknown_role_still_rejected_after_migration(db_connection):
    """The widened CHECK still rejects roles outside the permitted set."""
    import psycopg

    run_migrations(db_connection)
    location_id = _insert_location(db_connection)

    with db_connection.cursor() as cur:
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                """
                INSERT INTO users (name, role, email, location_id)
                VALUES (%s, %s, %s, %s)
                """,
                ("Bad Role", "wizard", "wiz@example.test", location_id),
            )
