"""Extract stage: turn routed source material into ExtractedRecords.

Two extraction paths are provided:

- **Image path** (Task 6, Req 4): image sources routed by Collect are extracted
  through the provider-agnostic LLM abstraction (``llm_generate``) and each
  produced row is wrapped in an :class:`ExtractedRecord` envelope, then written
  as one CSV per target table.
- **Lesson ``.docx`` path** (Task 7, Req 5): ``.docx`` lesson documents routed
  by Collect are parsed as **text** (``python-docx``) with no image OCR and no
  LLM call, producing one ``lessons`` record per lesson plus one
  ``student_metrics`` record per embedded measurement.

The image path imports **only** ``llm_generate`` -- never a provider SDK -- so
swapping providers requires no Extractor change (Req 6.1, 6.2).
"""

from __future__ import annotations

from funhouse_pipeline.extract.context import (
    BusinessRules,
    PricingTier,
    build_business_rules,
    build_extraction_prompt_context,
    build_system_prompt,
)
from funhouse_pipeline.extract.csv_writer import (
    DOMAIN_COLUMNS,
    ENVELOPE_COLUMNS,
    header_for,
    write_csvs,
)
from funhouse_pipeline.extract.images import (
    EXTRACT_TASK,
    build_extraction_context,
    extract_images,
)
from funhouse_pipeline.extract.lessons import (
    ALLOWED_METRIC_TYPES,
    DETERMINISTIC_CONFIDENCE,
    DOCX_PROVIDER,
    extract_lessons,
    parse_lesson_document,
)
from funhouse_pipeline.extract.records import (
    DEFAULT_CONFIDENCE,
    MALFORMED_CONFIDENCE,
    TARGET_TABLES,
    ExtractedRecord,
    ParsedRecord,
    default_record_id,
    parse_item_content,
)

__all__ = [
    # context / system prompt (6.1)
    "BusinessRules",
    "PricingTier",
    "build_business_rules",
    "build_system_prompt",
    "build_extraction_prompt_context",
    # records / envelope + parsing (6.2)
    "ExtractedRecord",
    "ParsedRecord",
    "TARGET_TABLES",
    "DEFAULT_CONFIDENCE",
    "MALFORMED_CONFIDENCE",
    "parse_item_content",
    "default_record_id",
    # image extraction (6.2)
    "extract_images",
    "build_extraction_context",
    "EXTRACT_TASK",
    # lesson .docx text path (7.1)
    "extract_lessons",
    "parse_lesson_document",
    "ALLOWED_METRIC_TYPES",
    "DOCX_PROVIDER",
    "DETERMINISTIC_CONFIDENCE",
    # CSV writers (6.3)
    "write_csvs",
    "header_for",
    "ENVELOPE_COLUMNS",
    "DOMAIN_COLUMNS",
]
