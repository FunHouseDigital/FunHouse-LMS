"""Unit / example tests for the Extract image path (Tasks 6.6, 6.7 + core logic).

Concrete, readable scenarios complementing the property tests: the five CSVs are
always produced (6.6); the Extract module uses only the LLM abstraction and no
provider SDK (6.7); and the model-output parsing contract / confidence defaulting
behaves as documented. Pure local work with an injected fake LLM -- no network.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from funhouse_pipeline.collect import HandlerTarget, RoutedFile
from funhouse_pipeline.extract import (
    DEFAULT_CONFIDENCE,
    MALFORMED_CONFIDENCE,
    TARGET_TABLES,
    ExtractedRecord,
    build_business_rules,
    build_system_prompt,
    extract_images,
    header_for,
    parse_item_content,
    write_csvs,
)
from funhouse_pipeline.extract.csv_writer import ENVELOPE_COLUMNS
from funhouse_pipeline.llm import LLMResult, LLMResultItem

_FIXED_NOW = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def _routed_image(path: Path) -> RoutedFile:
    return RoutedFile(
        path=path,
        subfolder="cards",
        source_type="membership/pay cards",
        handler=HandlerTarget.IMAGE_EXTRACT,
    )


def _record(table: str) -> ExtractedRecord:
    return ExtractedRecord(
        record_id="r1",
        target_table=table,
        payload={},
        confidence_score=0.9,
        source_file="cards/x.png",
        provider="bedrock",
        extracted_at=_FIXED_NOW,
    )


# --------------------------------------------------------------------------- #
# Task 6.6 -- exactly five CSVs are produced.
# --------------------------------------------------------------------------- #


def test_exactly_five_csvs_are_produced(tmp_path):
    # Provide records for only two tables; all five CSVs must still be written.
    records = [_record("players"), _record("payments")]

    paths = write_csvs(records, tmp_path)

    assert set(paths) == set(TARGET_TABLES)
    csv_files = sorted(p.name for p in tmp_path.glob("*.csv"))
    assert csv_files == sorted(f"{t}.csv" for t in TARGET_TABLES)
    assert len(csv_files) == 5


def test_empty_tables_still_get_a_header_only_csv(tmp_path):
    paths = write_csvs([_record("players")], tmp_path)

    # sessions had no records -> file exists with just the header line.
    sessions_lines = paths["sessions"].read_text(encoding="utf-8").splitlines()
    assert len(sessions_lines) == 1
    assert sessions_lines[0].split(",")[: len(ENVELOPE_COLUMNS)] == list(ENVELOPE_COLUMNS)


def test_csv_header_has_envelope_then_domain_columns(tmp_path):
    write_csvs([_record("payments")], tmp_path)
    header = header_for("payments")
    assert header[: len(ENVELOPE_COLUMNS)] == list(ENVELOPE_COLUMNS)
    assert "amount" in header and "product_name" in header
    # lessons.source_file must not duplicate the envelope source_file column.
    lessons_header = header_for("lessons")
    assert lessons_header.count("source_file") == 1


def test_csv_row_contains_envelope_and_domain_values(tmp_path):
    rec = ExtractedRecord(
        record_id="abc123",
        target_table="players",
        payload={"first_name": "Thabo", "last_name": "M", "photo_consent": True},
        confidence_score=0.42,
        source_file="cards/thabo.png",
        provider="bedrock",
        extracted_at=_FIXED_NOW,
    )
    paths = write_csvs([rec], tmp_path)
    text = paths["players"].read_text(encoding="utf-8")
    assert "abc123" in text
    assert "cards/thabo.png" in text
    assert "Thabo" in text
    assert "true" in text  # boolean rendered
    assert _FIXED_NOW.isoformat() in text


# --------------------------------------------------------------------------- #
# Task 6.7 -- Extract uses only the abstraction, never a provider SDK.
# --------------------------------------------------------------------------- #


def test_extract_module_imports_only_the_abstraction():
    import funhouse_pipeline.extract as extract_pkg
    from funhouse_pipeline.extract import context, csv_writer, images, records
    from funhouse_pipeline.llm import llm_generate

    forbidden = {
        "boto3",
        "anthropic",
        "BedrockBatchProvider",
        "AnthropicProvider",
        "BedrockBatchError",
    }
    for module in (extract_pkg, context, csv_writer, images, records):
        leaked = forbidden.intersection(vars(module))
        assert not leaked, f"{module.__name__} must not import a provider SDK; found {leaked}"

    # The image path routes through the abstraction's single entry point.
    assert images._default_llm_generate is llm_generate


# --------------------------------------------------------------------------- #
# Model-output parsing contract + confidence defaulting.
# --------------------------------------------------------------------------- #


def test_parse_records_wrapper_form():
    content = (
        '{"records": [{"target_table": "players", "confidence": 0.8, '
        '"payload": {"first_name": "Aya"}}]}'
    )
    parsed = parse_item_content(content)
    assert len(parsed) == 1
    assert parsed[0].target_table == "players"
    assert parsed[0].confidence == 0.8
    assert parsed[0].payload == {"first_name": "Aya"}
    assert parsed[0].malformed is False


def test_parse_records_bare_array_and_inline_payload():
    content = '[{"target_table": "payments", "amount": "R30", "confidence": 0.6}]'
    parsed = parse_item_content(content)
    assert len(parsed) == 1
    assert parsed[0].target_table == "payments"
    assert parsed[0].payload == {"amount": "R30"}


def test_parse_missing_confidence_uses_default():
    parsed = parse_item_content('{"records": [{"target_table": "lessons", "payload": {}}]}')
    assert parsed[0].confidence == DEFAULT_CONFIDENCE
    assert parsed[0].malformed is False


def test_parse_out_of_range_confidence_is_clamped():
    high = parse_item_content('{"records": [{"target_table": "players", "confidence": 5}]}')
    low = parse_item_content('{"records": [{"target_table": "players", "confidence": -3}]}')
    assert high[0].confidence == 1.0
    assert low[0].confidence == 0.0


def test_parse_malformed_json_yields_zero_confidence_record():
    parsed = parse_item_content("this is not json at all")
    assert len(parsed) == 1
    assert parsed[0].confidence == MALFORMED_CONFIDENCE
    assert parsed[0].malformed is True
    assert parsed[0].target_table == "players"  # documented default target


def test_parse_invalid_target_table_is_flagged():
    parsed = parse_item_content('{"records": [{"target_table": "not_a_table", "x": 1}]}')
    assert parsed[0].malformed is True
    assert parsed[0].confidence == MALFORMED_CONFIDENCE


def test_parse_code_fenced_json():
    content = "```json\n{\"records\": [{\"target_table\": \"players\", \"confidence\": 0.5}]}\n```"
    parsed = parse_item_content(content)
    assert parsed[0].target_table == "players"
    assert parsed[0].malformed is False


def test_parse_empty_records_list_yields_nothing():
    assert parse_item_content('{"records": []}') == []


# --------------------------------------------------------------------------- #
# extract_images end-to-end with a fake LLM.
# --------------------------------------------------------------------------- #


class _FakeLLM:
    def __init__(self, content: str, provider: str = "bedrock") -> None:
        self.content = content
        self.provider = provider
        self.captured_context = None
        self.captured_task = None

    def __call__(self, task, context, **kwargs):
        self.captured_task = task
        self.captured_context = context
        items = tuple(
            LLMResultItem(custom_id=rec["custom_id"], content=self.content)
            for rec in context["records"]
        )
        return LLMResult(task=task, provider=self.provider, items=items)


def test_extract_images_envelopes_records_with_provenance(tmp_path):
    image = tmp_path / "card.png"
    image.write_bytes(b"img")
    fake = _FakeLLM(
        '{"records": [{"target_table": "players", "confidence": 0.9, '
        '"payload": {"first_name": "Loyiso"}}]}'
    )

    records = extract_images(
        [_routed_image(image)],
        known_player_names=["Loyiso"],
        llm_generate_fn=fake,
        now=lambda: _FIXED_NOW,
    )

    assert len(records) == 1
    rec = records[0]
    assert rec.target_table == "players"
    assert rec.confidence_score == 0.9
    assert rec.source_file == str(image)      # provenance (Req 4.4)
    assert rec.provider == "bedrock"
    assert rec.extracted_at == _FIXED_NOW
    assert rec.payload == {"first_name": "Loyiso"}
    assert rec.record_id  # stable id present

    # The business-rules system prompt was sent in the request (Req 4.2).
    assert "Loyiso" in fake.captured_context["system_prompt"]
    assert fake.captured_task == "extract_records"


def test_extract_images_ignores_docx_routed_files(tmp_path):
    docx = tmp_path / "lesson.docx"
    docx.write_bytes(b"doc")
    docx_routed = RoutedFile(
        path=docx,
        subfolder="lessons",
        source_type="lesson documents",
        handler=HandlerTarget.DOCX_TEXT_PARSER,
    )

    called = {"n": 0}

    def _never(task, context, **kwargs):  # pragma: no cover - must not be called
        called["n"] += 1
        raise AssertionError("llm_generate should not run when there are no images")

    records = extract_images([docx_routed], llm_generate_fn=_never)
    assert records == []
    assert called["n"] == 0


def test_extract_images_full_flow_writes_five_csvs(tmp_path):
    image = tmp_path / "sheet.png"
    image.write_bytes(b"img")
    content = (
        '{"records": ['
        '{"target_table": "players", "confidence": 0.9, "payload": {"first_name": "A"}},'
        '{"target_table": "payments", "confidence": 0.7, "payload": {"amount": "R10"}}'
        ']}'
    )
    records = extract_images([_routed_image(image)], llm_generate_fn=_FakeLLM(content))

    out = tmp_path / "csvs"
    paths = write_csvs(records, out)
    assert set(paths) == set(TARGET_TABLES)
    assert len(list(out.glob("*.csv"))) == 5
