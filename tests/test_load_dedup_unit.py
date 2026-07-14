"""Unit tests for the player deduplication / resolution layer (Task 10.1).

Pure-function tests cover the deterministic ``dedup_key`` rule and the
higher-confidence-non-null merge; DB-backed tests (skipped when no PostgreSQL is
reachable) cover exact-match merge, ambiguous-merge flagging, pending consent on
new rows, and merging into a pre-existing row.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from funhouse_pipeline.db.migrations import run_migrations
from funhouse_pipeline.extract.records import ExtractedRecord
from funhouse_pipeline.load.dedup import (
    AMBIGUOUS_MERGE,
    MISSING_NAME,
    compute_dedup_key,
    merge_attributes,
    name_key,
    normalize_birth_date,
    resolve_players,
    slug,
)


def _rec(record_id, payload, confidence=0.9):
    return ExtractedRecord(
        record_id=record_id,
        target_table="players",
        payload=payload,
        confidence_score=confidence,
        source_file=f"src/{record_id}.png",
        provider="bedrock",
        extracted_at=datetime(2024, 1, 1),
    )


# --------------------------------------------------------------------------- #
# Pure functions
# --------------------------------------------------------------------------- #


def test_slug_normalizes_case_and_whitespace():
    assert slug("  De  Villiers ") == "de villiers"
    assert slug("JOHN") == "john"
    assert slug(None) == ""


def test_normalize_birth_date_handles_forms():
    assert normalize_birth_date("2010-04-01") == "2010-04-01"
    assert normalize_birth_date(date(2010, 4, 1)) == "2010-04-01"
    assert normalize_birth_date(datetime(2010, 4, 1, 12, 30)) == "2010-04-01"
    assert normalize_birth_date("2010-04-01T00:00:00Z") == "2010-04-01"
    assert normalize_birth_date("") == ""
    assert normalize_birth_date("not-a-date") == ""
    assert normalize_birth_date(None) == ""


def test_dedup_key_rule_is_deterministic_and_documented():
    payload = {"first_name": "John", "last_name": "Smith", "birth_date": "2010-04-01"}
    assert compute_dedup_key(payload) == "john|smith|2010-04-01"
    # Whitespace/case variations collapse to the same key.
    variant = {"first_name": " john ", "last_name": "SMITH", "birth_date": date(2010, 4, 1)}
    assert compute_dedup_key(variant) == compute_dedup_key(payload)
    # Absent birth date leaves the trailing part empty.
    assert compute_dedup_key({"first_name": "John", "last_name": "Smith"}) == "john|smith|"
    assert name_key(payload) == "john|smith"


def test_merge_attributes_favors_higher_confidence_non_null():
    low = _rec("a", {"first_name": "John", "last_name": None, "grade": "3"}, confidence=0.4)
    high = _rec("b", {"first_name": "John", "last_name": "Smith", "grade": None}, confidence=0.95)
    merged = merge_attributes([low, high])
    assert merged["first_name"] == "John"
    # last_name only present on the (higher-confidence) record.
    assert merged["last_name"] == "Smith"
    # grade only present on the lower-confidence record -> filled from it.
    assert merged["grade"] == "3"


# --------------------------------------------------------------------------- #
# DB-backed behavior
# --------------------------------------------------------------------------- #

pytestmark_db = pytest.mark.db


@pytest.fixture
def loaded_db(db_connection):
    """Migrated schema plus a single location row; returns (conn, location_id)."""
    run_migrations(db_connection)
    row = db_connection.execute(
        "INSERT INTO locations (name) VALUES ('Smithfield') RETURNING id"
    ).fetchone()
    return db_connection, row[0]


@pytest.mark.db
def test_exact_match_merges_into_one_row_with_pending_consent(loaded_db):
    conn, loc = loaded_db
    records = [
        _rec("r1", {"first_name": "John", "last_name": "Smith", "birth_date": "2010-04-01"}),
        _rec("r2", {"first_name": "john", "last_name": " smith ", "birth_date": "2010-04-01"}),
    ]
    result = resolve_players(records, conn, location_id=loc)

    # Both candidates resolve to the same, single surviving row.
    assert result.resolved["r1"] == result.resolved["r2"]
    assert len(result.created) == 1
    count = conn.execute("SELECT count(*) FROM players").fetchone()[0]
    assert count == 1
    status = conn.execute("SELECT consent_status FROM players").fetchone()[0]
    assert status == "pending"


@pytest.mark.db
def test_conflicting_birth_date_same_name_is_flagged_not_merged(loaded_db):
    conn, loc = loaded_db
    records = [
        _rec("r1", {"first_name": "Jane", "last_name": "Doe", "birth_date": "2010-04-01"}),
        _rec("r2", {"first_name": "Jane", "last_name": "Doe", "birth_date": "2011-05-02"}),
    ]
    result = resolve_players(records, conn, location_id=loc)

    assert result.resolved == {}
    assert len(result.created) == 0
    reasons = {f.reason for f in result.flagged}
    assert reasons == {AMBIGUOUS_MERGE}
    assert {f.identity for f in result.flagged} == {"r1", "r2"}
    # Nothing ambiguous was written.
    assert conn.execute("SELECT count(*) FROM players").fetchone()[0] == 0


@pytest.mark.db
def test_missing_name_is_flagged(loaded_db):
    conn, loc = loaded_db
    records = [_rec("r1", {"first_name": "", "last_name": None, "birth_date": "2010-04-01"})]
    result = resolve_players(records, conn, location_id=loc)
    assert result.resolved == {}
    assert [f.reason for f in result.flagged] == [MISSING_NAME]


@pytest.mark.db
def test_merge_into_existing_row_fills_null_gap(loaded_db):
    conn, loc = loaded_db
    # Pre-existing row (same dedup_key as the incoming record) with a NULL grade.
    existing = conn.execute(
        "INSERT INTO players (first_name, last_name, birth_date, dedup_key, location_id) "
        "VALUES ('John','Smith','2010-04-01','john|smith|2010-04-01', %s) RETURNING id",
        (loc,),
    ).fetchone()[0]

    records = [
        _rec("r1", {"first_name": "John", "last_name": "Smith",
                    "birth_date": "2010-04-01", "grade": "4"}),
    ]
    result = resolve_players(records, conn, location_id=loc)

    # No new row; merged into the existing surviving row.
    assert result.resolved["r1"] == existing
    assert result.merged_into_existing["r1"] == existing
    assert conn.execute("SELECT count(*) FROM players").fetchone()[0] == 1

    grade = conn.execute(
        "SELECT grade FROM players WHERE id = %s", (existing,)
    ).fetchone()[0]
    # The NULL grade gap is filled from the incoming record.
    assert grade == "4"


@pytest.mark.db
def test_merge_into_existing_row_does_not_clobber_existing_value(loaded_db):
    conn, loc = loaded_db
    # Pre-existing row already carries a grade ('3').
    existing = conn.execute(
        "INSERT INTO players (first_name, last_name, birth_date, grade, dedup_key, location_id) "
        "VALUES ('John','Smith','2010-04-01','3','john|smith|2010-04-01', %s) RETURNING id",
        (loc,),
    ).fetchone()[0]

    records = [
        _rec("r1", {"first_name": "John", "last_name": "Smith",
                    "birth_date": "2010-04-01", "grade": "4"}),
    ]
    result = resolve_players(records, conn, location_id=loc)

    assert result.resolved["r1"] == existing
    grade = conn.execute(
        "SELECT grade FROM players WHERE id = %s", (existing,)
    ).fetchone()[0]
    # Already-loaded value is authoritative and is NOT overwritten.
    assert grade == "3"
