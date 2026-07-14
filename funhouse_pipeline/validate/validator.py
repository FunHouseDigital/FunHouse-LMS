"""Deterministic, LLM-free record validation (Task 8.1, Req 7.1-7.7, 15.3).

The Validator is a **pure function** over an
:class:`~funhouse_pipeline.extract.records.ExtractedRecord` and the injected
:class:`~funhouse_pipeline.extract.context.BusinessRules`. It issues **no LLM
call and no network I/O** (Req 7.1, 15.3) -- this module deliberately never
imports :func:`funhouse_pipeline.llm.llm_generate` nor any provider/AWS SDK. The
same inputs always produce the same result (design Property 13).

Each record accumulates **all** applicable flag reasons (Req 7.6) and is
classified as exactly ``CLEAN`` (no reason) or ``FLAGGED`` (>=1 reason)
(Req 7.7, Property 18).

Rules
=====

``LOW_CONFIDENCE`` (Req 7.2)
    ``confidence_score < threshold`` (threshold from config, default 0.7).

``IMPOSSIBLE_DATE`` (Req 7.3)
    Any *date field for the record's target table* that is not a real calendar
    date, or is out of a plausible range. The date fields checked per table are:

    ===================  ============================================
    target_table         date field(s)
    ===================  ============================================
    ``players``          ``birth_date``
    ``sessions``         ``started_at``, ``ended_at``
    ``payments``         ``paid_at``
    ``student_metrics``  ``measured_at``
    ``lessons``          (none -- lessons carry no date column)
    ===================  ============================================

    A field is flagged when: it does not parse as an ISO calendar date/datetime
    (e.g. ``2024-13-40``); OR it is in the future relative to the reference date;
    OR its year predates :data:`MIN_PLAUSIBLE_YEAR`. For ``birth_date``
    additionally: the implied age is below :data:`MIN_AGE` or above
    :data:`MAX_AGE` (Req 7.3 example). An absent/empty date field is not a
    violation. The reference "today" is injectable (``reference_date``); it
    defaults to :func:`datetime.date.today`, which is the only environmental
    input -- pass an explicit ``reference_date`` for a fully reproducible result.

``UNKNOWN_NAME`` (Req 7.4)
    The record's person name matches no known player name after normalization
    (lower-cased, trimmed, internal whitespace collapsed). The name is taken from
    ``first_name``+``last_name`` for ``players`` and from ``player_name`` for
    ``sessions``/``payments``/``student_metrics``; ``lessons`` carry no person
    name and are never name-checked.

    **Cold-start default:** when ``known_player_names`` is empty the name check is
    **disabled** (records are not flagged for an unknown name). Players are not
    seeded, so on a first run there is no ground truth to match against; flagging
    every record would defeat review. This matches the design's note that an
    empty known-name set is allowed on a cold start. Set
    ``flag_unknown_when_empty=True`` to opt into strict flagging instead.

``AMOUNT_NO_TIER`` (Req 7.5)
    A ``payments`` record whose ``amount`` matches no ``Pricing_Tier`` and no
    product price. Matching rule (documented): known amounts are the **positive**
    product prices in cents (e.g. R10->1000, R30->3000, R50->5000,
    R350->35000). An input amount is normalized to candidate cent values:

      * ``"R30"`` / ``"R30.00"`` -> Rand -> ``3000`` cents (the ``R`` prefix
        fixes the unit).
      * a bare number (``"30"``, ``30``, ``30.0``) is ambiguous, so BOTH
        interpretations are tried -- as Rands (``*100``) and as cents -- and it
        matches if *either* equals a known amount. Thus ``30`` and ``R30`` and
        ``3000`` all match the R30 tier.

    An unparseable/empty amount matches nothing and is flagged.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable, Mapping

from funhouse_pipeline.config.settings import DEFAULT_CONFIDENCE_THRESHOLD
from funhouse_pipeline.extract.context import BusinessRules
from funhouse_pipeline.extract.records import ExtractedRecord
from funhouse_pipeline.validate.results import (
    AMOUNT_NO_TIER,
    IMPOSSIBLE_DATE,
    LOW_CONFIDENCE,
    UNKNOWN_NAME,
    Partition,
    ValidationResult,
)

# --------------------------------------------------------------------------- #
# Plausibility constants (documented ranges for IMPOSSIBLE_DATE).
# --------------------------------------------------------------------------- #

#: Dates whose year is before this are considered implausible for this dataset.
MIN_PLAUSIBLE_YEAR = 1900
#: A birth_date implying an age below this many years is implausible (Req 7.3).
MIN_AGE = 3
#: A birth_date implying an age above this many years is implausible (Req 7.3).
MAX_AGE = 100

#: Date fields carried by each target table's payload (see module docstring).
DATE_FIELDS: Mapping[str, tuple[str, ...]] = {
    "players": ("birth_date",),
    "sessions": ("started_at", "ended_at"),
    "payments": ("paid_at",),
    "student_metrics": ("measured_at",),
    "lessons": (),
}

#: Which date fields are birth dates (subject to the age plausibility window).
_BIRTH_DATE_FIELDS = frozenset({"birth_date"})


# --------------------------------------------------------------------------- #
# Normalization helpers
# --------------------------------------------------------------------------- #


def normalize_name(name: Any) -> str:
    """Lower-case, trim, and collapse internal whitespace for name matching."""
    return " ".join(str(name).strip().lower().split()) if name is not None else ""


def _candidate_name(record: ExtractedRecord) -> str | None:
    """Return the person name referenced by ``record``, or ``None`` if it has none."""
    payload = record.payload or {}
    if record.target_table == "players":
        first = payload.get("first_name") or ""
        last = payload.get("last_name") or ""
        combined = f"{first} {last}".strip()
        return combined or None
    if "player_name" in payload:
        value = payload.get("player_name")
        text = "" if value is None else str(value).strip()
        return text or None
    return None


# --------------------------------------------------------------------------- #
# Date rule
# --------------------------------------------------------------------------- #


def _to_date(value: Any) -> date | None:
    """Parse ``value`` into a :class:`date`.

    Returns ``None`` when the field is absent/empty (nothing to validate) and
    raises :class:`ValueError` when a present value is not a real calendar date.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value).strip()
    if not text:
        return None

    iso = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(iso).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"unparseable date: {value!r}") from exc


