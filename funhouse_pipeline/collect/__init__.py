"""Collect stage: read the Source_Folder and route files by source type.

This is the first pipeline stage (Collect). It walks the fixed set of source
subfolders inside a ``Source_Folder`` and routes each file to the downstream
handler appropriate for its source type, per the design's routing table:

===========  ===================================  =========================
Subfolder    Source type                          Downstream handler
===========  ===================================  =========================
``cards/``   membership / pay cards (images)      Extract -> Bedrock image path
``sheets/``  attendance / payment sheets (images) Extract -> Bedrock image path
``photos/``  photos of records (images)           Extract -> Bedrock image path
``whatsapp/``exported chat images                 Extract -> Bedrock image path
``lessons/`` ``.docx`` lesson documents           Extract -> ``.docx`` text parser
===========  ===================================  =========================

Behavior (Req 3):
- Read files from ``cards/``, ``sheets/``, ``lessons/``, ``photos/``,
  ``whatsapp/`` (Req 3.1).
- A missing subfolder is recorded as **absent** and processing continues with
  the remaining subfolders (Req 3.2).
- A file whose type is unsupported for its subfolder is **skipped**, and the
  skip is recorded with the file path and a reason (Req 3.3).

This stage performs **no network I/O** whatsoever - it is pure local
filesystem work (Req 15.3). It produces a self-contained :class:`CollectResult`
whose contents (routed files, absent subfolders, skips) are compatible with the
run manifest the orchestrator threads through every stage (Task 14).

The :class:`HandlerTarget` enum is a lightweight marker that downstream stages
(Extract) consume to decide which extraction path a routed file takes; the
Extract implementation itself is out of scope for this stage.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Source layout and routing configuration
# --------------------------------------------------------------------------- #


class HandlerTarget(str, Enum):
    """Downstream handler a routed file is destined for.

    A lightweight marker consumed by the Extract stage. ``str`` mixin so the
    value serializes cleanly into the run manifest.
    """

    #: Image sources -> Extract via the Bedrock Batch image path.
    IMAGE_EXTRACT = "image_extract"
    #: ``.docx`` lesson documents -> Extract via the text parser (no OCR).
    DOCX_TEXT_PARSER = "docx_text_parser"


# Supported file extensions per source type (lowercased, incl. leading dot).
# Image folders accept common photo/scan formats; lessons accept only .docx.
IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".heic", ".pdf"}
)
DOCX_EXTENSIONS: frozenset[str] = frozenset({".docx"})


@dataclass(frozen=True)
class SubfolderSpec:
    """Routing rules for a single source subfolder."""

    name: str
    source_type: str
    handler: HandlerTarget
    supported_extensions: frozenset[str]


# The five expected subfolders and how each is routed (Req 3.1). Ordered as in
# the design so the collect result is deterministic.
SUBFOLDER_SPECS: tuple[SubfolderSpec, ...] = (
    SubfolderSpec("cards", "membership/pay cards", HandlerTarget.IMAGE_EXTRACT, IMAGE_EXTENSIONS),
    SubfolderSpec("sheets", "attendance/payment sheets", HandlerTarget.IMAGE_EXTRACT, IMAGE_EXTENSIONS),
    SubfolderSpec("lessons", "lesson documents", HandlerTarget.DOCX_TEXT_PARSER, DOCX_EXTENSIONS),
    SubfolderSpec("photos", "photos of records", HandlerTarget.IMAGE_EXTRACT, IMAGE_EXTENSIONS),
    SubfolderSpec("whatsapp", "exported chat images", HandlerTarget.IMAGE_EXTRACT, IMAGE_EXTENSIONS),
)

#: Names of the expected subfolders, in processing order (Req 3.1).
SOURCE_SUBFOLDERS: tuple[str, ...] = tuple(spec.name for spec in SUBFOLDER_SPECS)

_SPEC_BY_NAME: dict[str, SubfolderSpec] = {spec.name: spec for spec in SUBFOLDER_SPECS}


# --------------------------------------------------------------------------- #
# Result model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RoutedFile:
    """A source file successfully routed to a downstream handler."""

    path: Path
    subfolder: str
    source_type: str
    handler: HandlerTarget

    def to_dict(self) -> dict[str, str]:
        return {
            "path": str(self.path),
            "subfolder": self.subfolder,
            "source_type": self.source_type,
            "handler": self.handler.value,
        }


@dataclass(frozen=True)
class SkippedFile:
    """A source file skipped because its type is unsupported (Req 3.3)."""

    path: Path
    subfolder: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "path": str(self.path),
            "subfolder": self.subfolder,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class CollectResult:
    """Outcome of a Collect run - a self-contained, manifest-compatible view.

    Attributes:
        routed: Files routed to a downstream handler.
        skipped: Files skipped with a recorded path + reason (Req 3.3).
        present_subfolders: Expected subfolders that existed and were processed.
        absent_subfolders: Expected subfolders recorded as absent (Req 3.2).
    """

    routed: tuple[RoutedFile, ...]
    skipped: tuple[SkippedFile, ...]
    present_subfolders: tuple[str, ...]
    absent_subfolders: tuple[str, ...]

    def routed_for(self, handler: HandlerTarget) -> list[RoutedFile]:
        """Return routed files destined for a specific downstream handler."""
        return [r for r in self.routed if r.handler == handler]

    def routed_in(self, subfolder: str) -> list[RoutedFile]:
        """Return routed files that came from a specific subfolder."""
        return [r for r in self.routed if r.subfolder == subfolder]

    def skipped_in(self, subfolder: str) -> list[SkippedFile]:
        """Return skipped files that came from a specific subfolder."""
        return [s for s in self.skipped if s.subfolder == subfolder]

    def to_manifest_dict(self) -> dict[str, Any]:
        """Serialize into a plain dict compatible with the run manifest."""
        return {
            "routed": [r.to_dict() for r in self.routed],
            "skipped": [s.to_dict() for s in self.skipped],
            "present_subfolders": list(self.present_subfolders),
            "absent_subfolders": list(self.absent_subfolders),
        }

    def summary(self) -> str:
        absent = ", ".join(self.absent_subfolders) or "(none)"
        return (
            f"Collect complete: {len(self.routed)} routed, "
            f"{len(self.skipped)} skipped, "
            f"{len(self.present_subfolders)} subfolders present, "
            f"{len(self.absent_subfolders)} absent.\n"
            f"  Absent subfolders: {absent}"
        )


# --------------------------------------------------------------------------- #
# Core
# --------------------------------------------------------------------------- #


def _iter_files(folder: Path) -> list[Path]:
    """Return every regular file under ``folder`` (recursively), sorted.

    Recurses so nested layouts within a subfolder are handled. Directories,
    symlinked directories, and non-file entries are ignored. Sorting keeps the
    result deterministic regardless of filesystem enumeration order.
    """
    return sorted(p for p in folder.rglob("*") if p.is_file())


def collect(source_folder: str | Path) -> CollectResult:
    """Walk the Source_Folder subfolders and route files by source type.

    Reads ``cards/``, ``sheets/``, ``lessons/``, ``photos/`` and ``whatsapp/``
    within ``source_folder`` (Req 3.1). Missing subfolders are recorded as
    absent and do not halt collection (Req 3.2); files of an unsupported type
    for their subfolder are skipped with a recorded path and reason (Req 3.3).

    This function performs **no network I/O** (Req 15.3): it only reads the
    local filesystem.

    Args:
        source_folder: Path to the input directory containing the source
            subfolders. It need not exist; a wholly missing folder yields all
            subfolders recorded as absent.

    Returns:
        A :class:`CollectResult` capturing routed files, skips, and which
        subfolders were present vs. absent.
    """
    root = Path(source_folder)

    routed: list[RoutedFile] = []
    skipped: list[SkippedFile] = []
    present: list[str] = []
    absent: list[str] = []

    for spec in SUBFOLDER_SPECS:
        subfolder_path = root / spec.name

        # A missing (or non-directory) subfolder is absent; keep going (Req 3.2).
        if not subfolder_path.is_dir():
            absent.append(spec.name)
            continue

        present.append(spec.name)

        for file_path in _iter_files(subfolder_path):
            extension = file_path.suffix.lower()
            if extension in spec.supported_extensions:
                routed.append(
                    RoutedFile(
                        path=file_path,
                        subfolder=spec.name,
                        source_type=spec.source_type,
                        handler=spec.handler,
                    )
                )
            else:
                supported = ", ".join(sorted(spec.supported_extensions))
                shown = extension if extension else "(none)"
                skipped.append(
                    SkippedFile(
                        path=file_path,
                        subfolder=spec.name,
                        reason=(
                            f"unsupported file type '{shown}' for subfolder "
                            f"'{spec.name}' (supported: {supported})"
                        ),
                    )
                )

    return CollectResult(
        routed=tuple(routed),
        skipped=tuple(skipped),
        present_subfolders=tuple(present),
        absent_subfolders=tuple(absent),
    )
