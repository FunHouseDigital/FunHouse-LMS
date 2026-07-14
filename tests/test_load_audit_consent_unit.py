"""Unit tests for the Load audit trail and append-only consent ledger (Task 12).

These are DB-backed example tests (they need a reachable PostgreSQL and skip
otherwise, like the other Load tests). They complement the property tests with
concrete, readable assertions about:

* ``sync_log`` auditing of loader inserts and skips, and ``logged_by`` being set
  where the column exists (Req 14.5);
* the append-only consent API appending grants and revocations as new rows and
  never issuing an UPDATE/DELETE (Req 11.1-11.3);
* the database trigger rejecting a direct UPDATE/DELETE on ``consents``
  (Req 11.3, the enforcement backstop);
* :func:`load_consent_records` resolving players and flagging unresolved ones.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from funhouse_pipeline.db.migrations import run_migrations
from funhouse_pipeline.db.seed import seed
from funhouse_pipeline.extract.context import build_business_rules
from funhouse_pipeline.extract.records import ExtractedRecord
from funhouse_pipeline.load.consent import (
    UNRESOLVED_PLAYER,
    append_consent,
    load_consent_records,
    revoke_consent,
)
from funhouse_pipeline.load.loader import load_clean_records

pytestmark = [pytest.mark.db]


@pytest.fixture
def seeded_db(db_connection):
    run_migrations(db_connection)
    seed(db_connection)
    loc = db_connection.execute(
        "SELECT id FROM locations WHERE name = 'Smithfield'"
    ).fetchone()[0]
    aya = db_connection.execute("SELECT id FROM users WHERE name = 'Aya'").fetchone()[0]
    return db_connection, loc, aya, build_business_rules()


def _rec(record_id, table, payload, source):
    return ExtractedRecord(
        record_id=record_id,
        target_table=table,
        payload=payload,
        confidence_score=0.95,
        source_file=source,
        provider="bedrock",
        extracted_at=datetime(2024, 1, 1),
    )


def _new_player(conn, loc, first="Consent"):
    pid = conn.execute(
        "INSERT INTO players (first_name, consent_status, location_id) "
        "VALUES (%s, 'pending', %s) RETURNING id",
        (first, loc),
    ).fetchone()[0]
    conn.commit()
    return pid


# --------------------------------------------------------------------------- #
# Loader audit trail (Req 14.5)
# --------------------------------------------------------------------------- #


def test_loader_audits_insert_then_skip_with_logged_by(seeded_db):
    conn, loc, aya, rules = seeded_db
    records = [
        _rec("p0", "players", {"first_name": "John", "last_name": "Smith"}, "cards/p0.png"),
        _rec(
            "pay0",
            "payments",
            {"player_name": "John Smith", "product_name": "PayPerUse-1hr",
             "amount": "R30", "paid_at": "2024-01-01"},
            "cards/pay0.png",
        ),
    ]

    result = load_clean_records(
        records, conn, location_id=loc, rules=rules, logged_by=aya, device_id="dev-9"
    )
    assert result.flagged == []
    assert len(result.loaded) == 1

    payment = result.loaded[0]
    # logged_by set on the payments row (Req 14.5).
    logged_by = conn.execute(
        "SELECT logged_by FROM payments WHERE id = %s", (payment.row_id,)
    ).fetchone()[0]
    assert logged_by == aya

    # An 'insert' sync_log entry references the row, actor, and device.
    audit = conn.execute(
        "SELECT action, user_id, device_id FROM sync_log "
        "WHERE entity = 'payments' AND record_id = %s",
        (payment.row_id,),
    ).fetchone()
    assert audit == ("insert", aya, "dev-9")

    # Re-loading the same batch turns the payment into a skip that is audited.
    result2 = load_clean_records(
        records, conn, location_id=loc, rules=rules, logged_by=aya, device_id="dev-9"
    )
    assert [s.table for s in result2.skipped] == ["payments"]
    skip_actions = conn.execute(
        "SELECT count(*) FROM sync_log "
        "WHERE entity = 'payments' AND record_id = %s AND action = 'skip'",
        (payment.row_id,),
    ).fetchone()[0]
    assert skip_actions == 1


# --------------------------------------------------------------------------- #
# Append-only consent ledger (Req 11.1-11.3)
# --------------------------------------------------------------------------- #


def test_append_consent_writes_row_and_audit(seeded_db):
    conn, loc, aya, _rules = seeded_db
    player_id = _new_player(conn, loc)

    cid = append_consent(
        conn,
        player_id=player_id,
        consent_type="data_processing",
        granted=True,
        location_id=loc,
        method="paper",
        captured_by_user_id=aya,
    )

    row = conn.execute(
        "SELECT player_id, consent_type, granted, method FROM consents WHERE id = %s",
        (cid,),
    ).fetchone()
    assert row == (player_id, "data_processing", True, "paper")

    audited = conn.execute(
        "SELECT count(*) FROM sync_log "
        "WHERE entity = 'consents' AND record_id = %s AND action = 'insert'",
        (cid,),
    ).fetchone()[0]
    assert audited == 1


def test_revocation_is_new_appended_row(seeded_db):
    conn, loc, aya, _rules = seeded_db
    player_id = _new_player(conn, loc)

    grant_id = append_consent(
        conn, player_id=player_id, consent_type="photo", granted=True, location_id=loc
    )
    grant_row_before = conn.execute(
        "SELECT * FROM consents WHERE id = %s", (grant_id,)
    ).fetchone()

    revoke_id = revoke_consent(
        conn, player_id=player_id, consent_type="photo", location_id=loc
    )

    # A distinct new row represents the revocation.
    assert revoke_id != grant_id
    assert conn.execute(
        "SELECT granted FROM consents WHERE id = %s", (revoke_id,)
    ).fetchone()[0] is False

    # The original grant row is unchanged (append-only).
    grant_row_after = conn.execute(
        "SELECT * FROM consents WHERE id = %s", (grant_id,)
    ).fetchone()
    assert grant_row_after == grant_row_before

    # Both rows persist -> two rows for this player.
    total = conn.execute(
        "SELECT count(*) FROM consents WHERE player_id = %s", (player_id,)
    ).fetchone()[0]
    assert total == 2


def test_direct_update_and_delete_are_rejected_by_trigger(seeded_db):
    conn, loc, _aya, _rules = seeded_db
    player_id = _new_player(conn, loc)
    cid = append_consent(
        conn, player_id=player_id, consent_type="data_processing",
        granted=True, location_id=loc,
    )

    # The append-only trigger (migration 002) rejects UPDATE...
    with pytest.raises(Exception):
        conn.execute("UPDATE consents SET granted = false WHERE id = %s", (cid,))
    conn.rollback()

    # ...and DELETE (Req 11.3).
    with pytest.raises(Exception):
        conn.execute("DELETE FROM consents WHERE id = %s", (cid,))
    conn.rollback()

    # The row is still present and unchanged.
    assert conn.execute(
        "SELECT granted FROM consents WHERE id = %s", (cid,)
    ).fetchone()[0] is True


def test_load_consent_records_resolves_and_flags(seeded_db):
    conn, loc, aya, rules = seeded_db
    # Create a player the consent record can resolve to.
    load_clean_records(
        [_rec("p0", "players", {"first_name": "Jane", "last_name": "Doe"}, "cards/p0.png")],
        conn,
        location_id=loc,
        rules=rules,
        logged_by=aya,
    )

    records = [
        _rec("c0", "consents",
             {"player_name": "Jane Doe", "consent_type": "data_processing", "granted": True},
             "forms/c0.png"),
        _rec("c1", "consents",
             {"player_name": "Nobody Here", "consent_type": "photo", "granted": True},
             "forms/c1.png"),
    ]
    result = load_consent_records(
        records, conn, location_id=loc, captured_by_user_id=aya
    )

    assert [a.record_id for a in result.appended] == ["c0"]
    assert [(f.record_id, f.reason) for f in result.flagged] == [("c1", UNRESOLVED_PLAYER)]

    # The appended consent is present and audited.
    cid = result.appended[0].consent_id
    assert conn.execute(
        "SELECT consent_type FROM consents WHERE id = %s", (cid,)
    ).fetchone()[0] == "data_processing"
    assert conn.execute(
        "SELECT count(*) FROM sync_log WHERE entity = 'consents' AND record_id = %s",
        (cid,),
    ).fetchone()[0] == 1


def test_loader_routes_consent_records_to_ledger(seeded_db):
    conn, loc, aya, rules = seeded_db
    records = [
        _rec("p0", "players", {"first_name": "Sam", "last_name": "Lee"}, "cards/p0.png"),
        _rec("c0", "consents",
             {"player_name": "Sam Lee", "consent_type": "photo", "granted": True},
             "forms/c0.png"),
    ]
    result = load_clean_records(
        records, conn, location_id=loc, rules=rules, logged_by=aya
    )
    assert result.consents is not None
    assert len(result.consents.appended) == 1
    assert conn.execute("SELECT count(*) FROM consents").fetchone()[0] == 1