def _age_years(birth: date, reference: date) -> int:
    """Whole years from ``birth`` to ``reference`` (calendar-correct)."""
    had_birthday = (reference.month, reference.day) >= (birth.month, birth.day)
    return reference.year - birth.year - (0 if had_birthday else 1)


def _date_is_impossible(value: Any, *, is_birth: bool, reference: date) -> bool:
    """True when a present date value is invalid or out of the plausible range."""
    try:
        parsed = _to_date(value)
    except ValueError:
        return True  # not a real calendar date
    if parsed is None:
        return False  # absent -> not a violation
    if parsed > reference:
        return True  # future date
    if parsed.year < MIN_PLAUSIBLE_YEAR:
        return True
    if is_birth:
        age = _age_years(parsed, reference)
        if age < MIN_AGE or age > MAX_AGE:
            return True
    return False


def _has_impossible_date(record: ExtractedRecord, *, reference: date) -> bool:
    payload = record.payload or {}
    for field_name in DATE_FIELDS.get(record.target_table, ()):
        if field_name not in payload:
            continue
        if _date_is_impossible(
            payload.get(field_name),
            is_birth=field_name in _BIRTH_DATE_FIELDS,
            reference=reference,
        ):
            return True
    return False


# --------------------------------------------------------------------------- #
# Amount rule
# --------------------------------------------------------------------------- #


def _known_amount_cents(rules: BusinessRules) -> frozenset[int]:
    """The set of valid amounts (in cents) from products + pricing tiers.

    Only **positive** prices count as valid tiers (a placeholder 0-cent product
    such as the unpriced Holiday Special is not a matchable amount).
    """
    cents: set[int] = set()
    for product in rules.products:
        if product.price_cents > 0:
            cents.add(int(product.price_cents))
    for tier in rules.pricing_tiers:
        if tier.price_cents > 0:
            cents.add(int(tier.price_cents))
    return frozenset(cents)


