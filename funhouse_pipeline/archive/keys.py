"""Deterministic Object_Storage key derivation (shared by Load + Archive).

The Archive stage (Task 13) stores every original source file under the
``raw/`` prefix using a **deterministic** object key so re-archival is a no-op
and every loaded row can be traced back to its archived original
(Req 12.1, design "Idempotency & Re-Runnability"). The Load stage (Task 11.4)
needs that *same* key ahead of Archive so it can write ``lessons.original_file_ref``
consistently (Req 10.2). To guarantee the two stages never diverge, the key
convention lives here in one place and both stages call :func:`archive_key`.

Key convention (design § Archive)
---------------------------------
::

    raw/<source-subfolder>/<original-filename>

where ``<source-subfolder>`` is the immediate parent directory of the source
file (one of ``cards`` / ``sheets`` / ``lessons`` / ``photos`` / ``whatsapp``)
and ``<original-filename>`` is the file's basename. Examples::

    /data/source/lessons/week1.docx  -> raw/lessons/week1.docx
    photos/img_0001.jpg              -> raw/photos/img_0001.jpg
    week1.docx                       -> raw/week1.docx   (no subfolder available)

The function is pure and deterministic: the same ``source_file`` always maps to
the same key. It performs no I/O.
"""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath

#: The prefix under which originals are archived in Object_Storage (Req 12.1).
RAW_PREFIX = "raw"


def _as_posix_parts(source_file: str) -> PurePosixPath:
    """Normalize a source path (posix or windows separators) to a posix path.

    Source provenance strings come from the local filesystem and are posix on
    the operator laptop/container, but a backslash-separated path is handled
    defensively so the derived key is stable regardless of how provenance was
    recorded.
    """
    text = str(source_file).strip()
    if "\\" in text and "/" not in text:
        # Looks like a pure Windows path; translate separators.
        return PurePosixPath(*PureWindowsPath(text).parts)
    return PurePosixPath(text)


def archive_key(source_file: str) -> str:
    """Return the deterministic ``raw/<subfolder>/<filename>`` key for a source.

    Args:
        source_file: Provenance path/reference of the original source file
            (e.g. an :class:`~funhouse_pipeline.extract.records.ExtractedRecord`'s
            ``source_file``).

    Returns:
        The object key the original is (or will be) archived under, e.g.
        ``raw/lessons/week1.docx``. When no parent subfolder can be determined
        the key is ``raw/<filename>``.
    """
    path = _as_posix_parts(source_file)
    filename = path.name
    subfolder = path.parent.name  # immediate parent dir, "" when none
    if subfolder:
        return f"{RAW_PREFIX}/{subfolder}/{filename}"
    return f"{RAW_PREFIX}/{filename}"
