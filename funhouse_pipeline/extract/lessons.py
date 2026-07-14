"""Lesson ``.docx`` text-parse path (Task 7.1, Req 5.1/5.2/5.3/10.1/10.3).

Turns ``.docx`` lesson documents routed by Collect
(``HandlerTarget.DOCX_TEXT_PARSER``) into
:class:`~funhouse_pipeline.extract.records.ExtractedRecord`s **by reading the
document as text** with ``python-docx`` -- there is **no image OCR and no LLM
call of any kind** on this path (Req 5.1). This module deliberately never
imports :func:`funhouse_pipeline.llm.llm_generate` nor any provider SDK, so the
text path cannot issue an image-extraction call (design Property 9).

Because extraction here is fully deterministic (plain text parsing, not a
probabilistic model), every produced record is stamped with a deterministic
confidence of :data:`DETERMINISTIC_CONFIDENCE` (``1.0``) and provider
:data:`DOCX_PROVIDER` (``"docx-parser"``), matching the design's envelope notes
that the ``provider`` column may be ``docx-parser`` for this path.

Document conventions (how lessons and metrics are recognized)
=============================================================
The parser reads the document's paragraphs and tables **in document order** and
applies a small, explicit set of marker rules. A *marker* is a paragraph whose
trimmed text begins with ``LABEL:`` (case-insensitive) for one of the labels
below; the text after the colon is the marker's value.

Lesson delimiting rule (Req 5.2, 10.1)
--------------------------------------
- ``LESSON:`` starts a **new lesson**; the text after the colon is that lesson's
  ``title``. A document therefore contains exactly as many lessons as it has
  ``LESSON:`` markers.
- **Default (one file = one lesson):** a document with **no** ``LESSON:`` marker
  is treated as a single lesson whose ``title`` defaults to the file stem. This
  guarantees at least one ``lessons`` record per file.
- Any content appearing *before* the first ``LESSON:`` marker in a multi-lesson
  document is preamble and is not counted as a lesson.

Lesson tagging (Req 10.3)
-------------------------
Within a lesson block:
- ``TOPIC:``      -> sets the lesson ``topic`` tag.
- ``PHENOMENON:`` -> sets the lesson ``phenomenon`` tag.
- ``TITLE:``      -> overrides the lesson ``title`` (optional; ``LESSON:`` text
  is the default title).
- Every other non-marker paragraph is appended to the lesson ``content``.

Embedded measurement rule (Req 5.3)
-----------------------------------
Learning measurements are recognized two ways, and each recognized measurement
becomes one ``student_metrics`` record attached to the enclosing lesson:

1. **Labelled ``METRIC:`` line** -- ``METRIC: <player> | <metric_type> | <value>
   [| <measured_at>]`` (pipe-separated). ``<metric_type>`` is normalized (see
   below).
2. **Metric table** (e.g. a TypingBird WPM/accuracy table) -- a table whose
   header row's first cell is a person label (``student``/``player``/``name``/
   ``learner``) and whose remaining header cells name a recognized metric. For
   each data row and each recognized metric column with a non-empty cell, a
   ``student_metrics`` record is emitted with the row's first cell as the player
   name.

Metric-type normalization (guarantees Req 5.3 / Property 11)
------------------------------------------------------------
A raw metric label is normalized to one of the allowed values
``typing_wpm``, ``typing_accuracy``, ``homework_done``, ``quiz_score``,
``observation`` by: (a) exact match after lower-casing and mapping spaces/hyphens
to underscores, else (b) keyword match (``wpm``/``words per minute`` ->
``typing_wpm``; ``accuracy`` -> ``typing_accuracy``; ``homework`` ->
``homework_done``; ``quiz`` -> ``quiz_score``; ``observation``/``notes`` ->
``observation``). A label that matches nothing is **not emitted** -- so every
produced ``student_metrics`` record is guaranteed to carry an allowed
``metric_type``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from funhouse_pipeline.collect import HandlerTarget, RoutedFile
from funhouse_pipeline.extract.records import (
    ExtractedRecord,
    default_record_id,
)

#: Provider tag for records produced by the deterministic text parser.
DOCX_PROVIDER = "docx-parser"

#: Deterministic confidence for text-parsed records (not a probabilistic model).
DETERMINISTIC_CONFIDENCE = 1.0

#: The allowed ``student_metrics.metric_type`` values (Req 5.3, design schema).
ALLOWED_METRIC_TYPES: tuple[str, ...] = (
    "typing_wpm",
    "typing_accuracy",
    "homework_done",
    "quiz_score",
    "observation",
)
_ALLOWED_METRIC_SET = frozenset(ALLOWED_METRIC_TYPES)

# Marker labels (compared case-insensitively against a paragraph's leading text).
_LESSON_LABEL = "lesson"
_TITLE_LABEL = "title"
_TOPIC_LABEL = "topic"
_PHENOMENON_LABEL = "phenomenon"
_METRIC_LABEL = "metric"

# Person-column labels that identify the first column of a metric table.
_PERSON_HEADERS = frozenset({"student", "player", "name", "learner", "pupil"})


def _normalize_metric_type(raw: str | None) -> str | None:
    """Normalize a raw metric label to an allowed value, or ``None``.

    See the module docstring for the full rule. Returning ``None`` means the
    label is unrecognized and no record should be produced for it -- this is what
    guarantees every emitted metric carries an allowed ``metric_type``.
    """
    if raw is None:
        return None
    text = " ".join(str(raw).strip().lower().split())
    if not text:
        return None

    # (a) exact match after mapping spaces/hyphens to underscores.
    canonical = text.replace("-", "_").replace(" ", "_")
    if canonical in _ALLOWED_METRIC_SET:
        return canonical

    # (b) keyword match.
    if "wpm" in text or "words per minute" in text:
        return "typing_wpm"
    if "accuracy" in text:
        return "typing_accuracy"
    if "homework" in text:
        return "homework_done"
    if "quiz" in text:
        return "quiz_score"
    if "observation" in text or "notes" in text:
        return "observation"
    return None


def _split_marker(text: str) -> tuple[str, str] | None:
    """Return ``(label, value)`` if ``text`` is a ``LABEL: value`` marker."""
    if ":" not in text:
        return None
    label, _, value = text.partition(":")
    label = label.strip().lower()
    if not label or " " in label:
        # Only single-word labels are markers (avoids matching prose sentences).
        return None
    return label, value.strip()


def _iter_block_items(document: Any) -> Iterator[Any]:
    """Yield paragraphs and tables of a python-docx document in document order.

    python-docx exposes ``paragraphs`` and ``tables`` separately, losing their
    interleaving. We walk the body's XML children so a metric table is attributed
    to the lesson block it physically sits in.
    """
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    body = document.element.body
    for child in body.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, document)
        elif isinstance(child, CT_Tbl):
            yield Table(child, document)


class _LessonBuilder:
    """Accumulates one lesson's fields and its embedded metrics."""

    __slots__ = ("title", "topic", "phenomenon", "_content_lines", "metrics")

    def __init__(self, title: str) -> None:
        self.title = title
        self.topic: str | None = None
        self.phenomenon: str | None = None
        self._content_lines: list[str] = []
        # Each metric: (player_name, metric_type, value, measured_at).
        self.metrics: list[tuple[str, str, str, str | None]] = []

    def add_content(self, line: str) -> None:
        self._content_lines.append(line)

    def content(self) -> str:
        return "\n".join(self._content_lines).strip()