def _amount_candidates_cents(value: Any) -> tuple[int, ...]:
    """Candidate cent values for a raw amount (see the module docstring rule)."""
    if value is None or isinstance(value, bool):
        return ()

    if isinstance(value, (int, float)):
        return _numeric_candidates(float(value))

    text = str(value).strip()
    if not text:
        return ()

    lowered = text.lower()
    if lowered.startswith("r"):
        remainder = lowered[1:].strip().replace(",", "")
        try:
            rand = float(remainder)
        except ValueError:
            return ()
        return (round(rand * 100),)

    try:
        number = float(text.replace(",", ""))
    except ValueError:
        return ()
    return _numeric_candidates(number)


def _numeric_candidates(number: float) -> tuple[int, ...]:
    """Both interpretations of a bare number: as Rands (*100) and as cents."""
    candidates: set[int] = {round(number * 100)}
    if float(number).is_integer():
        candidates.add(int(number))
    return tuple(sorted(candidates))


def _amount_has_no_tier(record: ExtractedRecord, known_cents: frozenset[int]) -> bool:
    payload = record.payload or {}
    if "amount" not in payload:
        # A payment with no amount at all cannot match any tier.
        return True
    candidates = _amount_candidates_cents(payload.get("amount"))
    if not candidates:
        return True
    return not any(c in known_cents for c in candidates)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def validate(
    record: ExtractedRecord,
    rules: BusinessRules,
    *,
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    reference_date: date | None = None,
    flag_unknown_when_empty: bool = False,
) -> ValidationResult:
    """Validate one record deterministically, accumulating all flag reasons.

    Args:
        record: The extracted record to classify.
        rules: Injected business rules (known player names, product prices).
        threshold: Confidence threshold; records below it are ``LOW_CONFIDENCE``.
        reference_date: "Today" for date plausibility; defaults to
            :func:`date.today`. Pass an explicit value for a reproducible result.
        flag_unknown_when_empty: When ``True``, still apply the unknown-name rule
            even if ``known_player_names`` is empty. Defaults to ``False`` (the
            cold-start default: no name check when there is no ground truth).

    Returns:
        A :class:`ValidationResult` -- ``CLEAN`` with no reasons, or ``FLAGGED``
        with the ordered list of every rule it violated (Req 7.6, 7.7).
    """
    reference = reference_date or date.today()
    reasons: list[str] = []

    # Req 7.2 -- low confidence.
    if record.confidence_score < threshold:
        reasons.append(LOW_CONFIDENCE)

    # Req 7.3 -- impossible date in any of the table's date fields.
    if _has_impossible_date(record, reference=reference):
        reasons.append(IMPOSSIBLE_DATE)

    # Req 7.4 -- unknown person name (after normalization).
    known = frozenset(normalize_name(n) for n in rules.known_player_names)
    if known or flag_unknown_when_empty:
        candidate = _candidate_name(record)
        if candidate is not None and normalize_name(candidate) not in known:
            reasons.append(UNKNOWN_NAME)

    # Req 7.5 -- payment amount matching no tier/product price.
    if record.target_table == "payments":
        if _amount_has_no_tier(record, _known_amount_cents(rules)):
            reasons.append(AMOUNT_NO_TIER)

    if reasons:
        return ValidationResult.flagged(record, reasons)
    return ValidationResult.clean(record)


def partition(
    records: Iterable[ExtractedRecord],
    rules: BusinessRules,
    *,
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    reference_date: date | None = None,
    flag_unknown_when_empty: bool = False,
) -> Partition:
    """Validate a batch and return the total clean/flagged :class:`Partition`.

    Preserves input order. Every input record appears in exactly one of the
    partition's clean/flagged subsets (design Property 18).
    """
    results = tuple(
        validate(
            record,
            rules,
            threshold=threshold,
            reference_date=reference_date,
            flag_unknown_when_empty=flag_unknown_when_empty,
        )
        for record in records
    )
    return Partition(results=results)
