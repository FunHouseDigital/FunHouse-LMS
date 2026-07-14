"""Property-based test for transparent provider selection (Task 4.2).

Implements design Property 12. Uses injected fake providers via a fresh
:class:`ProviderRegistry`, so the property runs with no network access and no
live AWS calls. Runs a minimum of 100 Hypothesis iterations, per the design's
Testing Strategy.
"""

from __future__ import annotations

from dataclasses import fields

from hypothesis import given, settings
from hypothesis import strategies as st

import pytest

from funhouse_pipeline.llm import (
    LLMResult,
    LLMResultItem,
    ProviderRegistry,
    llm_generate,
)

pytestmark = pytest.mark.property

_SETTINGS = settings(max_examples=100, deadline=None)

# The exact set of fields that make up the normalized result shape. The whole
# point of Property 12 is that this shape does not vary by provider.
_RESULT_FIELDS = {f.name for f in fields(LLMResult)}
_ITEM_FIELDS = {f.name for f in fields(LLMResultItem)}


class _RecordingFakeProvider:
    """A fake provider that records the call it received and returns a
    normalized LLMResult of the standard shape -- identical for any provider."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[tuple[str, dict]] = []

    def generate(self, task, context):
        self.calls.append((task, dict(context)))
        records = context.get("records") or [context]
        items = tuple(
            LLMResultItem(
                custom_id=str(rec.get("custom_id", index)),
                content=f"{self.name}:{task}:{index}",
                stop_reason="end_turn",
            )
            for index, rec in enumerate(records)
        )
        return LLMResult(task=task, provider=self.name, items=items, model_id="fake-model")


# Provider names to route to. Includes the two real names plus arbitrary values
# to prove routing is by-name and not hard-coded to a known list.
_provider_names = st.one_of(
    st.sampled_from(["bedrock", "anthropic"]),
    st.text(alphabet="abcdefghijklmnopqrstuvwxyz-_", min_size=1, max_size=16),
)
_record_lists = st.lists(
    st.fixed_dictionaries({"custom_id": st.text(min_size=1, max_size=8)}),
    min_size=1,
    max_size=6,
)


# Feature: phase0-data-foundation, Property 12: Provider selection is
# transparent to the Extractor. For any configured provider value, llm_generate
# routes the call to that provider and returns a normalized LLMResult of the
# same shape, requiring no change to Extractor code.
# Validates: Requirements 6.2
@_SETTINGS
@given(name=_provider_names, task=st.text(min_size=1, max_size=20), records=_record_lists)
def test_property_12_provider_selection_is_transparent(name, task, records):
    # Register a fresh fake for the (possibly arbitrary) configured provider name.
    registry = ProviderRegistry()
    fake = _RecordingFakeProvider(name)
    registry.register(name, lambda _options, _fake=fake: _fake)

    context = {"system_prompt": "business rules", "records": records}

    # Caller code is identical regardless of which provider is configured: the
    # provider is chosen purely from the LLM_PROVIDER environment value.
    result = llm_generate(
        task,
        context,
        registry=registry,
        env={"LLM_PROVIDER": name},
    )

    # (1) Routed to exactly the configured provider.
    assert len(fake.calls) == 1
    assert fake.calls[0][0] == task
    assert result.provider == name

    # (2) Normalized result of the SAME shape for any provider value.
    assert isinstance(result, LLMResult)
    assert {f.name for f in fields(type(result))} == _RESULT_FIELDS
    assert result.task == task
    assert len(result.items) == len(records)
    for item in result.items:
        assert isinstance(item, LLMResultItem)
        assert {f.name for f in fields(type(item))} == _ITEM_FIELDS
        assert isinstance(item.content, str)
        assert isinstance(item.custom_id, str)


# Feature: phase0-data-foundation, Property 12 (companion): two different
# providers yield the SAME result shape for the same context, so the Extractor's
# parser is identical regardless of which provider served the call.
# Validates: Requirements 6.2
@_SETTINGS
@given(records=_record_lists)
def test_property_12_two_providers_yield_identical_shape(records):
    registry = ProviderRegistry()
    registry.register("bedrock", lambda _o: _RecordingFakeProvider("bedrock"))
    registry.register("anthropic", lambda _o: _RecordingFakeProvider("anthropic"))

    context = {"system_prompt": "rules", "records": records}

    r_bedrock = llm_generate("extract_records", context, registry=registry, env={"LLM_PROVIDER": "bedrock"})
    r_anthropic = llm_generate("extract_records", context, registry=registry, env={"LLM_PROVIDER": "anthropic"})

    # Same container shape and same per-item shape; only the provider label and
    # content differ -- caller parsing code is unchanged.
    assert type(r_bedrock) is type(r_anthropic)
    assert {f.name for f in fields(type(r_bedrock))} == {f.name for f in fields(type(r_anthropic))}
    assert len(r_bedrock.items) == len(r_anthropic.items) == len(records)
    for a, b in zip(r_bedrock.items, r_anthropic.items):
        assert {f.name for f in fields(type(a))} == {f.name for f in fields(type(b))}
