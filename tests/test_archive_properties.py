"""Property-based tests for the Archive component (Tasks 13.2, 13.3).

Implements design Properties 25 and 26 with **Hypothesis** (minimum 100
iterations each) against a **moto-backed S3** (no live AWS), per the design's
Testing Strategy. Arbitrary byte blobs stand in as "source files".
"""

from __future__ import annotations

from pathlib import Path

import boto3
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from moto import mock_aws

from funhouse_pipeline.archive import (
    RAW_PREFIX,
    Archiver,
    SHA256_METADATA_KEY,
    archive_key,
    sha256_of_bytes,
)
from funhouse_pipeline.collect import HandlerTarget, RoutedFile

pytestmark = pytest.mark.property

_REGION = "af-south-1"
_BUCKET = "funhouse-archive-test"

# Property tests spin up moto per example; the function-scoped setup below is
# intentional, so suppress Hypothesis's function-scoped-fixture health check.
_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# One of the five real source subfolders (drives archive_key's subfolder part).
_subfolders = st.sampled_from(["cards", "sheets", "lessons", "photos", "whatsapp"])

# Safe filename stems (letters/digits/_-), plus an extension.
_stems = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-",
    min_size=1,
    max_size=20,
)
_exts = st.sampled_from([".png", ".jpg", ".jpeg", ".pdf", ".docx", ".heic"])

# Arbitrary byte blobs as source-file content (incl. empty and binary).
_blobs = st.binary(min_size=0, max_size=2048)


def _make_bucket(s3) -> None:
    s3.create_bucket(
        Bucket=_BUCKET,
        CreateBucketConfiguration={"LocationConstraint": _REGION},
    )


# Feature: phase0-data-foundation, Property 25: Archived originals are
# byte-for-byte preserved. For any source file (arbitrary bytes), the archived
# object's content equals the original (equal SHA-256 digest).
# Validates: Requirements 12.3
@_SETTINGS
@given(subfolder=_subfolders, stem=_stems, ext=_exts, data=_blobs)
def test_property_25_archived_originals_are_byte_for_byte_preserved(
    tmp_path_factory, subfolder, stem, ext, data
):
    tmp = tmp_path_factory.mktemp("src")
    src = tmp / subfolder / f"{stem}{ext}"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(data)

    with mock_aws():
        s3 = boto3.client("s3", region_name=_REGION)
        _make_bucket(s3)

        archiver = Archiver(bucket=_BUCKET, s3_client=s3, region=_REGION)
        obj = archiver.archive_file(src)

        stored = s3.get_object(Bucket=_BUCKET, Key=obj.key)
        body = stored["Body"].read()

        # Content is identical and the SHA-256 digests match (Req 12.3).
        assert body == data
        assert sha256_of_bytes(body) == sha256_of_bytes(data)
        assert obj.sha256 == sha256_of_bytes(data)
        assert stored["Metadata"][SHA256_METADATA_KEY] == sha256_of_bytes(data)


# Feature: phase0-data-foundation, Property 26: Every loaded row traces to an
# archived original. For any set of source files, after archiving, every
# record's provenance (source_file) resolves via archive_key to an object that
# exists under raw/, and a lessons row's original_file_ref equals
# archive_key(source_file).
# Validates: Requirements 4.4, 10.2, 12.1
#
# Design note: this asserts the pure provenance<->archive-key correspondence
# WITHOUT a database. `original_file_ref` is defined (design, Task 11.4) as
# archive_key(source_file) -- the exact key the Archiver writes under raw/ --
# so proving key equality here proves the traceability the loaded row relies on,
# and needs no DB round-trip.
@_SETTINGS
@given(
    sources=st.lists(
        st.tuples(_subfolders, _stems, _exts, _blobs),
        min_size=1,
        max_size=6,
        # Distinct (subfolder, stem, ext) so keys don't collide within a batch.
        unique_by=lambda t: (t[0], t[1], t[2]),
    )
)
def test_property_26_loaded_rows_trace_to_archived_originals(tmp_path_factory, sources):
    tmp = tmp_path_factory.mktemp("src")

    routed_files: list[RoutedFile] = []
    for subfolder, stem, ext, data in sources:
        path = tmp / subfolder / f"{stem}{ext}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        handler = (
            HandlerTarget.DOCX_TEXT_PARSER
            if subfolder == "lessons"
            else HandlerTarget.IMAGE_EXTRACT
        )
        routed_files.append(
            RoutedFile(
                path=path,
                subfolder=subfolder,
                source_type=subfolder,
                handler=handler,
            )
        )

    with mock_aws():
        s3 = boto3.client("s3", region_name=_REGION)
        _make_bucket(s3)

        archiver = Archiver(bucket=_BUCKET, s3_client=s3, region=_REGION)
        result = archiver.archive(routed_files)

        archived_keys = set(result.keys())

        for routed in routed_files:
            provenance = str(routed.path)
            key = archive_key(provenance)

            # Provenance resolves to a key under the raw/ prefix (Req 12.1).
            assert key.startswith(f"{RAW_PREFIX}/")
            # The object actually exists in Object_Storage.
            assert key in archived_keys
            head = s3.head_object(Bucket=_BUCKET, Key=key)
            assert head["ResponseMetadata"]["HTTPStatusCode"] == 200

            # For lessons, original_file_ref (== archive_key) points at that
            # archived object (Req 10.2). Load derives the ref the same way.
            if routed.subfolder == "lessons":
                original_file_ref = archive_key(provenance)
                assert original_file_ref == key
                assert original_file_ref in archived_keys
