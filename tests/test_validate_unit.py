"""Unit / example tests for the Validate stage (Task 8.1, 8.2).

Concrete, readable scenarios complementing the property tests: each flag rule in
isolation, reason accumulation, the clean/flagged partition, the cold-start
empty-known-names behavior, the documented amount-matching rule, and the review
artifact + operator approval path. All pure/local work -- no network, no model.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from funhouse_pipeline.extract import ExtractedRecord, build_business_rules
from funhouse_pipeline.validate import (
    AMOUNT_NO_TIER,
    IMPOSSIBLE_DATE,
    LOW_CONFIDENCE,
    UNKNOWN_NAME,
    ValidationStatus,
    approve,
    approve_partition,
    load_approved_ids,
    partition,
    read_review_record_ids,
    validate,
    write_review_artifact,
)

_NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)
_REF = date(2024, 1, 1)
_KNOWN = ("Thabo Mokoena", "Naledi Dlamini", "Sipho Ndlovu")
_RULES = build_business_rules(known_player_names=_KNOWN)


def _rec(table, payload, *, conf=0.95, rid="r1"):
    return ExtractedRecord(rid, table, payload, conf, "src.png", "bedrock", _NOW)


def _validate(record):
    return validate(record, _RULES, threshold=0.7, reference_date=_REF)


# --------------------------------------------------------------------------- #
# LOW_CONFIDENCE (Req 7.2)
# --------------------------------------------------------------------------- #


def test_low_confidence_is_flagged():
    r = _validate(_rec("players", {"first_name": "Thabo", "last_name": "Mokoena"}, conf=0.4))
    assert r.status is ValidationStatus.FLAGGED
    assert LOW_CONFIDENCE in r.reasons


def test_confidence_at_threshold_is_not_low():
    r = _validate(_rec("players", {"first_name": "Thabo", "last_name": "Mokoena"}, conf=0.7))
    assert LOW_CONFIDENCE not in r.reasons


# --------------------------------------------------------------------------- #
# IMPOSSIBLE_DATE (Req 7.3)
# --------------------------------------------------------------------------- #


def test_invalid_calendar_date_flagged():
    r = _validate(_rec("players", {"first_name": "Thabo", "last_name": "Mokoena", "birth_date": "2024-13-40"}))
    assert IMPOSSIBLE_DATE in r.reasons


def test_future_date_flagged():
    r = _validate(_rec("sessions", {"player_name": "Thabo Mokoena", "started_at": "2099-01-01"}))
    assert IMPOSSIBLE_DATE in r.reasons


def test_birth_date_too_young_flagged():
    # Born 2023 relative to 2024-01-01 reference => age < 3.
    r = _validate(_rec("players", {"first_name": "Thabo", "last_name": "Mokoena", "birth_date": "2023-06-01"}))
    assert IMPOSSIBLE_DATE in r.reasons


def test_birth_date_too_old_flagged():
    r = _validate(_rec("players", {"first_name": "Thabo", "last_name": "Mokoena", "birth_date": "1900-01-01"}))
    assert IMPOSSIBLE_DATE in r.reasons


def test_plausible_birth_date_is_clean():
    r = _validate(_rec("players", {"first_name": "Thabo", "last_name": "Mokoena", "birth_date": "2014-06-01"}))
    assert r.status is ValidationStatus.CLEAN


def test_absent_date_is_not_flagged():
    r = _validate(_rec("sessions", {"player_name": "Thabo Mokoena", "started_at": ""}))
    assert IMPOSSIBLE_DATE not in r.reasons


# --------------------------------------------------------------------------- #
# UNKNOWN_NAME (Req 7.4)
# --------------------------------------------------------------------------- #


def test_unknown_name_flagged():
    r = _validate(_rec("sessions", {"player_name": "Nobody Here", "session_type": "lounge"}))
    assert UNKNOWN_NAME in r.reasons


def test_known_name_case_and_whitespace_insensitive():
    r = _validate(_rec("sessions", {"player_name": "  thabo   MOKOENA "}))
    assert UNKNOWN_NAME not in r.reasons


def test_players_name_from_first_and_last():
    r = _validate(_rec("players", {"first_name": "Naledi", "last_name": "Dlamini"}))
    assert UNKNOWN_NAME not in r.reasons


def test_cold_start_empty_known_names_disables_name_check():
    rules_empty = build_business_rules()  # no known player names
    r = validate(
        _rec("sessions", {"player_name": "Anyone At All"}),
        rules_empty,
        threshold=0.7,
        reference_date=_REF,
    )
    assert UNKNOWN_NAME not in r.reasons
    assert r.status is ValidationStatus.CLEAN


def test_cold_start_strict_opt_in_flags_unknown():
    rules_empty = build_business_rules()
    r = validate(
        _rec("sessions", {"player_name": "Anyone At All"}),
        rules_empty,
        threshold=0.7,
        reference_date=_REF,
        flag_unknown_when_empty=True,
    )
    assert UNKNOWN_NAME in r.reasons


def test_lessons_never_name_checked():
    r = _validate(_rec("lessons", {"title": "Fractions", "topic": "Numbers"}))
    assert r.status is ValidationStatus.CLEAN


# --------------------------------------------------------------------------- #
# AMOUNT_NO_TIER (Req 7.5) -- documented matching rule
# --------------------------------------------------------------------------- #


def test_amount_rand_prefixed_matches_tier():
    for amount in ("R10", "R30", "R50", "R350", "R30.00"):
        r = _validate(_rec("payments", {"player_name": "Thabo Mokoena", "amount": amount}))
        assert AMOUNT_NO_TIER not in r.reasons, amount


def test_amount_bare_number_matches_as_rand_or_cents():
    # "30" (rands) and 3000 (cents) both resolve to the R30 tier.
    for amount in ("30", 30, 3000, "3000", 50, "350"):
        r = _validate(_rec("payments", {"player_name": "Thabo Mokoena", "amount": amount}))
        assert AMOUNT_NO_TIER not in r.reasons, amount


def test_amount_no_tier_flagged():
    for amount in ("R99", "77", 12345, "not-a-number", ""):
        r = _validate(_rec("payments", {"player_name": "Thabo Mokoena", "amount": amount}))
        assert AMOUNT_NO_TIER in r.reasons, amount


def test_payment_missing_amount_flagged():
    r = _validate(_rec("payments", {"player_name": "Thabo Mokoena"}))
    assert AMOUNT_NO_TIER in r.reasons


def test_amount_rule_only_applies_to_payments():
    # A non-payment record with an odd amount value is not amount-checked.
    r = _validate(_rec("sessions", {"player_name": "Thabo Mokoena", "amount": "R99"}))
    assert AMOUNT_NO_TIER not in r.reasons


# --------------------------------------------------------------------------- #
# Reason accumulation + partition (Req 7.6, 7.7)
# --------------------------------------------------------------------------- #


def test_multiple_reasons_accumulate():
    r = _validate(
        _rec(
            "payments",
            {"player_name": "Ghost Person", "amount": "R99", "paid_at": "2099-01-01"},
            conf=0.1,
        )
    )
    assert r.status is ValidationStatus.FLAGGED
    assert set(r.reasons) == {LOW_CONFIDENCE, IMPOSSIBLE_DATE, UNKNOWN_NAME, AMOUNT_NO_TIER}


def test_partition_splits_clean_and_flagged():
    records = [
        _rec("players", {"first_name": "Thabo", "last_name": "Mokoena"}, rid="clean1"),
        _rec("sessions", {"player_name": "Nobody"}, rid="flag1"),
    ]
    part = partition(records, _RULES, threshold=0.7, reference_date=_REF)
    assert [r.record.record_id for r in part.clean] == ["clean1"]
    assert [r.record.record_id for r in part.flagged] == ["flag1"]
    assert len(part.clean) + len(part.flagged) == len(records)


# --------------------------------------------------------------------------- #
# Review artifact + operator approval (Req 7.8)
# --------------------------------------------------------------------------- #


def test_review_artifact_contains_only_flagged(tmp_path):
    records = [
        _rec("players", {"first_name": "Thabo", "last_name": "Mokoena"}, rid="clean1"),
        _rec("sessions", {"player_name": "Nobody"}, rid="flag1"),
        _rec("payments", {"player_name": "Thabo Mokoena", "amount": "R99"}, rid="flag2"),
    ]
    part = partition(records, _RULES, threshold=0.7, reference_date=_REF)
    path = write_review_artifact(part, "run-123", tmp_path)

    assert path.name == "flagged-run-123.csv"
    ids = read_review_record_ids(path)
    assert set(ids) == {"flag1", "flag2"}
    assert "clean1" not in ids


def test_operator_approval_promotes_flagged_to_clean():
    records = [
        _rec("sessions", {"player_name": "Nobody"}, rid="flag1"),
        _rec("payments", {"player_name": "Thabo Mokoena", "amount": "R99"}, rid="flag2"),
    ]
    part = partition(records, _RULES, threshold=0.7, reference_date=_REF)
    assert len(part.flagged) == 2

    promoted = approve_partition(part, {"flag1"})
    assert {r.record.record_id for r in promoted.clean} == {"flag1"}
    assert {r.record.record_id for r in promoted.flagged} == {"flag2"}


def test_load_approved_ids_from_csv(tmp_path):
    csv_path = tmp_path / "approvals.csv"
    csv_path.write_text("record_id,note\nflag1,looks fine\nflag2,verified\n", encoding="utf-8")
    assert load_approved_ids(csv_path) == {"flag1", "flag2"}


def test_load_approved_ids_from_plain_text(tmp_path):
    txt_path = tmp_path / "approvals.txt"
    txt_path.write_text("flag1\nflag2\n\n", encoding="utf-8")
    assert load_approved_ids(txt_path) == {"flag1", "flag2"}


def test_approve_leaves_unlisted_flags_untouched():
    records = [_rec("sessions", {"player_name": "Nobody"}, rid="flag1")]
    part = partition(records, _RULES, threshold=0.7, reference_date=_REF)
    promoted = approve(part.results, set())
    assert promoted[0].status is ValidationStatus.FLAGGED


# --------------------------------------------------------------------------- #
# Determinism / offline (Req 7.1, 15.3)
# --------------------------------------------------------------------------- #


def test_validate_is_deterministic():
    rec = _rec("payments", {"player_name": "Ghost", "amount": "R99", "paid_at": "bad"}, conf=0.3)
    assert _validate(rec) == _validate(rec)


def test_validate_package_imports_no_llm_or_sdk():
    from funhouse_pipeline.validate import validator as validator_mod

    forbidden = {"boto3", "anthropic", "llm_generate", "BedrockBatchProvider", "AnthropicProvider"}
    leaked = forbidden.intersection(vars(validator_mod))
    assert not leaked, f"Validator must not reference an LLM/SDK; found {leaked}"
