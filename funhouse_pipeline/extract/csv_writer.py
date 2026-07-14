"""CSV writers for the five Extract target tables (Task 6.3, Req 4.5).

Writes one CSV per target table -- ``players``, ``sessions``, ``payments``,
``lessons``, ``student_metrics`` -- into an output directory. Every row carries
the extracted-record **envelope** columns (``record_id``, ``confidence_score``,
``source_file``, ``extracted_at``, ``provider``) followed by that table's domain
columns, per the design's "CSV intermediate schemas" section.

All five CSVs are **always** produced, even when a table has no records (an
empty CSV with just the header), so downstream stages have a stable set of
inputs (Req 4.5).
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from funhouse_pipeline.extract.records import TARGET_TABLES, ExtractedRecord

#: Envelope columns present in every CSV (design: CSV intermediate schemas).
ENVELOPE_COLUMNS: tuple[str, ...] = (
    "record_id",
    "confidence_score",
    "source_file",
    "extracted_at",
    "provider",
)

#: Domain columns per target table (design: CSV intermediate schemas).
DOMAIN_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "players": (
        "first_name",
        "last_name",
        "birth_date",
        "grade",
        "school_name",
        "guardian_name",
        "photo_consent",
    ),
    "sessions": (
        "player_name",
        "session_type",
        "started_at",
        "ended_at",
        "duration_minutes",
        "school_name",
    ),
    "payments": (
        "player_name",
        "product_name",
        "amount",
        "method",
        "paid_at",
    ),
    "lessons": (
        "title",
        "topic",
        "phenomenon",
        "content",
        "source_file",
    ),
    "student_metrics": (
        "player_name",
        "lesson_title",
        "metric_type",
        "value",
        "measured_at",
    ),
}


def header_for(table: str) -> list[str]:
    """Return the ordered CSV header for ``table``.

    Envelope columns come first; domain columns follow with any that duplicate
    an envelope column removed (e.g. ``lessons`` lists ``source_file`` which is
    already an envelope column).
    """
    domain = [c for c in DOMAIN_COLUMNS[table] if c not in ENVELOPE_COLUMNS]
    return list(ENVELOPE_COLUMNS) + domain


def _cell(value: Any) -> Any:
    """Render a payload value for CSV output (None -> empty string)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def _row_for(record: ExtractedRecord) -> dict[str, Any]:
    extracted_at = record.extracted_at
    row: dict[str, Any] = {
        "record_id": record.record_id,
        "confidence_score": record.confidence_score,
        "source_file": record.source_file,
        "extracted_at": extracted_at.isoformat()
        if isinstance(extracted_at, datetime)
        else extracted_at,
        "provider": record.provider,
    }
    payload = record.payload or {}
    for col in DOMAIN_COLUMNS[record.target_table]:
        if col in ENVELOPE_COLUMNS:
            # Domain column shadows an envelope column (lessons.source_file):
            # prefer an explicit payload value when present.
            if col in payload:
                row[col] = _cell(payload[col])
            continue
        row[col] = _cell(payload.get(col))
    return row


def write_csvs(
    records: Iterable[ExtractedRecord],
    output_dir: str | Path,
) -> dict[str, Path]:
    """Write the five target-table CSVs into ``output_dir``.

    Records are grouped by ``target_table``; each of the five CSVs is written
    (empty CSVs still get a header). Records whose ``target_table`` is not one of
    the five are ignored defensively (the parser normalizes malformed output to a
    valid table, so this should not occur in practice).

    Args:
        records: The extracted records to write.
        output_dir: Directory to write the CSVs into (created if absent).

    Returns:
        Mapping of table name to the written CSV path.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    grouped: dict[str, list[ExtractedRecord]] = {t: [] for t in TARGET_TABLES}
    for record in records:
        if record.target_table in grouped:
            grouped[record.target_table].append(record)

    paths: dict[str, Path] = {}
    for table in TARGET_TABLES:
        path = out / f"{table}.csv"
        header = header_for(table)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=header, extrasaction="ignore")
            writer.writeheader()
            for record in grouped[table]:
                writer.writerow(_row_for(record))
        paths[table] = path

    return paths
