"""Flagged-record review artifact + operator approval (Task 8.2, Req 7.8).

When validation completes, **only** flagged records are surfaced to the Operator
(Req 7.8) via a review artifact ``flagged-<run_id>.csv``. Clean records are never
written to it (design Property 19). The Operator reviews the file and can
**approve** individual flagged rows; an approved row is promoted to clean *for
that run* (Req 7.8) -- the record's flags are cleared without re-running
validation.

Approvals are supplied either directly as a set of ``record_id`` values or via an
approvals list file (a CSV with a ``record_id`` column, or a plain text file with
one id per line) so the Operator can edit a file offline. This module performs
only local file I/O -- no network, no model call.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable

from funhouse_pipeline.validate.results import (
    Partition,
    ValidationResult,
    ValidationStatus,
)

#: Column order for the review CSV. Envelope/provenance first, then reasons.
REVIEW_COLUMNS: tuple[str, ...] = (
    "record_id",
    "target_table",
    "confidence_score",
    "source_file",
    "provider",
    "extracted_at",
    "reasons",
    "payload",
)

#: Separator used to join multiple flag reasons into one CSV cell.
REASON_SEPARATOR = ";"


def _iter_flagged(results: Iterable[ValidationResult]) -> list[ValidationResult]:
    return [r for r in results if r.status is ValidationStatus.FLAGGED]


def _review_row(result: ValidationResult) -> dict[str, str]:
    record = result.record
    extracted_at = record.extracted_at
    return {
        "record_id": record.record_id,
        "target_table": record.target_table,
        "confidence_score": str(record.confidence_score),
        "source_file": record.source_file,
        "provider": record.provider,
        "extracted_at": extracted_at.isoformat()
        if hasattr(extracted_at, "isoformat")
        else str(extracted_at),
        "reasons": REASON_SEPARATOR.join(result.reasons),
        "payload": json.dumps(record.payload or {}, sort_keys=True, default=str),
    }


def write_review_artifact(
    results: Iterable[ValidationResult] | Partition,
    run_id: str,
    output_dir: str | Path,
) -> Path:
    """Write ``flagged-<run_id>.csv`` containing only flagged records (Req 7.8).

    Args:
        results: Either a :class:`Partition` or an iterable of
            :class:`ValidationResult`. Only flagged results are written; clean
            results are excluded (design Property 19).
        run_id: Identifier for the run; used in the artifact filename.
        output_dir: Directory to write into (created if absent).

    Returns:
        The path to the written review CSV.
    """
    if isinstance(results, Partition):
        flagged = list(results.flagged)
    else:
        flagged = _iter_flagged(results)

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"flagged-{run_id}.csv"

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        for result in flagged:
            writer.writerow(_review_row(result))

    return path


def read_review_record_ids(path: str | Path) -> list[str]:
    """Return the ``record_id`` values present in a review artifact (in order)."""
    ids: list[str] = []
    with Path(path).open("r", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            record_id = (row.get("record_id") or "").strip()
            if record_id:
                ids.append(record_id)
    return ids


def load_approved_ids(path: str | Path) -> set[str]:
    """Load approved ``record_id`` values from an approvals list file.

    Accepts either a CSV with a ``record_id`` header column, or a plain text
    file with one ``record_id`` per line (blank lines and a leading ``record_id``
    header line are ignored). Returns the set of approved ids.
    """
    text = Path(path).read_text(encoding="utf-8")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return set()

    # CSV form: a header row that names record_id.
    header = [h.strip() for h in lines[0].split(",")]
    if "record_id" in header:
        approved: set[str] = set()
        with Path(path).open("r", newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                value = (row.get("record_id") or "").strip()
                if value:
                    approved.add(value)
        return approved

    # Plain-text form: one id per line.
    return {line for line in lines if line.lower() != "record_id"}


def approve(
    results: Iterable[ValidationResult],
    approved_ids: Iterable[str],
) -> list[ValidationResult]:
    """Promote flagged results whose ``record_id`` is approved to clean.

    A flagged result whose record id is in ``approved_ids`` becomes a ``CLEAN``
    result (its reasons cleared) for this run (Req 7.8). All other results pass
    through unchanged, preserving order.
    """
    approved = set(approved_ids)
    promoted: list[ValidationResult] = []
    for result in results:
        if result.status is ValidationStatus.FLAGGED and result.record.record_id in approved:
            promoted.append(ValidationResult.clean(result.record))
        else:
            promoted.append(result)
    return promoted


def approve_partition(partition: Partition, approved_ids: Iterable[str]) -> Partition:
    """Return a new :class:`Partition` with approved flagged rows promoted to clean."""
    return Partition(results=tuple(approve(partition.results, approved_ids)))
