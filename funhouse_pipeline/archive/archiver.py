"""Archive stage: store originals to Object_Storage under the ``raw/`` prefix.

The Archiver uploads every original source file **byte-for-byte unmodified** to
S3 under a deterministic key (``raw/<subfolder>/<filename>`` -- see
:func:`funhouse_pipeline.archive.keys.archive_key`), so no source material is
lost and the pipeline can be re-run from originals (Req 12.1, 12.3). The bucket
lives in region ``af-south-1`` and transfer uses TLS (boto3's default is HTTPS)
(Req 12.2, 14.3, 14.4).

Integrity + idempotent re-archival
----------------------------------
A **SHA-256** digest of the original content is stored as S3 object metadata.
On (re-)archival the Archiver issues a ``head_object`` for the target key:

- object **absent** -> upload it;
- object **present with a matching** ``sha256`` metadata value -> skip the
  upload and treat it as success (idempotent no-op, Req 12.3 / design
  "Idempotency & Re-Runnability");
- object **present with a different / missing** hash -> re-upload so the
  archived copy matches the current original.

Injectable client
------------------
The boto3 S3 client is **injectable** (consistent with
:class:`~funhouse_pipeline.llm.bedrock.BedrockBatchProvider`), so tests drive the
whole path against a moto-backed S3 with no live AWS. When no client is
injected one is created lazily, pinned to the configured region.

This module reads originals from the local filesystem; it needs no database.
It accepts routed files from Collect (:class:`~funhouse_pipeline.collect.RoutedFile`)
and/or direct filesystem paths.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from funhouse_pipeline.archive.keys import RAW_PREFIX, archive_key
from funhouse_pipeline.collect import RoutedFile

#: S3 object-metadata key under which the content SHA-256 hex digest is stored.
#: (S3 lowercases user-metadata keys, so this is stored/read as ``sha256``.)
SHA256_METADATA_KEY = "sha256"

#: Default region for a lazily-created S3 client (Req 12.2, 14.4).
DEFAULT_REGION = "af-south-1"

#: Read size when hashing a file off disk (bytes).
_HASH_CHUNK = 1024 * 1024


class ArchiveStatus(str, Enum):
    """Outcome of archiving a single source file.

    ``str`` mixin so the value serializes cleanly into the run manifest.
    """

    #: The object was uploaded (absent before, or its hash had changed).
    UPLOADED = "uploaded"
    #: The object already existed with a matching hash -> no-op (idempotent).
    SKIPPED = "skipped"


@dataclass(frozen=True)
class ArchivedObject:
    """Record of one archived (or already-present) original."""

    key: str
    sha256: str
    source_path: str
    status: ArchiveStatus

    def to_dict(self) -> dict[str, str]:
        return {
            "key": self.key,
            "sha256": self.sha256,
            "source_path": self.source_path,
            "status": self.status.value,
        }


@dataclass(frozen=True)
class ArchiveResult:
    """Outcome of an Archive run - manifest-compatible.

    Attributes:
        objects: One entry per input file (uploaded or skipped), in input order.
        bucket: The destination bucket.
    """

    objects: tuple[ArchivedObject, ...]
    bucket: str

    @property
    def uploaded(self) -> list[ArchivedObject]:
        """Files that were uploaded this run."""
        return [o for o in self.objects if o.status is ArchiveStatus.UPLOADED]

    @property
    def skipped(self) -> list[ArchivedObject]:
        """Files already present with a matching hash (idempotent no-ops)."""
        return [o for o in self.objects if o.status is ArchiveStatus.SKIPPED]

    def keys(self) -> list[str]:
        """All archived object keys (uploaded + skipped)."""
        return [o.key for o in self.objects]

    def to_manifest_dict(self) -> dict[str, Any]:
        return {
            "bucket": self.bucket,
            "objects": [o.to_dict() for o in self.objects],
        }

    def summary(self) -> str:
        return (
            f"Archive complete: {len(self.uploaded)} uploaded, "
            f"{len(self.skipped)} already present (skipped), "
            f"into bucket '{self.bucket}' under '{RAW_PREFIX}/'."
        )


def sha256_of_bytes(data: bytes) -> str:
    """Return the hex SHA-256 digest of ``data``."""
    return hashlib.sha256(data).hexdigest()


def sha256_of_file(path: Path) -> str:
    """Return the hex SHA-256 digest of a file's bytes, read in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _coerce_source(item: RoutedFile | str | Path) -> Path:
    """Resolve an input item to the local path of the original to archive."""
    if isinstance(item, RoutedFile):
        return Path(item.path)
    return Path(item)


