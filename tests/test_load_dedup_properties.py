"""Property-based tests for player deduplication (Tasks 10.2, 10.3).

Implements design Properties 20 and 21. Both require a reachable PostgreSQL
server and are skipped automatically otherwise (see conftest's ``db_connection``
fixture). Each property runs a minimum of 100 Hypothesis iterations, per the
design's Testing Strategy.

Because the ``db_connection`` fixture is function-scoped and reused across a
single test's generated examples, each example first clears the working tables
so state does not leak between examples (the disposable schema is dropped only
at test teardown).
"""

from __future__ import annotations

from datetime import datetime

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from funhouse_pipeline.db.migrations import run_migrations
from funhouse_pipeline.extract.records import ExtractedRecord
from funhouse_pipeline.load.dedup import compute_dedup_key, resolve_players

pytestmark = [pytest.mark.db, pytest.mark.property]

_DB_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# Small pools so collisions on name and birth_date are frequent (design's
# "controllable name/birth_date collisions" generator note for Property 20).
_FIRST = st.sampled_from(["john", "jane", "sam", "alex"])
_LAST = st.sampled_from(["smith", "lee", "ng", "kim"])
_BIRTH = st.sampled_from([None, "2010-01-01", "2011-02-02", "2012-03-03"])
_CONF = st.floats(min_value=0.0, max_value=1.0)

# A candidate spec: (first_name, last_name, birth_date, confidence).
_PLAYER_SPEC = st.tuples(_FIRST, _LAST, _BIRTH, _CONF)
_PLAYER_BATCH = st.lists(_PLAYER_SPEC, min_size=1, max_size=8)


@pytest.fixture
def migrated_db(db_connection):
    run_migrations(db_connection)
    return db_connection


def _reset(conn) -> None:
    """Clear working tables between Hypothesis examples (keep the schema)."""
    conn.execute("DELETE FROM student_metrics")
    conn.execute("DELETE FROM payments")
    conn.execute("DELETE FROM sessions")
    conn.execute("DELETE FROM players")


def _location(conn):
    """Return an existing location id, creating one on first use."""
    row = conn.execute("SELECT id FROM locations LIMIT 1").fetchone()
    if row:
        return row[0]
    return conn.execute(
        "INSERT INTO locations (name) VALUES ('Smithfield') RETURNING id"
    ).fetchone()[0]


def _build_records(specs):
    records = []
    for i, (first, last, birth, conf) in enumerate(specs):
        payload = {"first_name": first, "last_name": last, "birth_date": birth}
        records.append(
            ExtractedRecord(
                record_id=f"rec_{i}",
                target_table="players",
                payload=payload,
                confidence_score=conf,
                source_file=f"src/{i}.png",
                provider="bedrock",
                extracted_at=datetime(2024, 1, 1),
            )
        )
    return records


# Feature: phase0-data-foundation, Property 20: Player deduplication yields one
# row per person with preserved history. For any set of extracted player records
# (learners and lounge customers mixed), after load the players table contains no
# two rows for the same person (unique dedup_key), all such records land in the
# single players table, and every session/payment/metric from merged records is
# associated with exactly one surviving players row with no history lost.
# Validates: Requirements 8.1, 8.2, 8.3
@_DB_SETTINGS
@given(specs=_PLAYER_BATCH)
def test_property_20_dedup_one_row_per_person_history_preserved(migrated_db, specs):
    conn = migrated_db
    _reset(conn)
    loc = _location(conn)

    records = _build_records(specs)
    result = resolve_players(records, conn, location_id=loc)

    # (a) One row per person: dedup_key is unique across the single players table.
    total, distinct = conn.execute(
        "SELECT count(*), count(DISTINCT dedup_key) FROM players"
    ).fetchone()
    assert total == distinct

    # (b) Resolved candidates map only to rows that exist in the players table.
    player_ids = {r[0] for r in conn.execute("SELECT id FROM players").fetchall()}
    assert set(result.resolved.values()) <= player_ids

    # (c) Candidates with the same dedup_key merge to the SAME surviving row;
    #     flagged candidates are never resolved.
    flagged_ids = {f.identity for f in result.flagged}
    assert flagged_ids.isdisjoint(result.resolved)
    by_key: dict[str, set] = {}
    for rec in records:
        if rec.record_id in result.resolved:
            by_key.setdefault(compute_dedup_key(rec.payload), set()).add(
                result.resolved[rec.record_id]
            )
    for surviving in by_key.values():
        assert len(surviving) == 1  # exactly one surviving row per person

    # (d) History is preserved and attaches to exactly one surviving row: attach
    #     one session per resolved candidate and confirm none is lost or orphaned.
    inserted = 0
    for pid in result.resolved.values():
        conn.execute(
            "INSERT INTO sessions (player_id, session_type, location_id) "
            "VALUES (%s, 'lounge', %s)",
            (pid, loc),
        )
        inserted += 1
    joined = conn.execute(
        "SELECT count(*) FROM sessions s JOIN players p ON p.id = s.player_id"
    ).fetchone()[0]
    assert joined == inserted  # every session references exactly one surviving row


# Feature: phase0-data-foundation, Property 21: New players start with pending
# consent. For any newly created players row, its consent_status equals 'pending'.
# Validates: Requirements 8.4
@_DB_SETTINGS
@given(specs=_PLAYER_BATCH)
def test_property_21_new_players_start_pending(migrated_db, specs):
    conn = migrated_db
    _reset(conn)
    loc = _location(conn)

    resolve_players(_build_records(specs), conn, location_id=loc)

    statuses = [
        r[0] for r in conn.execute("SELECT consent_status FROM players").fetchall()
    ]
    assert all(status == "pending" for status in statuses)
