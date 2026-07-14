"""Unit tests for the Collect stage (Task 5.1) - concrete example scenarios.

These complement the property-based tests with specific, readable cases:
routing by source type, case-insensitive extensions, absent subfolders, skip
reasons, and manifest-compatible serialization. Pure filesystem, no network.
"""

from __future__ import annotations

from pathlib import Path

from funhouse_pipeline.collect import (
    HandlerTarget,
    SOURCE_SUBFOLDERS,
    collect,
)


def _make_tree(root: Path, files: dict[str, list[str]]) -> None:
    for subfolder, names in files.items():
        sub = root / subfolder
        sub.mkdir(parents=True, exist_ok=True)
        for name in names:
            (sub / name).write_bytes(b"content")


def test_routes_images_and_docx_to_correct_handlers(tmp_path):
    _make_tree(
        tmp_path,
        {
            "cards": ["a.jpg", "b.png"],
            "sheets": ["s.pdf"],
            "photos": ["p.heic"],
            "whatsapp": ["chat.jpeg"],
            "lessons": ["lesson1.docx"],
        },
    )

    result = collect(tmp_path)

    # All five subfolders present, none absent, nothing skipped.
    assert set(result.present_subfolders) == set(SOURCE_SUBFOLDERS)
    assert result.absent_subfolders == ()
    assert result.skipped == ()

    # Image folders route to the image extract path; lessons to the docx parser.
    image_routed = result.routed_for(HandlerTarget.IMAGE_EXTRACT)
    docx_routed = result.routed_for(HandlerTarget.DOCX_TEXT_PARSER)
    assert len(image_routed) == 5  # a, b, s, p, chat
    assert len(docx_routed) == 1
    assert docx_routed[0].subfolder == "lessons"
    assert docx_routed[0].path.name == "lesson1.docx"


def test_extension_matching_is_case_insensitive(tmp_path):
    _make_tree(tmp_path, {"cards": ["UPPER.JPG"], "lessons": ["Doc.DOCX"]})

    result = collect(tmp_path)

    assert len(result.routed) == 2
    assert result.skipped == ()


def test_missing_subfolders_recorded_absent(tmp_path):
    _make_tree(tmp_path, {"cards": ["a.jpg"]})

    result = collect(tmp_path)

    assert result.present_subfolders == ("cards",)
    assert set(result.absent_subfolders) == set(SOURCE_SUBFOLDERS) - {"cards"}
    assert len(result.routed) == 1


def test_unsupported_files_skipped_with_path_and_reason(tmp_path):
    _make_tree(tmp_path, {"cards": ["good.png", "bad.txt"], "lessons": ["notes.pdf"]})

    result = collect(tmp_path)

    assert len(result.routed) == 1  # good.png only
    skipped_names = {s.path.name: s for s in result.skipped}
    assert set(skipped_names) == {"bad.txt", "notes.pdf"}
    for skip in result.skipped:
        assert str(skip.path)  # non-empty path
        assert skip.reason.strip()  # non-empty reason
    # A .pdf is valid in image folders but unsupported in lessons.
    assert "lessons" in skipped_names["notes.pdf"].reason


def test_wholly_missing_source_folder_reports_all_absent(tmp_path):
    missing = tmp_path / "does-not-exist"

    result = collect(missing)

    assert set(result.absent_subfolders) == set(SOURCE_SUBFOLDERS)
    assert result.routed == ()
    assert result.skipped == ()
    assert result.present_subfolders == ()


def test_result_serializes_to_manifest_dict(tmp_path):
    _make_tree(tmp_path, {"cards": ["a.jpg", "bad.txt"]})

    manifest = collect(tmp_path).to_manifest_dict()

    assert set(manifest) == {"routed", "skipped", "present_subfolders", "absent_subfolders"}
    assert manifest["routed"][0]["handler"] == HandlerTarget.IMAGE_EXTRACT.value
    assert manifest["skipped"][0]["path"].endswith("bad.txt")
    assert "reason" in manifest["skipped"][0]


def test_nested_files_within_a_subfolder_are_walked(tmp_path):
    cards = tmp_path / "cards" / "batch1"
    cards.mkdir(parents=True)
    (cards / "nested.jpg").write_bytes(b"x")

    result = collect(tmp_path)

    assert len(result.routed) == 1
    assert result.routed[0].path.name == "nested.jpg"
