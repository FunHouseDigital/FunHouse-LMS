"""Property test: Collect and Validate perform no network I/O (Task 14.5).

Feature: phase0-data-foundation, Property 30: For any input, the Collect and
Validate stages complete without issuing any network call.

Validates: Requirements 15.3

Strategy
--------
We install a *network tripwire*: ``socket.socket`` is replaced with a subclass
whose constructor raises. Any attempt by Collect or Validate (directly or via a
transitive import) to open a network socket therefore fails the test loudly.
We then, for arbitrary generated inputs:

* build a real temporary ``Source_Folder`` with a random subset of the five
  subfolders and a random mix of supported/unsupported files, and run
  :func:`funhouse_pipeline.collect.collect`; and
* generate arbitrary :class:`ExtractedRecord`s and run
  :func:`funhouse_pipeline.validate.partition`.

Both must complete without tripping the socket guard.
"""

from __future__ import annotations

import socket
from datetime import datetime, timezone
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from funhouse_pipeline.collect import SOURCE_SUBFOLDERS, collect
from funhouse_pipeline.extract.context import build_business_rules
from funhouse_pipeline.extract.records import ExtractedRecord
from funhouse_pipeline.validate import partition

_MIN_ITERATIONS = 100


class _NetworkAttempted(RuntimeError):
    """Raised by the tripwire when any code tries to open a socket."""


class _TripwireSocket(socket.socket):
    def __init__(self, *args, **kwargs):  # noqa: D401 - guard
        raise _NetworkAttempted(
            "Collect/Validate attempted to create a network socket (Req 15.3)."
        )


@pytest.fixture
def _no_network(monkeypatch: pytest.MonkeyPatch):
    """Trip the test if any socket is created while the fixture is active."""
    monkeypatch.setattr(socket, "socket", _TripwireSocket)

    def _guard(*args, **kwargs):
        raise _NetworkAttempted("create_connection attempted (Req 15.3).")

    monkeypatch.setattr(socket, "create_connection", _guard)
    return monkeypatch


# --------------------------------------------------------------------------- #
# Generators
# --------------------------------------------------------------------------- #

_FILE_NAMES = st.sampled_from(
    ["a.jpg", "b.png", "c.docx", "d.txt", "e.pdf", "f.heic", "g.bin", "h"]
)

# A source-folder layout: for each of the five subfolders, whether it is present
# and what filenames it contains.
_layout = st.dictionaries(
    keys=st.sampled_from(SOURCE_SUBFOLDERS),
    values=st.lists(_FILE_NAMES, max_size=4, unique=True),
    max_size=len(SOURCE_SUBFOLDERS),
)

_TARGET_TABLES = st.sampled_from(
    ["players", "sessions", "payments", "lessons", "student_metrics"]
)

_payloads = st.dictionaries(
    keys=st.sampled_from(
        ["first_name", "last_name", "player_name", "amount", "paid_at", "birth_date"]
    ),
    values=st.one_of(st.none(), st.text(max_size=12), st.integers(-5, 100000)),
    max_size=4,
)


@st.composite
def _records(draw) -> ExtractedRecord:
    return ExtractedRecord(
        record_id=draw(st.text(min_size=1, max_size=8)),
        target_table=draw(_TARGET_TABLES),
        payload=draw(_payloads),
        confidence_score=draw(st.floats(min_value=0.0, max_value=1.0)),
        source_file=draw(st.text(min_size=1, max_size=16)),
        provider="test",
        extracted_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )


# --------------------------------------------------------------------------- #
# Property 30
# --------------------------------------------------------------------------- #


@settings(
    max_examples=_MIN_ITERATIONS,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(layout=_layout, records=st.lists(_records(), max_size=8))
def test_collect_and_validate_perform_no_network_io(
    layout, records, _no_network, tmp_path_factory
):
    # --- Collect over a real temporary Source_Folder ---------------------- #
    root: Path = tmp_path_factory.mktemp("src")
    for subfolder, files in layout.items():
        (root / subfolder).mkdir(parents=True, exist_ok=True)
        for name in files:
            (root / subfolder / name).write_bytes(b"x")

    collect_result = collect(root)
    # Sanity: collection still classifies present/absent subfolders.
    assert set(collect_result.present_subfolders) == set(layout.keys())

    # --- Validate over arbitrary records ---------------------------------- #
    rules = build_business_rules(())  # cold-start rules; no network
    result = partition(records, rules, threshold=0.7)
    # Total partition: every record lands in exactly one bucket.
    assert len(result.clean) + len(result.flagged) == len(records)
