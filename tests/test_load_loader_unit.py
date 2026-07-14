"""Unit tests for the clean-record loader (Task 11.1-11.4).

Pure-function tests cover amount->cents normalization, the deterministic
natural_key, the POPIA field filter, and the shared archive-key helper.
DB-backed tests (skipped when no PostgreSQL is reachable) cover FK resolution,
FK-failure flagging, natural-key idempotency, lesson tagging + provenance, and
the defensive POPIA filter reaching the database.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from funhouse_pipeline.archive import archive_key
from funhouse_pipeline.db.migrations import run_migrations
from funhouse_pipeline.db.seed import seed
from funhouse_pipeline.extract.context import build_business_rules
from funhouse_pipeline.extract.records import ExtractedRecord
from funhouse_pipeline.load.loader import (
    BAD_AMOUNT,
    UNRESOLVED_PLAYER,
    UNRESOLVED_SCHOOL,
    amount_to_cents,
    compute_natural_key,
    load_clean_records,
)
from funhouse_pipeline.load.popia import filter_payload, is_prohibited_key


def _rec(record_id, table, payload, *, confidence=0.95, source_file=None):
    return ExtractedRecord(
        record_id=record_id,
        target_table=table,
        payload=payload,
        confidence_score=confidence,
        source_file=source_file or f"cards/{record_id}.png",
        provider="bedrock",
        extracted_at=datetime(2024, 1, 1),
    )


# --------------------------------------------------------------------------- #
# Pure functions
# --------------------------------------------------------------------------- #


def test_amount_to_cents_rand_prefix_and_bare_number():
    known = frozenset({1000, 3000, 5000, 35000})
    assert amount_to_cents("R30", known_cents=known) == 3000
    assert amount_to_cents("R30.00", known_cents=known) == 3000
    # Bare number matches the validated tier via either interpretation.
    assert amount_to_cents("30", known_cents=known) == 3000
    assert amount_to_cents("3000", known_cents=known) == 3000  # cents interpretation
    assert amount_to_cents(50, known_cents=known) == 5000
    # No known tiers -> Rand default (×100).
    assert amount_to_cents("30") == 3000
    assert amount_to_cents("R7") == 700
    # Unparseable.
    assert amount_to_cents("") is None
    assert amount_to_cents("abc") is None
    assert amount_to_cents(None) is None


def test_compute_natural_key_is_deterministic_and_provenance_sensitive():
    payload = {"player_name": "John Smith", "session_type": "lesson",
               "started_at": "2024-01-01", "ended_at": "2024-01-01"}
    k1 = compute_natural_key("sessions", payload, "cards/a.png")
    k2 = compute_natural_key("sessions", dict(payload), "cards/a.png")
    assert k1 == k2  # deterministic
    assert k1.startswith("sessions:")
    # Different provenance -> different key.
    assert compute_natural_key("sessions", payload, "cards/b.png") != k1
    # Case/whitespace normalized identically.
    payload2 = {"player_name": " john   smith ", "session_type": "LESSON",
                "started_at": "2024-01-01", "ended_at": "2024-01-01"}
    assert compute_natural_key("sessions", payload2, "cards/a.png") == k1


def test_popia_filter_strips_id_numbers_and_addresses():
    assert is_prohibited_key("id_number")
    assert is_prohibited_key("ID Number")
    assert is_prohibited_key("national_id")
    assert is_prohibited_key("address")
    assert is_prohibited_key("physical_address")
    assert is_prohibited_key("street")
    assert not is_prohibited_key("first_name")
    assert not is_prohibited_key("player_name")

    clean, dropped = filter_payload(
        {
            "first_name": "John",
            "id_number": "8001015009087",
            "physical_address": "12 Main St",
            "grade": "4",
        }
    )
    assert clean == {"first_name": "John", "grade": "4"}
    assert set(dropped) == {"id_number", "physical_address"}


def test_archive_key_convention():
    assert archive_key("/data/source/lessons/week1.docx") == "raw/lessons/week1.docx"
    assert archive_key("photos/img_0001.jpg") == "raw/photos/img_0001.jpg"
    assert archive_key("week1.docx") == "raw/week1.docx"


# --------------------------------------------------------------------------- #
# DB-backed behavior
# --------------------------------------------------------------------------- #

pytestmark_db = pytest.mark.db


@pytest.fixture
def seeded_db(db_connection):
    """Migrated + seeded schema; returns (conn, location_id, rules)."""
    run_migrations(db_connection)
    seed(db_connection)
    loc = db_connection.execute(
        "SELECT id FROM locations WHERE name = 'Smithfield'"
    ).fetchone()[0]
    return db_connection, loc, build_business_rules()


@pytest.mark.db
def test_session_loads_with_resolved_player_fk(seeded_db):
    conn, loc, rules = seeded_db
    records = [
        _rec("p1", "players", {"first_name": "John", "last_name": "Smith"}),
        _rec("s1", "sessions", {"player_name": "John Smith", "session_type": "lounge"}),
    ]
    result = load_clean_records(records, conn, location_id=loc, rules=rules)

    assert not result.flagged
    assert len(result.loaded) == 1
    assert result.loaded[0].table == "sessions"
    row = conn.execute(
        "SELECT p.first_name FROM sessions s JOIN players p ON p.id = s.player_id"
    ).fetchone()
    assert row[0] == "John"


@pytest.mark.db
def test_unresolved_player_name_is_flagged_not_inserted(seeded_db):
    conn, loc, rules = seeded_db
    records = [_rec("s1", "sessions", {"player_name": "Nobody Here", "session_type": "lounge"})]
    result = load_clean_records(records, conn, location_id=loc, rules=rules)

    assert result.loaded == []
    assert [f.reason for f in result.flagged] == [UNRESOLVED_PLAYER]
    assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0


@pytest.mark.db
def test_payment_amount_to_cents_and_product_fk(seeded_db):
    conn, loc, rules = seeded_db
    records = [
        _rec("p1", "players", {"first_name": "Jane", "last_name": "Doe"}),
        _rec("pay1", "payments", {"player_name": "Jane Doe", "product_name": "PayPerUse-1hr",
                                  "amount": "R30", "method": "cash"}),
    ]
    result = load_clean_records(records, conn, location_id=loc, rules=rules)
    assert not result.flagged
    amount, has_product = conn.execute(
        "SELECT amount_cents, product_id IS NOT NULL FROM payments"
    ).fetchone()
    assert amount == 3000
    assert has_product is True


@pytest.mark.db
def test_bad_amount_is_flagged(seeded_db):
    conn, loc, rules = seeded_db
    records = [
        _rec("p1", "players", {"first_name": "Jane", "last_name": "Doe"}),
        _rec("pay1", "payments", {"player_name": "Jane Doe", "amount": "not-a-number"}),
    ]
    result = load_clean_records(records, conn, location_id=loc, rules=rules)
    assert [f.reason for f in result.flagged] == [BAD_AMOUNT]
    assert conn.execute("SELECT count(*) FROM payments").fetchone()[0] == 0


@pytest.mark.db
def test_natural_key_idempotency_skips_duplicate(seeded_db):
    conn, loc, rules = seeded_db
    player = _rec("p1", "players", {"first_name": "John", "last_name": "Smith"})
    session = _rec("s1", "sessions", {"player_name": "John Smith", "session_type": "lounge",
                                      "started_at": "2024-01-01"})

    first = load_clean_records([player, session], conn, location_id=loc, rules=rules)
    assert len(first.loaded) == 1

    # Re-loading the identical record is a no-op skip (Req 9.5, 13.3).
    second = load_clean_records([player, session], conn, location_id=loc, rules=rules)
    assert second.loaded == []
    assert len(second.skipped) == 1
    assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1


@pytest.mark.db
def test_lesson_tagging_and_original_file_ref(seeded_db):
    conn, loc, rules = seeded_db
    records = [
        _rec("l1", "lessons",
             {"title": "Shadows", "topic": "Light", "phenomenon": "Shadow formation",
              "content": "..."},
             source_file="lessons/shadows.docx"),
    ]
    result = load_clean_records(records, conn, location_id=loc, rules=rules)
    assert len(result.loaded) == 1
    topic, phenomenon, ref = conn.execute(
        "SELECT topic, phenomenon, original_file_ref FROM lessons"
    ).fetchone()
    assert topic == "Light"
    assert phenomenon == "Shadow formation"
    assert ref == "raw/lessons/shadows.docx"


@pytest.mark.db
def test_popia_prohibited_fields_never_reach_players_row(seeded_db):
    conn, loc, rules = seeded_db
    records = [
        _rec("p1", "players",
             {"first_name": "John", "last_name": "Smith",
              "id_number": "SENTINEL_ID", "physical_address": "SENTINEL_ADDR"}),
    ]
    result = load_clean_records(records, conn, location_id=loc, rules=rules)
    assert result.dropped_fields["p1"]
    # Neither sentinel appears in any players column.
    row = conn.execute("SELECT * FROM players").fetchone()
    assert all("SENTINEL" not in str(v) for v in row)


@pytest.mark.db
def test_present_but_unresolvable_school_on_player_is_flagged(seeded_db):
    conn, loc, rules = seeded_db
    records = [
        _rec("p1", "players",
             {"first_name": "John", "last_name": "Smith", "school_name": "Unknown School"}),
    ]
    result = load_clean_records(records, conn, location_id=loc, rules=rules)
    # The player row exists (needed for history) but the bad FK is flagged, not guessed.
    assert conn.execute("SELECT count(*) FROM players").fetchone()[0] == 1
    school_id = conn.execute("SELECT school_id FROM players").fetchone()[0]
    assert school_id is None
    assert [f.reason for f in result.flagged] == [UNRESOLVED_SCHOOL]
