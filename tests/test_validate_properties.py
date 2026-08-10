"""Property-based tests for the Validate stage (Tasks 8.3-8.9).

Implements design Properties 13-19. The Validator is a pure, deterministic,
LLM-free function, so these properties exercise it across generated records with
no network and no model call. Each property runs a minimum of 100 Hypothesis
iterations, per the design's Testing Strategy.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest import mock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from funhouse_pipeline.extract import (
    TARGET_TABLES,
    ExtractedRecord,
    build_business_rules,
)
from funhouse_pipeline.validate import (
    AMOUNT_NO_TIER,
    IMPOSSIBLE_DATE,
    LOW_CONFIDENCE,
    UNKNOWN_NAME,
    ValidationStatus,
    normalize_name,
    partition,
    read_review_record_ids,
    validate,
    write_review_artifact,
)

pytestmark = pytest.mark.property

_SETTINGS = settings(max_examples=100, deadline=None)

# Fixed environment so results are fully reproducible across iterations.
_NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)
_REF = date(2024, 1, 1)
_THRESHOLD = 0.7

_KNOWN_NAMES = ("Thabo Mokoena", "Naledi Dlamini", "Sipho Ndlovu", "Aya Nkosi")
_KNOWN_NORM = frozenset(normalize_name(n) for n in _KNOWN_NAMES)
_RULES = build_business_rules(known_player_names=_KNOWN_NAMES)

# Known valid amounts: R10/R30/R50/R250/R350 in both Rand and cent forms.
_MATCHING_AMOUNTS = {10, 30, 50, 250, 350, 1000, 3000, 5000, 25000, 35000}

# --------------------------------------------------------------------------- #
# Building-block strategies
# --------------------------------------------------------------------------- #

_confidence = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)

_letters = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ "
_random_name = (
    st.text(alphabet=_letters, min_size=1, max_size=15)
    .map(lambda s: " ".join(s.split()))
    .filter(lambda s: len(s) >= 1)
)
_unknown_name = _random_name.filter(lambda n: normalize_name(n) not in _KNOWN_NORM)

# Dates that are invalid, in the future, or before 1900 -> impossible for ANY date field.
_generic_impossible_date = st.one_of(
    st.sampled_from(
        ["2024-13-01", "2024-02-30", "2024-00-10", "2024-01-32", "not-a-date", "2024/01/01", "13-13-13"]
    ),
    st.dates(min_value=date(2024, 1, 2), max_value=date(3000, 1, 1)).map(lambda d: d.isoformat()),
    st.dates(min_value=date(1, 1, 1), max_value=date(1899, 12, 31)).map(lambda d: d.isoformat()),
)

# A grab-bag of date values (valid, absent, or impossible) for general records.
_any_date_value = st.one_of(
    st.none(),
    st.dates(min_value=date(2005, 1, 1), max_value=date(2018, 1, 1)).map(lambda d: d.isoformat()),
    _generic_impossible_date,
)

_any_amount = st.one_of(
    st.none(),
    st.sampled_from(
        ["R10", "R30", "R50", "R250", "R350", "30", "50", "250", 3000, 5000, 25000, 35000]
    ),
    st.sampled_from(["R99", "77", 12345, "not-a-number", ""]),
)

_any_name = st.one_of(st.sampled_from(_KNOWN_NAMES), _random_name)


@st.composite
def _payload_for(draw, table):
    payload = {}
    if table == "players":
        full = draw(_any_name)
        parts = full.split()
        payload["first_name"] = parts[0] if parts else full
        payload["last_name"] = " ".join(parts[1:])
        if draw(st.booleans()):
            payload["birth_date"] = draw(_any_date_value)
    elif table in ("sessions", "payments", "student_metrics"):
        payload["player_name"] = draw(_any_name)
        if table == "sessions" and draw(st.booleans()):
            payload["started_at"] = draw(_any_date_value)
        if table == "payments":
            payload["amount"] = draw(_any_amount)
        if table == "student_metrics" and draw(st.booleans()):
            payload["measured_at"] = draw(_any_date_value)
    else:  # lessons
        payload["title"] = draw(_random_name)
    return payload


@st.composite
def _record(draw, rid=None):
    record_id = rid if rid is not None else draw(st.uuids().map(str))
    table = draw(st.sampled_from(TARGET_TABLES))
    payload = draw(_payload_for(table))
    return ExtractedRecord(
        record_id=record_id,
        target_table=table,
        payload=payload,
        confidence_score=draw(_confidence),
        source_file=draw(st.sampled_from(["cards/a.png", "sheets/b.jpg", "photos/c.png"])),
        provider="bedrock",
        extracted_at=_NOW,
    )


@st.composite
def _record_batch(draw):
    ids = draw(st.lists(st.uuids().map(str), min_size=0, max_size=8, unique=True))
    return [draw(_record(rid=i)) for i in ids]


def _validate(record):
    return validate(record, _RULES, threshold=_THRESHOLD, reference_date=_REF)


# --------------------------------------------------------------------------- #
# Property 13 (Task 8.3)
# --------------------------------------------------------------------------- #

# Feature: phase0-data-foundation, Property 13: Validation is deterministic and
# LLM-free. For any extracted record, repeated calls to the Validator produce
# identical results, and validation issues no large-language-model call.
# Validates: Requirements 7.1
@_SETTINGS
@given(record=_record())
def test_property_13_validation_is_deterministic_and_llm_free(record):
    with mock.patch("funhouse_pipeline.llm.llm_generate") as llm_spy:
        first = _validate(record)
        second = _validate(record)

    assert llm_spy.call_count == 0
    assert first == second
    assert first.status == second.status
    assert first.reasons == second.reasons


# --------------------------------------------------------------------------- #
# Property 14 (Task 8.4)
# --------------------------------------------------------------------------- #

# Feature: phase0-data-foundation, Property 14: Low-confidence records are
# flagged. For any record whose confidence_score is below the configured
# threshold, the Validator marks it a Flagged_Record with reason LOW_CONFIDENCE.
# Validates: Requirements 7.2
@_SETTINGS
@given(
    record=_record(),
    low_conf=st.floats(min_value=0.0, max_value=_THRESHOLD, allow_nan=False, allow_infinity=False).filter(
        lambda x: x < _THRESHOLD
    ),
)
def test_property_14_low_confidence_records_flagged(record, low_conf):
    record = ExtractedRecord(
        record.record_id,
        record.target_table,
        record.payload,
        low_conf,
        record.source_file,
        record.provider,
        record.extracted_at,
    )
    result = _validate(record)
    assert result.status is ValidationStatus.FLAGGED
    assert LOW_CONFIDENCE in result.reasons


# --------------------------------------------------------------------------- #
# Property 15 (Task 8.5)
# --------------------------------------------------------------------------- #


@st.composite
def _impossible_date_record(draw):
    kind = draw(st.integers(min_value=0, max_value=1))
    if kind == 0:
        # Birth-date-based impossibility on players (too young, too old, or generic).
        value = draw(
            st.one_of(
                st.dates(min_value=date(2022, 1, 1), max_value=date(2023, 12, 31)).map(
                    lambda d: d.isoformat()
                ),  # age < 3 vs 2024-01-01
                st.dates(min_value=date(1900, 1, 1), max_value=date(1920, 1, 1)).map(
                    lambda d: d.isoformat()
                ),  # age > 100
                _generic_impossible_date,
            )
        )
        payload = {"first_name": "Thabo", "last_name": "Mokoena", "birth_date": value}
        table = "players"
    else:
        value = draw(_generic_impossible_date)
        payload = {"player_name": "Thabo Mokoena", "session_type": "lounge", "started_at": value}
        table = "sessions"
    return ExtractedRecord(
        draw(st.uuids().map(str)), table, payload, draw(_confidence), "cards/a.png", "bedrock", _NOW
    )


# Feature: phase0-data-foundation, Property 15: Impossible dates are flagged. For
# any record containing an impossible date, the Validator marks it a
# Flagged_Record with reason IMPOSSIBLE_DATE.
# Validates: Requirements 7.3
@_SETTINGS
@given(record=_impossible_date_record())
def test_property_15_impossible_dates_flagged(record):
    result = _validate(record)
    assert result.status is ValidationStatus.FLAGGED
    assert IMPOSSIBLE_DATE in result.reasons


# --------------------------------------------------------------------------- #
# Property 16 (Task 8.6)
# --------------------------------------------------------------------------- #


@st.composite
def _unknown_name_record(draw):
    name = draw(_unknown_name)
    table = draw(st.sampled_from(["players", "sessions", "payments", "student_metrics"]))
    if table == "players":
        parts = name.split()
        payload = {"first_name": parts[0] if parts else name, "last_name": " ".join(parts[1:])}
    else:
        payload = {"player_name": name}
    return ExtractedRecord(
        draw(st.uuids().map(str)), table, payload, 1.0, "cards/a.png", "bedrock", _NOW
    )


# Feature: phase0-data-foundation, Property 16: Unknown names are flagged. For any
# record whose person name matches no known player name after normalization, the
# Validator marks it a Flagged_Record with reason UNKNOWN_NAME.
# Validates: Requirements 7.4
@_SETTINGS
@given(record=_unknown_name_record())
def test_property_16_unknown_names_flagged(record):
    result = _validate(record)
    assert result.status is ValidationStatus.FLAGGED
    assert UNKNOWN_NAME in result.reasons


# --------------------------------------------------------------------------- #
# Property 17 (Task 8.7)
# --------------------------------------------------------------------------- #


@st.composite
def _bad_amount_record(draw):
    amount = draw(st.integers(min_value=1, max_value=10_000_000).filter(lambda x: x not in _MATCHING_AMOUNTS))
    representation = draw(st.sampled_from([str(amount), f"R{amount}", amount]))
    payload = {"player_name": "Thabo Mokoena", "amount": representation}
    return ExtractedRecord(
        draw(st.uuids().map(str)), "payments", payload, 1.0, "sheets/b.jpg", "bedrock", _NOW
    )


# Feature: phase0-data-foundation, Property 17: Payments with no matching tier or
# product are flagged. For any payment record whose amount equals no Pricing_Tier
# and no product price, the Validator marks it a Flagged_Record with reason
# AMOUNT_NO_TIER.
# Validates: Requirements 7.5
@_SETTINGS
@given(record=_bad_amount_record())
def test_property_17_bad_amount_flagged(record):
    result = _validate(record)
    assert result.status is ValidationStatus.FLAGGED
    assert AMOUNT_NO_TIER in result.reasons


# --------------------------------------------------------------------------- #
# Property 18 (Task 8.8)
# --------------------------------------------------------------------------- #

# Feature: phase0-data-foundation, Property 18: Clean/flagged is a total partition
# with recorded reasons. For any extracted record, the Validator classifies it as
# exactly one of Clean_Record or Flagged_Record: it is Clean if and only if it
# violates no rule, and every Flagged_Record carries a non-empty list of reasons.
# Validates: Requirements 7.6, 7.7
@_SETTINGS
@given(records=_record_batch())
def test_property_18_clean_flagged_total_partition(records):
    part = partition(records, _RULES, threshold=_THRESHOLD, reference_date=_REF)

    # Every result is exactly one of clean/flagged; the two subsets partition the batch.
    assert len(part.clean) + len(part.flagged) == len(records)
    clean_ids = [r.record.record_id for r in part.clean]
    flagged_ids = [r.record.record_id for r in part.flagged]
    assert set(clean_ids).isdisjoint(flagged_ids)

    for result in part.results:
        if result.status is ValidationStatus.CLEAN:
            assert result.reasons == ()
        else:
            assert result.status is ValidationStatus.FLAGGED
            assert len(result.reasons) >= 1


# --------------------------------------------------------------------------- #
# Property 19 (Task 8.9)
# --------------------------------------------------------------------------- #

# Feature: phase0-data-foundation, Property 19: Only flagged records are surfaced
# for review. For any set of validated records, the Operator review artifact
# contains exactly the flagged subset and no clean record.
# Validates: Requirements 7.8
@_SETTINGS
@given(records=_record_batch())
def test_property_19_only_flagged_surfaced_for_review(records, tmp_path_factory):
    part = partition(records, _RULES, threshold=_THRESHOLD, reference_date=_REF)
    out_dir = tmp_path_factory.mktemp("review")
    path = write_review_artifact(part, "run-xyz", out_dir)

    surfaced = set(read_review_record_ids(path))
    flagged_ids = {r.record.record_id for r in part.flagged}
    clean_ids = {r.record.record_id for r in part.clean}

    assert surfaced == flagged_ids
    assert surfaced.isdisjoint(clean_ids)
