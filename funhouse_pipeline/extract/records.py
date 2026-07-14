"""Extracted-record envelope and model-output parsing (Task 6.2, Req 4.1/4.3/4.4).

Every structured row the Extractor produces is wrapped in an
:class:`ExtractedRecord` envelope carrying:

- ``record_id``       -- stable id (idempotency + review)
- ``target_table``    -- one of players|sessions|payments|lessons|student_metrics
- ``payload``         -- the domain columns for that table
- ``confidence_score``-- extraction certainty in the closed interval [0, 1] (Req 4.3)
- ``source_file``     -- provenance: the originating source file (Req 4.4)
- ``provider``        -- LLM provider that produced it (``bedrock``/``anthropic``)
- ``extracted_at``    -- timestamp

Model-output parsing contract
-----------------------------
The model is prompted (see :mod:`funhouse_pipeline.extract.context`) to return,
per input item, a JSON document tagging each record with its target table::

    {"records": [
        {"target_table": "players",  "confidence": 0.9, "payload": {...}},
        {"target_table": "payments", "confidence": 0.8, "payload": {...}}
    ]}

The parser is deliberately forgiving so real-world model output does not silently
drop a source file:

- A bare JSON array, or a single record object, is accepted as well as the
  ``{"records": [...]}`` form.
- Domain columns may be nested under ``payload`` or provided inline alongside
  ``target_table``/``confidence``.
- Markdown code fences (```json ... ```) are stripped.

Confidence handling (documented defaults)
-----------------------------------------
- A valid numeric confidence is clamped into ``[0, 1]`` (guarantees Property 8).
- A record with **no** confidence uses :data:`DEFAULT_CONFIDENCE` (a neutral
  0.5) -- present but uncertain, left for the Validator's threshold to judge.
- **Malformed** output -- unparseable JSON, a non-record shape, or a record whose
  ``target_table`` is missing/invalid -- yields a record with confidence
  :data:`MALFORMED_CONFIDENCE` (``0.0``). Per the design's error handling this is
  emitted (not dropped) so the Validator flags it, and it is routed to
  ``malformed_target_table`` (default ``players``) since no valid table is known.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

#: The five target tables Extract writes (design: CSV intermediate schemas).
TARGET_TABLES: tuple[str, ...] = (
    "players",
    "sessions",
    "payments",
    "lessons",
    "student_metrics",
)

#: Confidence assigned when a record parses but carries no confidence value.
DEFAULT_CONFIDENCE = 0.5
#: Confidence assigned to malformed / unparseable model output (flagged later).
MALFORMED_CONFIDENCE = 0.0


@dataclass
class ExtractedRecord:
    """A single structured row produced by the Extractor, with its envelope."""

    record_id: str
    target_table: str
    payload: dict
    confidence_score: float
    source_file: str
    provider: str
    extracted_at: datetime


@dataclass(frozen=True)
class ParsedRecord:
    """Intermediate parse result before the envelope is attached."""

    target_table: str
    confidence: float
    payload: dict = field(default_factory=dict)
    malformed: bool = False


def _clamp(value: float) -> float:
    """Clamp a confidence value into the closed interval [0, 1] (Property 8)."""
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _coerce_confidence(raw: Any) -> float | None:
    """Return a float confidence, or ``None`` when absent/non-numeric.

    Non-finite values (``NaN``, ``inf``, ``-inf`` -- e.g. a literal ``"NAN"`` in
    model output) are rejected as ``None`` so they never leak into the envelope;
    the caller then falls back to :data:`DEFAULT_CONFIDENCE`, guaranteeing a
    finite score in ``[0, 1]`` (Property 8).
    """
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return value


def _strip_code_fences(text: str) -> str:
    """Strip a leading/trailing markdown code fence if present."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    # Drop the opening fence line (``` or ```json) and any closing fence.
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _malformed(payload: Any, malformed_target_table: str) -> ParsedRecord:
    body: dict[str, Any]
    if isinstance(payload, Mapping):
        body = dict(payload)
    else:
        body = {"_raw": payload}
    body.setdefault("_malformed", True)
    return ParsedRecord(
        target_table=malformed_target_table,
        confidence=MALFORMED_CONFIDENCE,
        payload=body,
        malformed=True,
    )


def _coerce_to_record_list(data: Any) -> list | None:
    """Normalize a parsed JSON document into a list of candidate records.

    Returns ``None`` when the shape is not record-like at all.
    """
    if isinstance(data, list):
        return data
    if isinstance(data, Mapping):
        if "records" in data:
            records = data["records"]
            return list(records) if isinstance(records, list) else None
        # A single-record object (has a target_table, or is a plain payload map).
        return [data]
    return None


def _normalize_record(raw: Any, malformed_target_table: str) -> ParsedRecord:
    """Normalize one candidate record into a :class:`ParsedRecord`."""
    if not isinstance(raw, Mapping):
        return _malformed(raw, malformed_target_table)

    target = raw.get("target_table")
    if target not in TARGET_TABLES:
        # Unknown/missing target: keep the content but flag it (conf 0.0).
        payload = raw.get("payload")
        if not isinstance(payload, Mapping):
            payload = {
                k: v for k, v in raw.items() if k not in ("target_table", "confidence")
            }
        return _malformed(payload, malformed_target_table)

    payload = raw.get("payload")
    if not isinstance(payload, Mapping):
        payload = {
            k: v
            for k, v in raw.items()
            if k not in ("target_table", "confidence", "confidence_score", "payload")
        }

    confidence = _coerce_confidence(raw.get("confidence", raw.get("confidence_score")))
    if confidence is None:
        confidence = DEFAULT_CONFIDENCE
    confidence = _clamp(confidence)

    return ParsedRecord(
        target_table=target,
        confidence=confidence,
        payload=dict(payload),
        malformed=False,
    )


def parse_item_content(
    content: str,
    *,
    malformed_target_table: str = "players",
) -> list[ParsedRecord]:
    """Parse one model item's text into a list of :class:`ParsedRecord`.

    Follows the documented model-output parsing contract (see module docstring).
    An empty/unparseable/non-record payload yields a single malformed record so
    the source is never silently dropped. A well-formed but empty
    ``{"records": []}`` yields an empty list (nothing was extractable).
    """
    text = "" if content is None else str(content).strip()
    if not text:
        return [_malformed(content, malformed_target_table)]

    text = _strip_code_fences(text)
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return [_malformed(content, malformed_target_table)]

    raw_records = _coerce_to_record_list(data)
    if raw_records is None:
        return [_malformed(data, malformed_target_table)]

    return [_normalize_record(raw, malformed_target_table) for raw in raw_records]


def default_record_id(source_file: str, target_table: str, index: int) -> str:
    """Deterministic, stable record id from provenance + position.

    Stable across runs for the same ``(source_file, target_table, index)`` so
    the same source produces the same ids -- supporting idempotency and review.
    """
    digest = hashlib.sha1(
        f"{source_file}|{target_table}|{index}".encode("utf-8")
    ).hexdigest()
    return digest[:16]
