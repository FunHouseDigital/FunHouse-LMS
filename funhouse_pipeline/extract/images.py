"""Image extraction path (Task 6.2, Req 4.1/4.3/4.4).

Turns image source files routed by Collect (``HandlerTarget.IMAGE_EXTRACT``)
into :class:`~funhouse_pipeline.extract.records.ExtractedRecord`s. All model
access flows through :func:`funhouse_pipeline.llm.llm_generate` -- this module
imports **only** that entry point and never a provider SDK (Req 6.1), so the
example test for provider isolation (Task 6.7) holds.

Flow:
1. Build the business-rules system prompt (Req 4.2) unless one is supplied.
2. Build an LLM ``context`` -- one record per image (base64 payload + provenance
   ``custom_id``) plus the system prompt.
3. Call ``llm_generate("extract_records", context)``.
4. Parse each returned item's content per the documented contract and wrap every
   produced record in the envelope (confidence in [0,1], source-file provenance,
   provider, timestamp).

``llm_generate`` is injectable (``llm_generate_fn``) so tests drive the whole
path with a fake and need no network.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from funhouse_pipeline.collect import HandlerTarget, RoutedFile
from funhouse_pipeline.extract.context import (
    BusinessRules,
    build_business_rules,
    build_system_prompt,
)
from funhouse_pipeline.extract.records import (
    ExtractedRecord,
    default_record_id,
    parse_item_content,
)
from funhouse_pipeline.llm import llm_generate as _default_llm_generate

#: Stable task id mapping to the extraction prompt template (design).
EXTRACT_TASK = "extract_records"

# Media types for the base64 image blocks sent to the model.
_MEDIA_TYPES: Mapping[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".heic": "image/heic",
    ".pdf": "application/pdf",
}


def _media_type_for(suffix: str) -> str:
    return _MEDIA_TYPES.get(suffix.lower(), "application/octet-stream")


def build_extraction_context(
    image_files: Sequence[RoutedFile],
    system_prompt: str,
) -> dict[str, Any]:
    """Build the LLM ``context`` for a batch of image files.

    Each file becomes one record whose ``custom_id`` is the file path (the
    provenance carried back into every produced record) and whose image bytes
    are base64-encoded. The business-rules ``system_prompt`` is attached so the
    model extracts against known ground truth (Req 4.2).
    """
    records: list[dict[str, Any]] = []
    for routed in image_files:
        path = Path(routed.path)
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        records.append(
            {
                "custom_id": str(path),
                "images": [{"media_type": _media_type_for(path.suffix), "data": data}],
            }
        )
    return {"system_prompt": system_prompt, "records": records}


def extract_images(
    routed_files: Iterable[RoutedFile],
    *,
    known_player_names: Iterable[str] = (),
    business_rules: BusinessRules | None = None,
    system_prompt: str | None = None,
    llm_generate_fn: Callable[..., Any] = _default_llm_generate,
    provider: str | None = None,
    options: Mapping[str, Any] | None = None,
    task: str = EXTRACT_TASK,
    now: Callable[[], datetime] | None = None,
    malformed_target_table: str = "players",
    record_id_factory: Callable[[str, str, int], str] = default_record_id,
) -> list[ExtractedRecord]:
    """Extract structured records from image sources routed by Collect.

    Args:
        routed_files: Files from Collect; only those with handler
            ``IMAGE_EXTRACT`` are processed (``.docx`` lessons are Task 7).
        known_player_names: Known player names injected into the system prompt
            (Req 4.2) when ``business_rules``/``system_prompt`` are not supplied.
        business_rules / system_prompt: Optional pre-built rules/prompt; built
            from seed data + ``known_player_names`` when omitted.
        llm_generate_fn: The model entry point (defaults to the real
            ``llm_generate``); injectable so tests use a fake with no network.
        provider / options: Optional overrides forwarded to ``llm_generate``.
        task: Stable task identifier.
        now: Clock for ``extracted_at`` (injectable for deterministic tests).
        malformed_target_table: Table malformed records are routed to.
        record_id_factory: Builds a stable ``record_id`` from provenance/position.

    Returns:
        A list of :class:`ExtractedRecord`, each carrying a complete envelope.
    """
    image_files = [
        rf for rf in routed_files if rf.handler == HandlerTarget.IMAGE_EXTRACT
    ]

    if business_rules is None:
        business_rules = build_business_rules(known_player_names)
    if system_prompt is None:
        system_prompt = build_system_prompt(business_rules)

    if not image_files:
        return []

    context = build_extraction_context(image_files, system_prompt)

    call_kwargs: dict[str, Any] = {}
    if provider is not None:
        call_kwargs["provider"] = provider
    if options is not None:
        call_kwargs["options"] = options
    result = llm_generate_fn(task, context, **call_kwargs)

    clock = now or (lambda: datetime.now(timezone.utc))
    provider_name = getattr(result, "provider", None) or (provider or "unknown")

    records: list[ExtractedRecord] = []
    for item in result.items:
        # Provenance: the custom_id we set is the source file path. Guarantee a
        # non-empty source_file so the envelope is always complete (Property 8).
        source_file = item.custom_id or "<unknown-source>"
        parsed = parse_item_content(
            item.content, malformed_target_table=malformed_target_table
        )
        for index, pr in enumerate(parsed):
            records.append(
                ExtractedRecord(
                    record_id=record_id_factory(source_file, pr.target_table, index),
                    target_table=pr.target_table,
                    payload=pr.payload,
                    confidence_score=pr.confidence,
                    source_file=source_file,
                    provider=provider_name,
                    extracted_at=clock(),
                )
            )
    return records