def _parse_metric_line(value: str) -> tuple[str, str, str, str | None] | None:
    """Parse a ``METRIC:`` line body ``player | type | value [| measured_at]``."""
    parts = [p.strip() for p in value.split("|")]
    if len(parts) < 3:
        return None
    player, raw_type, metric_value = parts[0], parts[1], parts[2]
    measured_at = parts[3] if len(parts) >= 4 and parts[3] else None
    metric_type = _normalize_metric_type(raw_type)
    if metric_type is None or not player or metric_value == "":
        return None
    return player, metric_type, metric_value, measured_at


def _cell_text(cell: Any) -> str:
    return " ".join(cell.text.split()).strip()


def _extract_table_metrics(table: Any) -> list[tuple[str, str, str, str | None]]:
    """Extract ``(player, metric_type, value, None)`` tuples from a metric table.

    Returns an empty list when the table is not a recognized metric table (its
    header's first cell is not a person label, or no column names a metric).
    """
    rows = list(table.rows)
    if len(rows) < 2:
        return []

    header = [_cell_text(c) for c in rows[0].cells]
    if not header or header[0].strip().lower() not in _PERSON_HEADERS:
        return []

    # Map each metric column index -> normalized metric_type.
    metric_columns: dict[int, str] = {}
    for idx in range(1, len(header)):
        metric_type = _normalize_metric_type(header[idx])
        if metric_type is not None:
            metric_columns[idx] = metric_type
    if not metric_columns:
        return []

    metrics: list[tuple[str, str, str, str | None]] = []
    for row in rows[1:]:
        cells = [_cell_text(c) for c in row.cells]
        if not cells:
            continue
        player = cells[0]
        if not player:
            continue
        for idx, metric_type in metric_columns.items():
            if idx >= len(cells):
                continue
            value = cells[idx]
            if value == "":
                continue
            metrics.append((player, metric_type, value, None))
    return metrics


