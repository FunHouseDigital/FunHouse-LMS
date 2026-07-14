"""Property-based tests for the Extract image path (Tasks 6.4 and 6.5).

Implements design Properties 7 and 8. Both run with no network: Property 7
inspects the pure prompt/context builders, and Property 8 drives the extraction
path with an injected fake ``llm_generate``. Each property runs a minimum of 100
Hypothesis iterations, per the design's Testing Strategy.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from funhouse_pipeline.collect import HandlerTarget, RoutedFile
from funhouse_pipeline.db.seed import PARTNER_SCHOOLS, PROPOSED_SCHOOLS, SEED_PRODUCTS
from funhouse_pipeline.extract import (
    TARGET_TABLES,
    build_business_rules,
    build_extraction_context,
    build_system_prompt,
    extract_images,
)
from funhouse_pipeline.llm import LLMResult, LLMResultItem

pytestmark = pytest.mark.property

_SETTINGS = settings(max_examples=100, deadline=None)

# Player names drawn from a safe alphabet (letters + single spaces), stripped and
# non-empty, so "name in prompt" is a meaningful, unambiguous check.
_player_name = (
    st.text(alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ ", min_size=1, max_size=20)
    .map(lambda s: s.strip())
    .filter(lambda s: len(s) >= 2)
)


# Feature: phase0-data-foundation, Property 7: Extraction context always
# includes the business rules. For any image extraction request, the
# context/system prompt built for the LLM contains the pricing tiers, product
# rules, school names, and known player names.
# Validates: Requirements 4.2
@_SETTINGS
@given(known_names=st.lists(_player_name, min_size=0, max_size=8, unique=True))
def test_property_7_context_always_includes_business_rules(known_names):
    rules = build_business_rules(known_names)
    prompt = build_system_prompt(rules)

    # The prompt is exactly what the extraction request carries. Prove it is
    # embedded into the context built for the LLM (via a real image file).
    with tempfile.TemporaryDirectory() as tmp:
        image_path = Path(tmp) / "card.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake-image-bytes")
        routed = RoutedFile(
            path=image_path,
            subfolder="cards",
            source_type="membership/pay cards",
            handler=HandlerTarget.IMAGE_EXTRACT,
        )
        context = build_extraction_context([routed], prompt)

    request_prompt = context["system_prompt"]
    assert request_prompt == prompt

    # (1) Pricing tiers: every seeded product's Rand amount appears.
    for product in SEED_PRODUCTS:
        rand = product.price_cents // 100
        assert f"R{rand}" in request_prompt

    # (2) Product rules: every product name and its rules JSON content appear.
    for product in SEED_PRODUCTS:
        assert product.name in request_prompt
    assert "members" in request_prompt          # Subscription rule key
    assert "hours_per_week" in request_prompt    # Holiday Special / Subscription

    # (3) School names: every partner and proposed school appears.
    for school in PARTNER_SCHOOLS + PROPOSED_SCHOOLS:
        assert school in request_prompt

    # (4) Known player names: every injected name appears.
    for name in rules.known_player_names:
        assert name in request_prompt


# --------------------------------------------------------------------------- #
# Property 8 helpers: generate varied per-item model outputs.
# --------------------------------------------------------------------------- #

_confidence = st.one_of(
    st.none(),                                          # absent -> default
    st.floats(min_value=-10, max_value=10, allow_nan=False, allow_infinity=False),  # incl. out-of-range
    st.integers(min_value=-5, max_value=5),
    st.text(max_size=5),                                # non-numeric junk
)
_payload = st.dictionaries(
    keys=st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=10),
    values=st.one_of(st.text(max_size=12), st.integers(), st.none(), st.booleans()),
    max_size=5,
)


def _record(target, conf, payload):
    rec = {"target_table": target, "payload": payload}
    if conf is not None:
        rec["confidence"] = conf
    return rec


_valid_records_json = st.lists(
    st.builds(_record, st.sampled_from(TARGET_TABLES), _confidence, _payload),
    min_size=0,
    max_size=4,
).map(lambda recs: json.dumps({"records": recs}))

_junk_content = st.text(max_size=40)  # mostly unparseable -> malformed -> 0.0

_item_content = st.one_of(_valid_records_json, _junk_content)


class _FakeLLM:
    """Fake ``llm_generate`` returning a preset content per input record."""

    def __init__(self, contents: dict[str, str]) -> None:
        self._contents = contents

    def __call__(self, task, context, **kwargs):
        items = tuple(
            LLMResultItem(custom_id=rec["custom_id"], content=self._contents[rec["custom_id"]])
            for rec in context["records"]
        )
        return LLMResult(task=task, provider="bedrock", items=items)


# Feature: phase0-data-foundation, Property 8: Every extracted record carries a
# complete envelope. For any record produced by the Extractor, the record has a
# confidence_score in the closed interval [0, 1] and a non-empty source_file
# provenance reference.
# Validates: Requirements 4.3, 4.4
@_SETTINGS
@given(contents=st.lists(_item_content, min_size=1, max_size=4))
def test_property_8_every_record_has_complete_envelope(contents):
    with tempfile.TemporaryDirectory() as tmp:
        routed: list[RoutedFile] = []
        contents_by_id: dict[str, str] = {}
        for i, content in enumerate(contents):
            image_path = Path(tmp) / f"img_{i}.png"
            image_path.write_bytes(b"bytes")
            routed.append(
                RoutedFile(
                    path=image_path,
                    subfolder="cards",
                    source_type="membership/pay cards",
                    handler=HandlerTarget.IMAGE_EXTRACT,
                )
            )
            contents_by_id[str(image_path)] = content

        records = extract_images(routed, llm_generate_fn=_FakeLLM(contents_by_id))

        for record in records:
            # confidence_score is within the closed interval [0, 1] (Req 4.3).
            assert isinstance(record.confidence_score, float)
            assert 0.0 <= record.confidence_score <= 1.0
            # source_file provenance is non-empty (Req 4.4).
            assert isinstance(record.source_file, str)
            assert record.source_file.strip()
            # target_table is always one of the five (malformed -> players).
            assert record.target_table in TARGET_TABLES
