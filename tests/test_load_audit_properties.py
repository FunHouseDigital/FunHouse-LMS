"""Property-based tests for the Load audit trail and consent ledger (Task 12).

Implements design Property 29 (every write is audited) and Property 24 (the
consent ledger is append-only and monotonic). Both require a reachable
PostgreSQL server and are skipped automatically otherwise (see conftest's
``db_connection`` fixture). Each property runs a minimum of 100 Hypothesis
iterations, per the design's Testing Strategy.

The ``db_connection`` fixture is function-scoped and reused across a single
test's generated examples. Property 29 clears the working + audit tables between
examples so ``sync_log`` counts are meaningful per example; Property 24 does
*not* clear ``consents`` (it is append-only by design) and instead checks
deltas/invariants relative to a per-example starting snapshot.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from funhouse_pipeline.db.migrations import run_migrations
from funhouse_pipeline.db.seed import seed
from funhouse_pipeline.extract.context import build_business_rules
from funhouse_pipeline.extract.records import ExtractedRecord
from funhouse_pipeline.load.consent import append_consent, revoke_consent
from funhouse_pipeline.load.loader import load_clean_records

pytestmark = [pytest.mark.db, pytest.mark.property]

_DB_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

_PLAYERS = [("John", "Smith"), ("Jane", "Doe"), ("Sam", "Lee"), ("Alex", "Ng")]
_PLAYER_NAMES = [f"{f} {l}" for f, l in _PLAYERS]
_SESSION_TYPES = ["lesson", "kit", "esports", "lounge"]
_METRIC_TYPES = ["typing_wpm", "typing_accuracy", "homework_done", "quiz_score", "observation"]
_AMOUNTS = ["R10", "R30", "R50", "R350"]
_PRODUCTS = ["PayPerUse-20min", "PayPerUse-1hr", "PayPerUse-2hr", "Subscription"]

# Tables that carry a logged_by column per the schema (Property 29 checks it).
_HAS_LOGGED_BY = {"sessions", "payments", "student_metrics"}


@pytest.fixture
def seeded_db(db_connection):
    run_migrations(db_connection)
    seed(db_connection)
    loc = db_connection.execute(
        "SELECT id FROM locations WHERE name = 'Smithfield'"
    ).fetchone()[0]
    aya = db_connection.execute(
        "SELECT id FROM users WHERE name = 'Aya'"
    ).fetchone()[0]
    return db_connection, loc, aya, build_business_rules()


def _reset(conn) -> None:
    """Clear working + audit tables between examples (keep seed data)."""
    conn.execute("DELETE FROM sync_log")
    conn.execute("DELETE FROM student_metrics")
    conn.execute("DELETE FROM payments")
    conn.execute("DELETE FROM sessions")
    conn.execute("DELETE FROM lessons")
    conn.execute("DELETE FROM players")


def _player_records():
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


def _build_other_records(specs):
    records = []
    for i, spec in enumerate(specs):
        records.append(
            ExtractedRecord(
                record_id=f"rec_{i}",
                target_table=spec["table"],
                payload=_payload_for(spec),
                confidence_score=0.95,
                source_file=f"cards/src_{spec['source_idx']}_{i}.png"
                if spec["table"] != "lessons"
                else f"lessons/src_{spec['source_idx']}_{i}.docx",
                provider="bedrock",
                extracted_at=datetime(2024, 1, 1),
            )
        )
    return records


def _sync_log_count(conn, entity, record_id, action, user_id):
    return conn.execute(
        "SELECT count(*) FROM sync_log "
        "WHERE entity = %s AND record_id = %s AND action = %s AND user_id = %s",
        (entity, record_id, action, user_id),
    ).fetchone()[0]


# Feature: phase0-data-foundation, Property 29: Every write is audited. For any
# record written to the database, the acting identity is recorded in logged_by
# (where the table has it) and a corresponding sync_log entry referencing the
# entity, record id, and action is appended.
# Validates: Requirements 14.5
@_DB_SETTINGS
@given(specs=st.lists(_RECORD_SPEC, min_size=1, max_size=8))
def test_property_29_every_write_is_audited(seeded_db, specs):
    conn, loc, aya, rules = seeded_db
    _reset(conn)

    records = _player_records() + _build_other_records(specs)

    # First load: every record is inserted.
    result = load_clean_records(
        records, conn, location_id=loc, rules=rules, logged_by=aya, device_id="dev-1"
    )
    assert result.flagged == []

    # (a) Every inserted non-player row has a matching 'insert' sync_log entry
    #     referencing (entity, record_id, action) with the acting identity; and
    #     where the table carries logged_by, the row's logged_by is the actor.
    for loaded in result.loaded:
        assert _sync_log_count(conn, loaded.table, loaded.row_id, "insert", aya) >= 1
        if loaded.table in _HAS_LOGGED_BY:
            logged_by = conn.execute(
                f"SELECT logged_by FROM {loaded.table} WHERE id = %s", (loaded.row_id,)
            ).fetchone()[0]
            assert logged_by == aya

    # (b) Every newly created player row is audited (players has no logged_by
    #     column, so only the sync_log entry is required).
    for player_id in result.players.created:
        assert _sync_log_count(conn, "players", player_id, "insert", aya) >= 1

    # Second load of the SAME records: every non-player record is now a skip and
    # each skip appends a matching 'skip' sync_log entry (Req 9.5, 14.5).
    result2 = load_clean_records(
        records, conn, location_id=loc, rules=rules, logged_by=aya, device_id="dev-1"
    )
    assert result2.flagged == []
    assert result2.loaded == []
    assert len(result2.skipped) == len(specs)
    for skipped in result2.skipped:
        existing_id = conn.execute(
            f"SELECT id FROM {skipped.table} WHERE natural_key = %s",
            (skipped.natural_key,),
        ).fetchone()[0]
        assert _sync_log_count(conn, skipped.table, existing_id, "skip", aya) >= 1


def _consent_ops():
    """A non-empty sequence of grant/revoke operations over two consent types."""
    op = st.sampled_from(
        [
            ("data_processing", True),
            ("data_processing", False),
            ("photo", True),
            ("photo", False),
        ]
    )
    return st.lists(op, min_size=1, max_size=10)


def _snapshot_consents(conn) -> dict:
    """Map every consents row id -> its full column tuple (byte-for-byte state)."""
    rows = conn.execute("SELECT * FROM consents").fetchall()
    # The primary key id is the first column (schema defines id first).
    return {row[0]: row for row in rows}


# Feature: phase0-data-foundation, Property 24: Consent ledger is append-only and
# monotonic. For any sequence of consent operations (including revocations), the
# consents row count is monotonically non-decreasing, every previously written
# row remains byte-for-byte unchanged, and a revocation is represented as a
# newly appended row.
# Validates: Requirements 11.1, 11.2, 11.3
@_DB_SETTINGS
@given(ops=_consent_ops())
def test_property_24_consent_ledger_append_only_and_monotonic(seeded_db, ops):
    conn, loc, aya, _rules = seeded_db

    # A subject player to attach consents to (append-only: we never clear the
    # ledger between examples, so invariants are checked relative to the current
    # starting state).
    player_id = conn.execute(
        "INSERT INTO players (first_name, consent_status, location_id) "
        "VALUES ('Consent', 'pending', %s) RETURNING id",
        (loc,),
    ).fetchone()[0]
    conn.commit()

    prev_snapshot = _snapshot_consents(conn)
    prev_count = len(prev_snapshot)

    for consent_type, granted in ops:
        if granted:
            new_id = append_consent(
                conn,
                player_id=player_id,
                consent_type=consent_type,
                granted=True,
                location_id=loc,
                captured_by_user_id=aya,
            )
        else:
            new_id = revoke_consent(
                conn,
                player_id=player_id,
                consent_type=consent_type,
                location_id=loc,
                captured_by_user_id=aya,
            )

        snapshot = _snapshot_consents(conn)

        # Monotonic, append-only: count strictly grows by exactly one row.
        assert len(snapshot) == prev_count + 1

        # Every previously written row is byte-for-byte unchanged.
        for row_id, row in prev_snapshot.items():
            assert row_id in snapshot, "a previously written consents row disappeared"
            assert snapshot[row_id] == row, "a previously written consents row changed"

        # The operation is represented as a newly appended row with the expected
        # grant/revoke flag (a revocation is a new row, not an edit).
        assert new_id in snapshot and new_id not in prev_snapshot
        appended_granted = conn.execute(
            "SELECT granted FROM consents WHERE id = %s", (new_id,)
        ).fetchone()[0]
        assert appended_granted is granted

        prev_snapshot = snapshot
        prev_count = len(snapshot)