def parse_lesson_document(
    path: str | Path,
    *,
    document_loader: Callable[[str], Any] | None = None,
) -> tuple[list[_LessonBuilder], str]:
    """Parse a single ``.docx`` file into lesson builders (no LLM/OCR).

    Args:
        path: Path to the ``.docx`` lesson file.
        document_loader: Optional injectable loader returning a python-docx
            ``Document`` (defaults to ``docx.Document``); handy for tests.

    Returns:
        ``(lessons, source_file)`` where ``lessons`` is the ordered list of
        parsed lesson builders (at least one; see the default one-file-one-lesson
        rule) and ``source_file`` is the string path used for provenance.
    """
    source_file = str(path)

    if document_loader is None:
        from docx import Document as _Document

        document = _Document(source_file)
    else:
        document = document_loader(source_file)

    default_title = Path(source_file).stem
    lessons: list[_LessonBuilder] = []
    # Preamble collector (content before the first LESSON: marker in a
    # multi-lesson doc, or the whole doc when there is no LESSON: marker).
    preamble = _LessonBuilder(default_title)
    current: _LessonBuilder = preamble
    seen_lesson_marker = False

    for block in _iter_block_items(document):
        if hasattr(block, "rows"):  # a Table
            for metric in _extract_table_metrics(block):
                current.metrics.append(metric)
            continue

        text = block.text.strip()
        if not text:
            continue

        marker = _split_marker(text)
        if marker is None:
            current.add_content(text)
            continue

        label, value = marker
        if label == _LESSON_LABEL:
            seen_lesson_marker = True
            current = _LessonBuilder(value or default_title)
            lessons.append(current)
        elif label == _TITLE_LABEL:
            if value:
                current.title = value
        elif label == _TOPIC_LABEL:
            current.topic = value or None
        elif label == _PHENOMENON_LABEL:
            current.phenomenon = value or None
        elif label == _METRIC_LABEL:
            parsed = _parse_metric_line(value)
            if parsed is not None:
                current.metrics.append(parsed)
        else:
            # An unrecognized ``word:`` prefix is treated as ordinary content.
            current.add_content(text)

    if not seen_lesson_marker:
        # Default rule: one file == one lesson (Req 5.2). The preamble IS the
        # single lesson, even when the document was empty.
        lessons = [preamble]

    return lessons, source_file


