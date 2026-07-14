"""Provider-agnostic contracts for the LLM abstraction (Req 6.1, 6.2).

Every model call in the pipeline flows through :func:`funhouse_pipeline.llm.llm_generate`,
which delegates to a provider implementing :class:`LLMProvider`. Regardless of
which provider runs, the call returns a **normalized** :class:`LLMResult` whose
shape never changes -- so the Extractor's output parser is identical for every
provider (design: LLM Abstraction Interface; Req 6.2).

Design references:
- ``task`` is a stable identifier (e.g. ``extract_records``) mapping to a
  versioned, provider-agnostic prompt template.
- ``context`` carries images/text + the business-rules system prompt.
- ``LLMResult`` is normalized: providers must return the same shape so the
  Extractor parses results identically (Req 6.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable


@dataclass(frozen=True)
class LLMResultItem:
    """One normalized model output, correlated back to its input record.

    A single ``generate`` call may cover many source records (the Bedrock Batch
    path submits a JSONL file of many records at once), so a result carries a
    sequence of items. Each item is provider-agnostic:

    Attributes:
        custom_id: Correlation id tying this output to the input record that
            produced it (provenance). Never empty.
        content: The model's text output for that record. This is what the
            Extractor parses; its meaning is identical across providers.
        stop_reason: Why generation stopped (e.g. ``end_turn``), when reported.
        raw: The provider-specific payload this item was normalized from, kept
            for debugging/audit. Callers MUST NOT depend on its shape.
    """

    custom_id: str
    content: str
    stop_reason: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMResult:
    """Normalized result returned by every provider (Req 6.2).

    The shape is intentionally provider-independent: the Extractor consumes
    ``items`` the same way whether the call was served by Bedrock Batch or the
    Anthropic API.

    Attributes:
        task: The stable task identifier the call was made for.
        provider: Name of the provider that produced the result
            (``bedrock`` | ``anthropic``).
        items: One :class:`LLMResultItem` per input record.
        model_id: The concrete model identifier used, when known.
        metadata: Provider-agnostic extras (e.g. batch job id). Advisory only.
    """

    task: str
    provider: str
    items: tuple[LLMResultItem, ...]
    model_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __iter__(self):
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)


@runtime_checkable
class LLMProvider(Protocol):
    """A pluggable model provider.

    Implementations translate the provider-agnostic ``(task, context)`` call
    into their own SDK/API and normalize the response back into an
    :class:`LLMResult`. The Extractor never imports a provider directly -- it
    only calls :func:`funhouse_pipeline.llm.llm_generate` -- so adding or
    swapping providers requires no Extractor code change (Req 6.2).
    """

    name: str

    def generate(self, task: str, context: Mapping[str, Any]) -> LLMResult:
        """Run ``task`` over ``context`` and return a normalized result."""
        ...


def extract_records_from_context(context: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    """Return the per-record work items from a call ``context``.

    The abstraction accepts a ``context`` mapping shaped as::

        {
            "system_prompt": "<business rules>",   # optional
            "model_id": "<provider model id>",     # optional
            "records": [                            # one entry per source item
                {"custom_id": "...", "text": "...", "images": [...]},
                ...
            ],
        }

    A convenience single-record form is also accepted (no ``records`` key): the
    whole context is treated as one record. This helper normalizes both forms to
    a list of record mappings so providers share identical input handling.
    """
    records = context.get("records")
    if records is None:
        # Single-record convenience form: treat the context itself as one record.
        single = {k: v for k, v in context.items() if k not in ("system_prompt", "model_id")}
        return [single]
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise TypeError("context['records'] must be a sequence of record mappings")
    return list(records)
