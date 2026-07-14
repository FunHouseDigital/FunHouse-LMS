"""Validate stage: deterministic, LLM-free classification of records (Req 7, 15.3).

The Validator (Task 8) is a **pure function** over an
:class:`~funhouse_pipeline.extract.records.ExtractedRecord` and the injected
:class:`~funhouse_pipeline.extract.context.BusinessRules`. It issues **no LLM
call and no network I/O**: it classifies each record as exactly ``CLEAN`` or
``FLAGGED``, accumulating a reason for every rule violated
(``LOW_CONFIDENCE``, ``IMPOSSIBLE_DATE``, ``UNKNOWN_NAME``, ``AMOUNT_NO_TIER``).

Only flagged records are surfaced to the Operator through a review artifact
(``flagged-<run_id>.csv``); the Operator can approve flagged rows to promote them
to clean for the run.

This package imports no provider/AWS SDK and no ``llm_generate`` -- validation is
offline by construction (Req 15.3).
"""

from __future__ import annotations

from funhouse_pipeline.validate.results import (
    ALL_REASONS,
    AMOUNT_NO_TIER,
    IMPOSSIBLE_DATE,
    LOW_CONFIDENCE,
    UNKNOWN_NAME,
    Partition,
    ValidationResult,
    ValidationStatus,
)
from funhouse_pipeline.validate.review import (
    REASON_SEPARATOR,
    REVIEW_COLUMNS,
    approve,
    approve_partition,
    load_approved_ids,
    read_review_record_ids,
    write_review_artifact,
)
from funhouse_pipeline.validate.validator import (
    DATE_FIELDS,
    MAX_AGE,
    MIN_AGE,
    MIN_PLAUSIBLE_YEAR,
    normalize_name,
    partition,
    validate,
)

__all__ = [
    # results / model
    "ValidationStatus",
    "ValidationResult",
    "Partition",
    "LOW_CONFIDENCE",
    "IMPOSSIBLE_DATE",
    "UNKNOWN_NAME",
    "AMOUNT_NO_TIER",
    "ALL_REASONS",
    # validator
    "validate",
    "partition",
    "normalize_name",
    "DATE_FIELDS",
    "MIN_PLAUSIBLE_YEAR",
    "MIN_AGE",
    "MAX_AGE",
    # review artifact + approval
    "write_review_artifact",
    "read_review_record_ids",
    "load_approved_ids",
    "approve",
    "approve_partition",
    "REVIEW_COLUMNS",
    "REASON_SEPARATOR",
]
