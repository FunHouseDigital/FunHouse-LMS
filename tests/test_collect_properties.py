"""Property-based tests for the Collect stage (Tasks 5.2 and 5.3).

Implements design Properties 5 and 6. Collect is pure local-filesystem work
with no network I/O, so these run with no external services. Folder trees are
built inside a fresh temporary directory per Hypothesis example (via
``tempfile.TemporaryDirectory``) so examples never contaminate one another and
no function-scoped fixture is shared across iterations. Each property runs a
minimum of 100 Hypothesis iterations, per the design's Testing Strategy.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from funhouse_pipeline.collect import (
    DOCX_EXTENSIONS,
    IMAGE_EXTENSIONS,
    SOURCE_SUBFOLDERS,
    SUBFOLDER_SPECS,
    collect,
)

pytestmark = pytest.mark.property

_SETTINGS = settings(max_examples=100, deadline=None)

# A supported extension per subfolder, used to populate present subfolders with
# files that MUST be routed to completion.
_SUPPORTED_EXAMPLE: dict[str, str] = {
    spec.name: sorted(spec.supported_extensions)[0] for spec in SUBFOLDER_SPECS
}

# Extensions we draw from to build *unsupported* files. Includes formats that
# are supported in *some* folder (e.g. .docx, .jpg) so the test also proves the
# routing is per-subfolder, not global.
_CANDIDATE_EXTENSIONS = [
    ".txt", ".csv", ".xlsx", ".doc", ".json", ".zip", ".mp4",
    ".mov", ".gif", ".bmp", ".rtf", ".docx", ".jpg", ".png", ".pdf", ".heic",
]

_filename_base = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_",
    min_size=1,
    max_size=12,
)


def _supported_ext_for(subfolder: str) -> frozenset[str]:
    return DOCX_EXTENSIONS if subfolder == "lessons" else IMAGE_EXTENSIONS


# Feature: phase0-data-foundation, Property 5: Missing subfolders do not halt
# collection. For any subset of the five expected subfolders being absent,
# collection records each absent subfolder and still processes every present
# subfolder to completion.
# Validates: Requirements 3.2
@_SETTINGS
@given(
    present_mask=st.lists(st.booleans(), min_size=len(SOURCE_SUBFOLDERS), max_size=len(SOURCE_SUBFOLDERS)),
    file_counts=st.lists(st.integers(min_value=0, max_value=3), min_size=len(SOURCE_SUBFOLDERS), max_size=len(SOURCE_SUBFOLDERS)),
)
def test_property_5_missing_subfolders_do_not_halt_collection(present_mask, file_counts):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        expected_absent: set[str] = set()
        expected_routed_per_folder: dict[str, int] = {}

        # Build a folder tree: present subfolders get N supported files each;
        # absent subfolders are simply never created.
        for is_present, count, subfolder in zip(present_mask, file_counts, SOURCE_SUBFOLDERS):
            if not is_present:
                expected_absent.add(subfolder)
                continue
            sub_path = root / subfolder
            sub_path.mkdir(parents=True)
            ext = _SUPPORTED_EXAMPLE[subfolder]
            for i in range(count):
                (sub_path / f"file_{i}{ext}").write_bytes(b"x")
            expected_routed_per_folder[subfolder] = count

        result = collect(root)

        # (1) Every absent subfolder is recorded, and only those.
        assert set(result.absent_subfolders) == expected_absent
        # (2) Present subfolders are exactly the complement, and are recorded.
        assert set(result.present_subfolders) == set(SOURCE_SUBFOLDERS) - expected_absent
        # (3) Absent + present partition the five expected subfolders.
        assert set(result.absent_subfolders) | set(result.present_subfolders) == set(SOURCE_SUBFOLDERS)

        # (4) Every present subfolder is processed to completion: all of its
        # supported files are routed. Absent ones contribute nothing.
        for subfolder in SOURCE_SUBFOLDERS:
            routed_here = result.routed_in(subfolder)
            assert len(routed_here) == expected_routed_per_folder.get(subfolder, 0)

        # (5) The absence of some subfolders never suppressed a present one:
        # total routed equals the sum over present subfolders.
        assert len(result.routed) == sum(expected_routed_per_folder.values())


# Feature: phase0-data-foundation, Property 6: Unsupported files are skipped
# with a recorded reason. For any file whose type is unsupported for its
# subfolder, collection skips the file and records a skip entry containing the
# file path and a reason.
# Validates: Requirements 3.3
@_SETTINGS
@given(
    subfolder=st.sampled_from(SOURCE_SUBFOLDERS),
    base=_filename_base,
    ext=st.sampled_from(_CANDIDATE_EXTENSIONS),
)
def test_property_6_unsupported_files_are_skipped_with_reason(subfolder, base, ext):
    # Only exercise genuinely-unsupported extensions for the chosen subfolder.
    assume(ext.lower() not in _supported_ext_for(subfolder))

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        sub_path = root / subfolder
        sub_path.mkdir(parents=True)
        unsupported = sub_path / f"{base}{ext}"
        unsupported.write_bytes(b"data")

        result = collect(root)

        # The file is skipped (not routed).
        routed_paths = {r.path for r in result.routed}
        assert unsupported not in routed_paths

        # A skip entry exists for exactly this file, carrying path + a reason.
        matching = [s for s in result.skipped if s.path == unsupported]
        assert len(matching) == 1
        skip = matching[0]
        assert skip.path == unsupported          # path recorded (Req 3.3)
        assert skip.subfolder == subfolder
        assert isinstance(skip.reason, str) and skip.reason.strip()  # reason recorded
        assert ext.lstrip(".") in skip.reason or ext in skip.reason
