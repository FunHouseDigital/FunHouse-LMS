"""Validation result model and the clean/flagged partition (Task 8.1, Req 7.6/7.7).

The Validator classifies every :class:`~funhouse_pipeline.extract.records.ExtractedRecord`
as **exactly one** of ``CLEAN`` or ``FLAGGED`` (design Property 18). A flagged
record carries a non-empty, ordered list of *reasons* -- one per rule it
violated (Req 7.6). A clean record carries no reasons and, by definition,
violated no rule (Req 7.7).

Nothing in this module performs I/O or calls a model -- it is the pure data
layer shared by the validator and the review artifact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

from funhouse_pipeline.extract.records import ExtractedRecord

# --------------------------------------------------------------------------- #
# Flag reasons (Req 7.2-7.5). Stable string codes so they survive CSV round-trips.
# --------------------------------------------------------------------------- #

#: Confidence below the configured threshold (Req 7.2).
LOW_CONFIDENCE = "LOW_CONFIDENCE"
#: A date field that is not a real calendar date or is out of plausible range (Req 7.3).
IMPOSSIBLE_DATE = "IMPOSSIBLE_DATE"
#: A person name that matches no known player after normalization (Req 7.4).
UNKNOWN_NAME = "UNKNOWN_NAME"
#: A payment amount matching no Pricing_Tier and no product price (Req 7.5).
AMOUNT_NO_TIER = "AMOUNT_NO_TIER"

#: All reasons, in the deterministic order the Validator records them.
ALL_REASONS: tuple[str, ...] = (
    LOW_CONFIDENCE,
    IMPOSSIBLE_DATE,
    UNKNOWN_NAME,
    AMOUNT_NO_TIER,
)


class ValidationStatus(str, Enum):
    """The two mutually-exclusive outcomes for a record."""

    CLEAN = "CLEAN"
    FLAGGED = "FLAGGED"


@dataclass(frozen=True)
class ValidationResult:
    """The outcome of validating a single record.

    Invariants (design Property 18):
      * ``status is CLEAN``   iff ``reasons == ()``.
      * ``status is FLAGGED`` iff ``len(reasons) >= 1``.
    """

    record: ExtractedRecord
    status: ValidationStatus
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status is ValidationStatus.CLEAN and self.reasons:
            raise ValueError("A CLEAN result must have no reasons.")
        if self.status is ValidationStatus.FLAGGED and not self.reasons:
            raise ValueError("A FLAGGED result must carry at least one reason.")

    @property
    def is_clean(self) -> bool:
        return self.status is ValidationStatus.CLEAN

    @property
    def is_flagged(self) -> bool:
        return self.status is ValidationStatus.FLAGGED

    @classmethod
    def clean(cls, record: ExtractedRecord) -> "ValidationResult":
        return cls(record=record, status=ValidationStatus.CLEAN, reasons=())

    @classmethod
    def flagged(cls, record: ExtractedRecord, reasons: Iterable[str]) -> "ValidationResult":
        return cls(
            record=record,
            status=ValidationStatus.FLAGGED,
            reasons=tuple(reasons),
        )


@dataclass(frozen=True)
class Partition:
    """A validated batch split into clean and flagged results.

    Holds the results in input order and exposes the two subsets. Together the
    subsets are a *total partition* of the batch: every input result is in
    exactly one of :meth:`clean` / :meth:`flagged` (design Property 18).
    """

    results: tuple[ValidationResult, ...] = field(default_factory=tuple)

    @property
    def clean(self) -> tuple[ValidationResult, ...]:
        return tuple(r for r in self.results if r.is_clean)

    @property
    def flagged(self) -> tuple[ValidationResult, ...]:
        return tuple(r for r in self.results if r.is_flagged)

    def summary(self) -> str:
        return (
            f"Validation complete: {len(self.clean)} clean, "
            f"{len(self.flagged)} flagged (of {len(self.results)} records)."
        )
