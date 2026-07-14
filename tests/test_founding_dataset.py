"""Example test for the full founding dataset (Task 15.3, Req 8.5).

Requirement 8.5: when player loading completes, the ``players`` table contains
**all 73 learners and the lounge regulars as deduplicated rows**.

There is no real historical data in the repository, so this test constructs a
**representative fixture dataset** that stands in for the founding intake and
asserts the deduplicated outcome:

* **73 distinct learners** + **12 distinct lounge regulars** = **85 distinct
  people** (each a unique ``dedup_key``).
* Some people **appear again** in the intake -- exact repeats, and repeats with
  case/whitespace variation of the same name -- exactly as messy paper records
  would. These MUST collapse to a single ``players`` row per person (Req 8.1-8.3).

The fixture is fed through the real Load dedup/resolution path
(:func:`funhouse_pipeline.load.load_clean_records`) against an ephemeral,
migrated PostgreSQL schema, and we assert the ``players`` table ends with exactly
85 rows -- every duplicate merged, learners and lounge customers sharing the one
table. DB-backed, so skipped automatically when no PostgreSQL server is reachable.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from funhouse_pipeline.db.migrations import run_migrations
from funhouse_pipeline.extract.records import ExtractedRecord
from funhouse_pipeline.load import load_clean_records

pytestmark = pytest.mark.db

# Fixture sizing -- stands in for the real founding intake (Req 8.5).
LEARNER_COUNT = 73
LOUNGE_REGULAR_COUNT = 12
EXPECTED_DISTINCT_PLAYERS = LEARNER_COUNT + LOUNGE_REGULAR_COUNT  # 85


def _player_record(record_id: str, first: str, last: str, birth_date, source: str, conf=0.95):
    return ExtractedRecord(
        record_id=record_id,
        target_table="players",
        payload={"first_name": first, "last_name": last, "birth_date": birth_date},
        confidence_score=conf,
        source_file=source,
        provider="bedrock",
        extracted_at=datetime(2024, 1, 1),
    )


def _build_founding_fixture() -> tuple[list[ExtractedRecord], dict[str, str]]:
    """Return (records, expected_identity_by_record_id).

    ``expected_identity_by_record_id`` maps each *duplicate* record id back to
    the record id of the first appearance of that same person, so the test can
    assert both resolved to the one surviving row.
    """
    records: list[ExtractedRecord] = []
    duplicate_of: dict[str, str] = {}

    # --- 73 distinct learners (unique first name -> unique dedup_key) -------- #
    learner_ids: list[str] = []
    for i in range(1, LEARNER_COUNT + 1):
        rid = f"learner-{i:02d}"
        learner_ids.append(rid)
        birth = date(2010, 1, 1).replace(year=2008 + (i % 6))  # spread of ages
        records.append(
            _player_record(rid, f"Learner{i:02d}", "Mokoena", birth.isoformat(),
                           source=f"cards/learner_{i:02d}.png")
        )

    # --- 12 distinct lounge regulars (no birth date, as lounge walk-ins) ----- #
    lounge_ids: list[str] = []
    for j in range(1, LOUNGE_REGULAR_COUNT + 1):
        rid = f"lounge-{j:02d}"
        lounge_ids.append(rid)
        records.append(
            _player_record(rid, f"Lounge{j:02d}", "Dlamini", None,
                           source=f"sheets/lounge_{j:02d}.png")
        )

    # --- Duplicate appearances that MUST dedup to one row each -------------- #
    # (a) 10 learners appear again in a second source, byte-identical identity.
    for i in range(1, 11):
        rid = f"learner-{i:02d}-again"
        duplicate_of[rid] = f"learner-{i:02d}"
        birth = date(2010, 1, 1).replace(year=2008 + (i % 6))
        records.append(
            _player_record(rid, f"Learner{i:02d}", "Mokoena", birth.isoformat(),
                           source=f"whatsapp/roster_{i:02d}.png")
        )

    # (b) 3 learners re-appear with case/whitespace variation of the same name
    #     -- normalization must still collapse them to the same person.
    for i in range(11, 14):
        rid = f"learner-{i:02d}-messy"
        duplicate_of[rid] = f"learner-{i:02d}"
        birth = date(2010, 1, 1).replace(year=2008 + (i % 6))
        records.append(
            _player_record(rid, f"  learner{i:02d}  ".upper(), " mokoena ",
                           birth.isoformat(), source=f"photos/note_{i:02d}.png")
        )

    # (c) 3 lounge regulars re-appear (exact repeat).
    for j in range(1, 4):
        rid = f"lounge-{j:02d}-again"
        duplicate_of[rid] = f"lounge-{j:02d}"
        records.append(
            _player_record(rid, f"Lounge{j:02d}", "Dlamini", None,
                           source=f"whatsapp/lounge_repeat_{j:02d}.png")
        )

    return records, duplicate_of


@pytest.fixture
def seeded_db(db_connection):
    """Migrated schema plus a single Smithfield location; returns (conn, loc_id)."""
    run_migrations(db_connection)
    loc_id = db_connection.execute(
        "INSERT INTO locations (name) VALUES ('Smithfield') RETURNING id"
    ).fetchone()[0]
    db_connection.commit()
    return db_connection, loc_id


def test_founding_dataset_dedups_to_73_learners_plus_lounge_regulars(seeded_db):
    conn, loc_id = seeded_db
    records, duplicate_of = _build_founding_fixture()

    # Sanity check on the fixture itself: it DOES contain duplicate appearances.
    assert len(records) == EXPECTED_DISTINCT_PLAYERS + len(duplicate_of)
    assert len(duplicate_of) == 16  # 10 exact + 3 messy learners + 3 lounge

    result = load_clean_records(records, conn, location_id=loc_id)

    # No person was ambiguous / dropped -- every record resolved to a row.
    assert result.players.flagged == []
    assert len(result.players.resolved) == len(records)

    # Exactly 85 distinct people were created; duplicates merged, not inserted.
    assert len(result.players.created) == EXPECTED_DISTINCT_PLAYERS

    player_count = conn.execute("SELECT count(*) FROM players").fetchone()[0]
    assert player_count == EXPECTED_DISTINCT_PLAYERS, (
        "learners + lounge regulars must be deduplicated to one row per person (Req 8.5)"
    )

    # Every duplicate appearance resolved to the SAME surviving row as its first
    # appearance (combined history attaches to one identity -- Req 8.2, 8.3).
    for dup_id, original_id in duplicate_of.items():
        assert result.players.resolved[dup_id] == result.players.resolved[original_id]

    # New rows start with pending consent (Req 8.4).
    statuses = conn.execute("SELECT DISTINCT consent_status FROM players").fetchall()
    assert {row[0] for row in statuses} == {"pending"}
