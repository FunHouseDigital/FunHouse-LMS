"""Unit tests for the Archive component (Task 13.1).

Exercises byte-for-byte upload, SHA-256 object metadata, idempotent
re-archival via ``head_object``, and acceptance of both Collect ``RoutedFile``
inputs and direct filesystem paths -- all against a **moto-backed S3** (no live
AWS), per the design's Testing Strategy.
"""

from __future__ import annotations

from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from funhouse_pipeline.archive import (
    RAW_PREFIX,
    ArchiveStatus,
    Archiver,
    SHA256_METADATA_KEY,
    archive_key,
    sha256_of_bytes,
)
from funhouse_pipeline.collect import HandlerTarget, RoutedFile

_REGION = "af-south-1"
_BUCKET = "funhouse-archive-test"


def _make_bucket(s3) -> None:
    s3.create_bucket(
        Bucket=_BUCKET,
        CreateBucketConfiguration={"LocationConstraint": _REGION},
    )


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@mock_aws
def test_uploads_original_byte_for_byte_with_hash_metadata(tmp_path):
    s3 = boto3.client("s3", region_name=_REGION)
    _make_bucket(s3)

    data = b"\x00\x01original card bytes\xff\xfe"
    src = _write(tmp_path / "cards" / "card1.png", data)

    archiver = Archiver(bucket=_BUCKET, s3_client=s3, region=_REGION)
    result = archiver.archive([src])

    assert len(result.uploaded) == 1
    assert result.skipped == []
    obj = result.objects[0]
    assert obj.status is ArchiveStatus.UPLOADED
    assert obj.key == "raw/cards/card1.png" == archive_key(str(src))

    stored = s3.get_object(Bucket=_BUCKET, Key=obj.key)
    body = stored["Body"].read()
    # Byte-for-byte preservation (Req 12.3).
    assert body == data
    # SHA-256 stored as object metadata (lowercased key).
    assert stored["Metadata"][SHA256_METADATA_KEY] == sha256_of_bytes(data)


@mock_aws
def test_rearchival_with_matching_hash_is_skipped(tmp_path):
    s3 = boto3.client("s3", region_name=_REGION)
    _make_bucket(s3)

    src = _write(tmp_path / "sheets" / "s.pdf", b"attendance sheet")
    archiver = Archiver(bucket=_BUCKET, s3_client=s3, region=_REGION)

    first = archiver.archive_file(src)
    assert first.status is ArchiveStatus.UPLOADED

    # Re-archive the unchanged file -> idempotent no-op skip (Req 12.3).
    second = archiver.archive_file(src)
    assert second.status is ArchiveStatus.SKIPPED
    assert second.key == first.key
    assert second.sha256 == first.sha256


@mock_aws
def test_changed_content_triggers_reupload(tmp_path):
    s3 = boto3.client("s3", region_name=_REGION)
    _make_bucket(s3)

    src = _write(tmp_path / "photos" / "p.jpg", b"version one")
    archiver = Archiver(bucket=_BUCKET, s3_client=s3, region=_REGION)
    archiver.archive_file(src)

    # Same key, different bytes -> hash differs -> re-upload.
    src.write_bytes(b"version two - changed")
    outcome = archiver.archive_file(src)
    assert outcome.status is ArchiveStatus.UPLOADED

    stored = s3.get_object(Bucket=_BUCKET, Key=outcome.key)["Body"].read()
    assert stored == b"version two - changed"


@mock_aws
def test_accepts_routed_file_inputs(tmp_path):
    s3 = boto3.client("s3", region_name=_REGION)
    _make_bucket(s3)

    src = _write(tmp_path / "lessons" / "week1.docx", b"lesson bytes")
    routed = RoutedFile(
        path=src,
        subfolder="lessons",
        source_type="lesson documents",
        handler=HandlerTarget.DOCX_TEXT_PARSER,
    )

    archiver = Archiver(bucket=_BUCKET, s3_client=s3, region=_REGION)
    obj = archiver.archive_file(routed)

    assert obj.key == "raw/lessons/week1.docx"
    assert obj.key.startswith(f"{RAW_PREFIX}/")
    assert s3.get_object(Bucket=_BUCKET, Key=obj.key)["Body"].read() == b"lesson bytes"


@mock_aws
def test_archive_result_reports_mixed_outcomes(tmp_path):
    s3 = boto3.client("s3", region_name=_REGION)
    _make_bucket(s3)

    a = _write(tmp_path / "cards" / "a.png", b"aaa")
    b = _write(tmp_path / "cards" / "b.png", b"bbb")
    archiver = Archiver(bucket=_BUCKET, s3_client=s3, region=_REGION)

    archiver.archive_file(a)  # pre-archive one file
    result = archiver.archive([a, b])  # a is skipped, b uploaded

    assert {o.status for o in result.objects} == {
        ArchiveStatus.SKIPPED,
        ArchiveStatus.UPLOADED,
    }
    assert len(result.skipped) == 1
    assert len(result.uploaded) == 1
    assert set(result.keys()) == {"raw/cards/a.png", "raw/cards/b.png"}


def test_empty_bucket_rejected():
    with pytest.raises(ValueError):
        Archiver(bucket="")
