"""Property-based tests for clean-record loading (Tasks 11.5, 11.6, 11.7).

Implements design Properties 22, 28, and 27. All require a reachable PostgreSQL
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
from unittest import mock

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from funhouse_pipeline.db.migrations import run_migrations
from funhouse_pipeline.db.seed import seed
from funhouse_pipeline.extract.context import build_business_rules
from funhouse_pipeline.extract.records import ExtractedRecord
from funhouse_pipeline.load.loader import compute_natural_key, load_clean_records

pytestmark = [pytest.mark.db, pytest.mark.property]

_DB_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# A fixed pool of players (distinct full names) referenced by other records.
_PLAYERS = [("John", "Smith"), ("Jane", "Doe"), ("Sam", "Lee"), ("Alex", "Ng")]
_PLAYER_NAMES = [f"{f} {l}" for f, l in _PLAYERS]

_SESSION_TYPES = ["lesson", "kit", "esports", "lounge"]
_METRIC_TYPES = ["typing_wpm", "typing_accuracy", "homework_done", "quiz_score", "observation"]
# All amounts below map to a seeded product price (validated tiers).
_AMOUNTS = ["R10", "R30", "R50", "R350"]
_PRODUCTS = ["PayPerUse-20min", "PayPerUse-1hr", "PayPerUse-2hr", "Subscription"]

# Prohibited keys the POPIA filter must strip (Property 28).
_PROHIBITED_KEYS = ["id_number", "national_id", "physical_address", "street", "address"]


@pytest.fixture
def seeded_db(db_connection):
    run_migrations(db_connection)
    seed(db_connection)
    loc = db_connection.execute(
        "SELECT id FROM locations WHERE name = 'Smithfield'"
    ).fetchone()[0]
    return db_connection, loc, build_business_rules()


def _reset(conn) -> None:
    """Clear working tables between Hypothesis examples (keep seed data)."""
    conn.execute("DELETE FROM student_metrics")
    conn.execute("DELETE FROM payments")
    conn.execute("DELETE FROM sessions")
    conn.execute("DELETE FROM lessons")
    conn.execute("DELETE FROM players")


def _player_records():
    """A clean player record for every name in the pool."""
    records = []
    for i, (first, last) in enumerate(_PLAYERS):
        records.append(
            ExtractedRecord(
                record_id=f"player_{i}",
                target_table="players",
                payload={"first_name": first, "last_name": last},
                confidence_score=0.95,
                source_file=f"cards/player_{i}.png",
                provider="bedrock",
                extracted_at=datetime(2024, 1, 1),
            )
        )
    return records


# A spec for one non-player record: (table, player_idx, choice, amount, session_type,
# metric_type, title_idx, source_idx).
_RECORD_SPEC = st.fixed_dictionaries(
    {
        "table": st.sampled_from(["sessions", "payments", "lessons", "student_metrics"]),
        "player_idx": st.integers(min_value=0, max_value=len(_PLAYERS) - 1),
        "amount": st.sampled_from(_AMOUNTS),
        "product": st.sampled_from(_PRODUCTS),
        "session_type": st.sampled_from(_SESSION_TYPES),
        "metric_type": st.sampled_from(_METRIC_TYPES),
        "title_idx": st.integers(min_value=0, max_value=5),
        "source_idx": st.integers(min_value=0, max_value=3),
    }
)


def _payload_for(spec):
    """Build a resolvable domain payload for a record spec."""
    player_name = _PLAYER_NAMES[spec["player_idx"]]
    table = spec["table"]
    if table == "sessions":
        return {"player_name": player_name, "session_type": spec["session_type"],
                "started_at": "2024-01-01", "ended_at": "2024-01-01"}
    if table == "payments":
        return {"player_name": player_name, "product_name": spec["product"],
                "amount": spec["amount"], "paid_at": "2024-01-01"}
    if table == "lessons":
        return {"title": f"Lesson {spec['title_idx']}", "topic": "Light",
                "phenomenon": "Shadows", "content": "body"}
    return {"player_name": player_name, "metric_type": spec["metric_type"],
            "value": "42", "measured_at": "2024-01-01"}


def _build_other_records(specs, *, extra_payload=None):
    records = []
    for i, spec in enumerate(specs):
        payload = _payload_for(spec)
        if extra_payload:
            payload = {**payload, **extra_payload(i)}
        records.append(
            ExtractedRecord(
                record_id=f"rec_{i}",
                target_table=spec["table"],
                payload=payload,
                confidence_score=0.95,
                source_file=f"cards/src_{spec['source_idx']}_{i}.png"
                if spec["table"] != "lessons"
                else f"lessons/src_{spec['source_idx']}_{i}.docx",
                provider="bedrock",
                extracted_at=datetime(2024, 1, 1),
            )
        )
    return records


# Feature: phase0-data-foundation, Property 22: Clean records load into their
# target tables (LLM-free). For any set of Clean_Records, the Loader creates a
# corresponding row in the correct target table for each record and issues no
# large-language-model call.
# Validates: Requirements 9.1, 9.2, 9.3, 9.4
@_DB_SETTINGS
@given(specs=st.lists(_RECORD_SPEC, min_size=1, max_size=8))
def test_property_22_clean_records_load_into_target_tables_llm_free(seeded_db, specs):
    conn, loc, rules = seeded_db
    _reset(conn)

    records = _player_records() + _build_other_records(specs)

    # No LLM call is issued anywhere in the load path (Req 9.2).
    with mock.patch("funhouse_pipeline.llm.llm_generate") as fake_llm:
        result = load_clean_records(records, conn, location_id=loc, rules=rules)
    assert fake_llm.call_count == 0

    # Every non-player Clean_Record resolves (no flags) and is loaded or skipped.
    assert result.flagged == []
    other_ids = {f"rec_{i}" for i in range(len(specs))}
    handled = {r.record_id for r in result.loaded} | {s.record_id for s in result.skipped}
    assert handled == other_ids

    # Each target table holds exactly the distinct natural_keys among its records
    # (a row was created in the correct target table for each record; duplicates
    # collapse via ON CONFLICT).
    expected_keys: dict[str, set] = {}
    for i, spec in enumerate(specs):
        table = spec["table"]
        rec = records[len(_PLAYERS) + i]
        key = compute_natural_key(table, rec.payload, rec.source_file)
        expected_keys.setdefault(table, set()).add(key)
    for table, keys in expected_keys.items():
        count = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        assert count == len(keys)

    # Players landed in the single players table too (Req 9.1/9.3).
    assert conn.execute("SELECT count(*) FROM players").fetchone()[0] == len(_PLAYERS)


# Feature: phase0-data-foundation, Property 28: No prohibited personal data is
# loaded. For any loaded row, no field contains a national identity number or
# physical address.
# Validates: Requirements 14.1
@_DB_SETTINGS
@given(
    specs=st.lists(_RECORD_SPEC, min_size=1, max_size=6),
    key_choices=st.lists(st.sampled_from(_PROHIBITED_KEYS), min_size=1, max_size=3),
)
def test_property_28_no_prohibited_personal_data_loaded(seeded_db, specs, key_choices):
    conn, loc, rules = seeded_db
    _reset(conn)

    sentinel = "PROHIBITED_PII_VALUE"

    def extra(i):
        return {key: f"{sentinel}_{i}" for key in key_choices}

    # Inject prohibited fields into both player and other records.
    players = _player_records()
    for p in players:
        p.payload.update({key: sentinel for key in key_choices})
    records = players + _build_other_records(specs, extra_payload=extra)

    load_clean_records(records, conn, location_id=loc, rules=rules)

    # Scan every column of every row in every target table; the sentinel (and so
    # the prohibited data) must never appear.
    for table in ("players", "sessions", "payments", "lessons", "student_metrics"):
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        for row in rows:
            assert all(sentinel not in str(cell) for cell in row)


# Feature: phase0-data-foundation, Property 27: Lessons are tagged with topic and
# phenomenon. For any lesson whose source contains a topic and phenomenon, the
# loaded lessons row has both fields populated.
# Validates: Requirements 10.3
@_DB_SETTINGS
@given(
    lessons=st.lists(
        st.tuples(
            st.integers(min_value=0, max_value=20),  # title/source discriminator
            st.text(alphabet="abcdefghijk ", min_size=1, max_size=12),  # topic
            st.text(alphabet="abcdefghijk ", min_size=1, max_size=12),  # phenomenon
        ),
        min_size=1,
        max_size=6,
    )
)
def test_property_27_lessons_tagged_with_topic_and_phenomenon(seeded_db, lessons):
    conn, loc, rules = seeded_db
    _reset(conn)

    records = []
    for i, (disc, topic, phenomenon) in enumerate(lessons):
        # Only meaningful when the source actually carries both tags.
        if not topic.strip() or not phenomenon.strip():
            continue
        records.append(
            ExtractedRecord(
                record_id=f"lesson_{i}",
                target_table="lessons",
                payload={"title": f"Lesson {disc}-{i}", "topic": topic,
                         "phenomenon": phenomenon, "content": "body"},
                confidence_score=1.0,
                source_file=f"lessons/lesson_{disc}_{i}.docx",
                provider="docx-parser",
                extracted_at=datetime(2024, 1, 1),
            )
        )

    result = load_clean_records(records, conn, location_id=loc, rules=rules)
    assert result.flagged == []

    # Every loaded lessons row has both topic and phenomenon populated.
    missing = conn.execute(
        "SELECT count(*) FROM lessons WHERE topic IS NULL OR phenomenon IS NULL "
        "OR btrim(topic) = '' OR btrim(phenomenon) = ''"
    ).fetchone()[0]
    assert missing == 0
    total = conn.execute("SELECT count(*) FROM lessons").fetchone()[0]
    assert total == len(result.loaded) + len(result.skipped)