class Archiver:
    """Uploads originals to S3 under ``raw/`` with idempotent re-archival.

    Args:
        bucket: Destination S3 bucket (must reside in ``region``).
        s3_client: Injectable boto3 S3 client (moto in tests). Lazily created
            from ``region`` when omitted.
        region: AWS region for a lazily-created client (default ``af-south-1``;
            Req 12.2, 14.4).
    """

    def __init__(
        self,
        *,
        bucket: str,
        s3_client: Any | None = None,
        region: str = DEFAULT_REGION,
    ) -> None:
        if not bucket:
            raise ValueError("Archiver requires a destination S3 bucket")
        self._bucket = bucket
        self._s3 = s3_client
        self._region = region

    @property
    def bucket(self) -> str:
        return self._bucket

    # ------------------------------------------------------------------ #
    # Lazy client construction (only when a real client was not injected)
    # ------------------------------------------------------------------ #
    @property
    def s3(self) -> Any:
        if self._s3 is None:
            import boto3

            # boto3 uses HTTPS/TLS by default -> encryption in transit (Req 14.3).
            self._s3 = boto3.client("s3", region_name=self._region)
        return self._s3

    # ------------------------------------------------------------------ #
    # Public entry points
    # ------------------------------------------------------------------ #
    def archive(self, files: Iterable[RoutedFile | str | Path]) -> ArchiveResult:
        """Archive each original, skipping any already present with same hash.

        Args:
            files: Routed files from Collect and/or direct paths. The ORIGINAL
                bytes are read from disk for each and uploaded under
                ``archive_key(source)``.

        Returns:
            An :class:`ArchiveResult` describing per-file uploaded/skipped
            outcomes.
        """
        objects = [self.archive_file(item) for item in files]
        return ArchiveResult(objects=tuple(objects), bucket=self._bucket)

    def archive_file(self, item: RoutedFile | str | Path) -> ArchivedObject:
        """Archive a single original file, returning its outcome.

        Uploads the file byte-for-byte under its deterministic key, storing the
        content SHA-256 as object metadata. If the key already holds an object
        whose stored hash matches, the upload is skipped (idempotent).
        """
        local_path = _coerce_source(item)
        key = archive_key(str(local_path))
        digest = sha256_of_file(local_path)

        existing = self._existing_hash(key)
        if existing is not None and existing == digest:
            # Already archived, unchanged -> no-op success (Req 12.3).
            return ArchivedObject(
                key=key,
                sha256=digest,
                source_path=str(local_path),
                status=ArchiveStatus.SKIPPED,
            )

        # Absent, or hash differs -> upload byte-for-byte unmodified (Req 12.1, 12.3).
        with local_path.open("rb") as handle:
            body = handle.read()
        self.s3.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=body,
            Metadata={SHA256_METADATA_KEY: digest},
        )
        return ArchivedObject(
            key=key,
            sha256=digest,
            source_path=str(local_path),
            status=ArchiveStatus.UPLOADED,
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _existing_hash(self, key: str) -> str | None:
        """Return the stored SHA-256 for ``key``, or ``None`` if absent.

        Uses ``head_object`` (a cheap metadata lookup). A missing object raises
        a ``ClientError`` with a 404/NoSuchKey code, which is treated as absent.
        Any other error propagates.
        """
        try:
            response = self.s3.head_object(Bucket=self._bucket, Key=key)
        except Exception as exc:  # noqa: BLE001 - narrowed below via error code
            if _is_not_found(exc):
                return None
            raise
        metadata = response.get("Metadata", {}) or {}
        # S3 returns user-metadata keys lowercased.
        return metadata.get(SHA256_METADATA_KEY)


def _is_not_found(exc: Exception) -> bool:
    """True when an S3 exception indicates the object does not exist."""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        error = response.get("Error", {}) or {}
        code = str(error.get("Code", ""))
        status = str(
            response.get("ResponseMetadata", {}).get("HTTPStatusCode", "")
        )
        if code in {"404", "NoSuchKey", "NotFound"} or status == "404":
            return True
    # boto3 exposes exceptions.ClientError; fall back to class name heuristics.
    return exc.__class__.__name__ in {"NoSuchKey", "404", "ClientError"} and (
        "404" in str(exc) or "Not Found" in str(exc)
    )
