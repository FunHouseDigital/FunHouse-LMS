"""Unit tests for the deterministic Entitlement_Engine helpers (Task 6, Req 8, 9).

These cover the pure, DB-free derivation logic: the units convention
(hours -> integer minutes), the deterministic weekly period boundary, the
validity-window derivation, and the recurring reset decision. They run
everywhere (no PostgreSQL needed); the DB-backed behaviour is covered by
``test_entitlements_properties.py``.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from funhouse_api.entitlements import engine


def _dt(y: int, m: int, d: int, hour: int = 12) -> datetime:
    return datetime(y, m, d, hour, tzinfo=timezone.utc)


# --- units convention (hours -> minutes) ----------------------------------- #


def test_allowance_units_hours_per_week_to_minutes():
    # Holiday Special: 3 hrs/week -> 180 minute-units.
    assert engine.allowance_units({"hours_per_week": 3}) == 180
    # Subscription: 2 hrs/week -> 120 minute-units.
    assert engine.allowance_units({"hours_per_week": 2}) == 120


def test_allowance_units_hours_and_counts_and_unlimited():
    assert engine.allowance_units({"hours": 1}) == 60
    assert engine.allowance_units({"units": 5}) == 5
    assert engine.allowance_units({"sessions": 10}) == 10
    # No unit rule -> unlimited (None).
    assert engine.allowance_units({"members": 4}) is None
    assert engine.allowance_units({}) is None


def test_is_recurring():
    assert engine.is_recurring({"hours_per_week": 3, "reset": "sunday"}) is True
    assert engine.is_recurring({"reset": "monday"}) is True
    assert engine.is_recurring({"hours_per_week": 2}) is True
    assert engine.is_recurring({"units": 5}) is False
    assert engine.is_recurring({}) is False


# --- deterministic weekly period boundary ---------------------------------- #


def test_period_start_most_recent_sunday():
    # 2024-01-03 is a Wednesday; the most recent Sunday is 2023-12-31.
    start = engine.period_start({"reset": "sunday"}, _dt(2024, 1, 3))
    assert start == date(2023, 12, 31)
    assert start.weekday() == 6  # Sunday


def test_period_start_on_the_reset_day_returns_that_day():
    # 2023-12-31 is itself a Sunday -> period start is the same day.
    start = engine.period_start({"reset": "sunday"}, _dt(2023, 12, 31))
    assert start == date(2023, 12, 31)


def test_period_start_defaults_to_monday_when_no_reset_named():
    # hours_per_week with no reset -> Monday anchor. 2024-01-03 (Wed) -> Mon 01-01.
    start = engine.period_start({"hours_per_week": 2}, _dt(2024, 1, 3))
    assert start == date(2024, 1, 1)
    assert start.weekday() == 0


def test_period_start_is_deterministic():
    a = engine.period_start({"reset": "sunday"}, _dt(2024, 6, 12))
    b = engine.period_start({"reset": "sunday"}, _dt(2024, 6, 12))
    assert a == b


# --- validity window derivation -------------------------------------------- #


def test_derive_window_holiday_special_fixed_weekly():
    rules = {"hours_per_week": 3, "reset": "sunday", "rollover": False, "fixed_window": True}
    w = engine.derive_window(rules, _dt(2024, 1, 3))
    assert w.remaining_units == 180
    assert w.recurring is True
    assert w.valid_from == date(2023, 12, 31)
    # fixed weekly window: one week from the period start.
    assert w.valid_to == date(2024, 1, 7)


def test_derive_window_subscription_min_term_months():
    rules = {"members": 4, "hours_per_week": 2, "min_term_months": 3}
    w = engine.derive_window(rules, _dt(2024, 1, 3))
    assert w.remaining_units == 120
    assert w.recurring is True
    # weekly allowance with no reset -> Monday anchor.
    assert w.valid_from == date(2024, 1, 1)
    # 3-month term from the period start.
    assert w.valid_to == date(2024, 4, 1)


def test_derive_window_non_recurring_units():
    rules = {"units": 5}
    w = engine.derive_window(rules, _dt(2024, 1, 3))
    assert w.remaining_units == 5
    assert w.recurring is False
    assert w.valid_from == date(2024, 1, 3)
    assert w.valid_to is None


def test_add_months_clamps_day():
    # Jan 31 + 1 month -> Feb 29 (2024 is a leap year).
    assert engine._add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    # Crossing a year boundary.
    assert engine._add_months(date(2024, 12, 15), 2) == date(2025, 2, 15)


# --- recurring reset decision (Req 9) -------------------------------------- #


def test_reset_if_new_period_resets_with_no_rollover():
    rules = {"hours_per_week": 3, "reset": "sunday"}
    # Stored period starts 2023-12-31 with only 10 units left; now is a week later.
    ent = {"remaining_units": 10, "valid_from": date(2023, 12, 31)}
    outcome = engine.reset_if_new_period(ent, rules, _dt(2024, 1, 8))
    assert outcome.changed is True
    # Reset to the full per-period allowance; prior 10 units discarded (no rollover).
    assert outcome.remaining_units == 180
    assert outcome.valid_from == date(2024, 1, 7)  # the Sunday of the new week


def test_reset_if_new_period_no_change_within_same_period():
    rules = {"hours_per_week": 3, "reset": "sunday"}
    ent = {"remaining_units": 50, "valid_from": date(2023, 12, 31)}
    # Still within the same week (Wed of that week).
    outcome = engine.reset_if_new_period(ent, rules, _dt(2024, 1, 3))
    assert outcome.changed is False
    assert outcome.remaining_units == 50


def test_reset_if_new_period_non_recurring_never_resets():
    rules = {"units": 5}
    ent = {"remaining_units": 1, "valid_from": date(2020, 1, 1)}
    outcome = engine.reset_if_new_period(ent, rules, _dt(2024, 1, 3))
    assert outcome.changed is False
    assert outcome.remaining_units == 1