def _lesson_records_for_file(
    path: str | Path,
    *,
    now: Callable[[], datetime],
    record_id_factory: Callable[[str, str, int], str],
    document_loader: Callable[[str], Any] | None,
) -> list[ExtractedRecord]:
    lessons, source_file = parse_lesson_document(path, document_loader=document_loader)

    records: list[ExtractedRecord] = []
    lesson_index = 0
    metric_index = 0
    for lesson in lessons:
        # One lessons record per lesson (Req 5.2, 10.1), tagged with topic and
        # phenomenon (Req 10.3).
        records.append(
            ExtractedRecord(
                record_id=record_id_factory(source_file, "lessons", lesson_index),
                target_table="lessons",
                payload={
                    "title": lesson.title,
                    "topic": lesson.topic,
                    "phenomenon": lesson.phenomenon,
                    "content": lesson.content(),
                    "source_file": source_file,
                },
                confidence_score=DETERMINISTIC_CONFIDENCE,
                source_file=source_file,
                provider=DOCX_PROVIDER,
                extracted_at=now(),
            )
        )
        lesson_index += 1

        # One student_metrics record per embedded measurement (Req 5.3). Every
        # metric_type here is guaranteed to be in the allowed set.
        for player, metric_type, value, measured_at in lesson.metrics:
            records.append(
                ExtractedRecord(
                    record_id=record_id_factory(
                        source_file, "student_metrics", metric_index
                    ),
                    target_table="student_metrics",
                    payload={
                        "player_name": player,
                        "lesson_title": lesson.title,
                        "metric_type": metric_type,
                        "value": value,
                        "measured_at": measured_at,
                    },
                    confidence_score=DETERMINISTIC_CONFIDENCE,
                    source_file=source_file,
                    provider=DOCX_PROVIDER,
                    extracted_at=now(),
                )
            )
            metric_index += 1

    return records


def extract_lessons(
    routed_files: Iterable[RoutedFile],
    *,
    now: Callable[[], datetime] | None = None,
    record_id_factory: Callable[[str, str, int], str] = default_record_id,
    document_loader: Callable[[str], Any] | None = None,
) -> list[ExtractedRecord]:
    """Extract lesson + metric records from ``.docx`` sources (text only).

    Consumes files routed by Collect and processes only those with handler
    :data:`~funhouse_pipeline.collect.HandlerTarget.DOCX_TEXT_PARSER`; image
    sources are left to the image path. Parsing is deterministic and issues no
    LLM or OCR call (Req 5.1).

    Args:
        routed_files: Files from Collect. Only ``DOCX_TEXT_PARSER`` files are
            processed. Plain ``str``/``Path`` items are also accepted as direct
            ``.docx`` paths for convenience.
        now: Clock for ``extracted_at`` (injectable for deterministic tests).
        record_id_factory: Builds a stable ``record_id`` from provenance/position.
        document_loader: Optional injectable python-docx ``Document`` loader.

    Returns:
        A list of :class:`ExtractedRecord`: one ``lessons`` record per lesson,
        plus one ``student_metrics`` record per recognized embedded measurement,
        each wrapped in the envelope with ``provider="docx-parser"``.
    """
    clock = now or (lambda: datetime.now(timezone.utc))

    paths: list[str | Path] = []
    for item in routed_files:
        if isinstance(item, RoutedFile):
            if item.handler == HandlerTarget.DOCX_TEXT_PARSER:
                paths.append(item.path)
        else:
            # Direct path (str/Path): accept only .docx.
            candidate = Path(item)
            if candidate.suffix.lower() == ".docx":
                paths.append(candidate)

    records: list[ExtractedRecord] = []
    for path in paths:
        records.extend(
            _lesson_records_for_file(
                path,
                now=clock,
                record_id_factory=record_id_factory,
                document_loader=document_loader,
            )
        )
    return records
