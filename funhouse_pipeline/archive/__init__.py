"""Archive stage: store originals to Object_Storage under the raw/ prefix.

The deterministic object-key convention is shared with Load (which writes
``lessons.original_file_ref`` using the same key), so it lives in
:mod:`funhouse_pipeline.archive.keys` and is re-exported here for both stages.

The upload/idempotency implementation (Task 13) lives in
:mod:`funhouse_pipeline.archive.archiver`: it uploads originals byte-for-byte
under ``raw/<subfolder>/<filename>`` in ``af-south-1`` over TLS, stores a
SHA-256 content hash as object metadata, and makes re-archival a no-op when the
key + hash match.
"""

from funhouse_pipeline.archive.archiver import (
    DEFAULT_REGION,
    SHA256_METADATA_KEY,
    ArchivedObject,
    Archiver,
    ArchiveResult,
    ArchiveStatus,
    sha256_of_bytes,
    sha256_of_file,
)
from funhouse_pipeline.archive.keys import RAW_PREFIX, archive_key

__all__ = [
    "RAW_PREFIX",
    "archive_key",
    "Archiver",
    "ArchiveResult",
    "ArchivedObject",
    "ArchiveStatus",
    "SHA256_METADATA_KEY",
    "DEFAULT_REGION",
    "sha256_of_bytes",
    "sha256_of_file",
]
