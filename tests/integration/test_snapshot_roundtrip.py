"""
Test 5 — Snapshot streaming round-trip (integration, requires MinIO)

The other integration tests cover the ``daily`` / ``monthly`` write paths, which
serialise a materialised list. Snapshot collection does not use any of them: it
goes through :meth:`S3BronzeLoader.write_snapshot_stream`, which gzips a
generator straight into a temp file and hands that file to ``upload_fileobj``.
Until this module existed, that path — the one carrying the only data in the
project that cannot be re-fetched — had been exercised by mocks only.

What is verified here, and why each item is here rather than in a unit test:

1. **Round-trip.** Upload → download → gunzip → compare. A mock asserts that
   ``put_object`` was called with certain bytes; it cannot catch a real object
   that decompresses to something else (ADR 0006 §4.1).
2. **Manifest semantics against the stored object.** ``file_size_bytes`` /
   ``sha256_checksum`` describe the *uncompressed* payload, ``stored_bytes``
   describes the object. Only a real ``head_object`` can confirm the second
   half of that contract, and the layout is frozen (AGENTS.md → Manifest
   contract).
3. **Multipart upload.** The real snapshot is ~18.5 MB and crosses boto3's 8 MB
   multipart threshold, so it is written with a different set of S3 actions than
   a small object. The storage node's credential is scoped down to just what the
   collector needs, and "the policy forgot ``CreateMultipartUpload``" is a
   failure that only appears at full size, in production, on the day it matters.
4. **The small-pull guard leaves the previous partition byte-identical.** The
   guard's whole purpose is that a bad upstream day does not damage a good one.
   That is a statement about what is in the bucket afterwards.

Run (requires the S3_* variables from .env):
    python -m pytest tests/integration/test_snapshot_roundtrip.py -v

Safe to re-run: everything is written under a dedicated test source prefix with
a fixed 1970 ingest date, so it can never collide with — or overwrite — a real
snapshot partition. Use an unrestricted development credential; the collector's
own scoped key deliberately cannot write here.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest

from ingestion.loaders.s3_loader import S3BronzeLoader, SnapshotTooSmallError
from tests.integration.conftest import object_exists, read_bytes, read_json, read_ndjson

# A dedicated prefix, never a real source: a stray write under
# SRC-WPG-SNOW/snow_clearing_status/ would overwrite a day of history that
# cannot be re-collected. The 1970 date makes a collision impossible even so.
TEST_SOURCE = "SRC-TEST-SNAPSHOT"
TEST_DATASET = "snapshot_roundtrip"
TEST_DATE = date(1970, 1, 1)

PREFIX = f"bronze/raw/{TEST_SOURCE}/{TEST_DATASET}/ingest_date={TEST_DATE.isoformat()}"
DATA_KEY = f"{PREFIX}/data.ndjson.gz"
MANIFEST_KEY = f"{PREFIX}/manifest.json"

# Mirrors the real upstream: flat records, no timestamp field of any kind.
SNAPSHOT_PAYLOAD = [
    {"address": "123 Main St", "has_street": "true", "has_alley": "false", "has_walk": "true"},
    {"address": "456 Portage Ave", "has_street": "false", "has_alley": "false", "has_walk": "false"},
    {"address": "789 Rue Deschambault", "has_street": "true", "has_alley": "true", "has_walk": "true"},
]


def _loader(bucket: str, s3_client: Any) -> S3BronzeLoader:
    """A loader configured the way SnapshotCollector configures it.

    ``timestamp_field=""`` is not an oversight — a snapshot source has no time
    field at all, which is why ``snapshot`` is the only strategy allowing
    ``timestamp_field: null`` in the source YAML.
    """
    return S3BronzeLoader(bucket_name=bucket, timestamp_field="", client=s3_client)


def _stream(records: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """Yield records one at a time, as the paginated upstream fetch does."""
    yield from records


def _ndjson_bytes(records: list[dict[str, Any]]) -> bytes:
    """The exact uncompressed payload the loader is expected to have hashed."""
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records).encode("utf-8")


@pytest.fixture
def written(bucket: str, s3_client: Any):
    """One collected snapshot day, freshly written before each test."""
    return _loader(bucket, s3_client).write_snapshot_stream(
        source_id=TEST_SOURCE,
        dataset_name=TEST_DATASET,
        ingest_date=TEST_DATE,
        records=_stream(SNAPSHOT_PAYLOAD),
        min_records=1,
    )


def test_snapshot_writes_a_paired_data_object_and_manifest(written, bucket, s3_client):
    """The pair is the acceptance criterion for a collected day (design §5.3)."""
    assert object_exists(s3_client, bucket, DATA_KEY), f"Data not found: {DATA_KEY}"
    assert object_exists(s3_client, bucket, MANIFEST_KEY), f"Manifest not found: {MANIFEST_KEY}"
    assert written.record_count == len(SNAPSHOT_PAYLOAD)
    assert written.filename == "data.ndjson.gz"


def test_streamed_data_decompresses_to_the_records_that_went_in(written, bucket, s3_client):
    """The check a mock cannot make: gzip on the way out, records on the way back."""
    assert read_ndjson(s3_client, bucket, DATA_KEY) == SNAPSHOT_PAYLOAD


def test_manifest_describes_the_uncompressed_payload(written, bucket, s3_client):
    """`file_size_bytes` / `sha256_checksum` cover the payload, not the object.

    This is what makes "same records ⇒ same checksum" hold across compression
    settings, and it is a frozen on-disk contract — snapshot history cannot be
    rewritten, so a manifest whose fields mean something else is permanent.
    """
    expected = _ndjson_bytes(SNAPSHOT_PAYLOAD)

    assert written.file_size_bytes == len(expected)
    assert written.sha256_checksum == hashlib.sha256(expected).hexdigest()
    assert read_bytes(s3_client, bucket, DATA_KEY) == expected

    stored = read_json(s3_client, bucket, MANIFEST_KEY)
    assert stored["sha256_checksum"] == written.sha256_checksum
    assert stored["file_size_bytes"] == written.file_size_bytes
    assert stored["record_count"] == len(SNAPSHOT_PAYLOAD)
    # No time field upstream, so no data date range — see _loader().
    assert stored["data_date_min"] is None
    assert stored["data_date_max"] is None


def test_manifest_stored_bytes_matches_the_object_actually_written(
    written, bucket, s3_client,
):
    """`stored_bytes` / `compression` describe the object; verify against S3 itself."""
    head = s3_client.head_object(Bucket=bucket, Key=DATA_KEY)

    assert written.compression == "gzip"
    assert head["ContentLength"] == written.stored_bytes
    assert written.stored_bytes < written.file_size_bytes
    # Content-Encoding must stay unset: Spark picks its codec from the file
    # extension and some clients would otherwise decompress a second time.
    assert "ContentEncoding" not in head


def test_manifest_is_stored_uncompressed_and_readable(bucket, s3_client, written):
    """Manifests stay plain JSON — being able to `head` one is worth the bytes."""
    raw = s3_client.get_object(Bucket=bucket, Key=MANIFEST_KEY)["Body"].read()

    assert json.loads(raw)["source_id"] == TEST_SOURCE


def test_small_pull_leaves_the_previous_day_byte_identical(written, bucket, s3_client):
    """A bad upstream day must not damage a good partition already written.

    The guard is only meaningful if nothing reaches the bucket when it trips —
    including a truncated object from a partially-completed upload.
    """
    before = read_bytes(s3_client, bucket, DATA_KEY)
    before_manifest = read_json(s3_client, bucket, MANIFEST_KEY)

    with pytest.raises(SnapshotTooSmallError):
        _loader(bucket, s3_client).write_snapshot_stream(
            source_id=TEST_SOURCE,
            dataset_name=TEST_DATASET,
            ingest_date=TEST_DATE,
            records=_stream(SNAPSHOT_PAYLOAD[:1]),
            min_records=1000,
        )

    assert read_bytes(s3_client, bucket, DATA_KEY) == before
    assert read_json(s3_client, bucket, MANIFEST_KEY) == before_manifest


@pytest.mark.slow
def test_object_larger_than_the_multipart_threshold_round_trips(bucket, s3_client):
    """~10 MB of incompressible data, to exercise the multipart upload path.

    The production snapshot is ~18.5 MB stored and crosses boto3's 8 MB
    threshold, at which ``upload_fileobj`` stops issuing a single PUT and starts
    a multipart upload — a different set of S3 actions. A credential scoped to
    ``PutObject`` alone passes every other test in this file and then fails on
    the real dataset. The payload has to be genuinely incompressible: gzip would
    otherwise squeeze repetitive filler well below the threshold and quietly
    turn this back into a single-part upload.
    """
    big_date = date(1970, 1, 2)
    big_prefix = f"bronze/raw/{TEST_SOURCE}/{TEST_DATASET}/ingest_date={big_date.isoformat()}"
    big_key = f"{big_prefix}/data.ndjson.gz"

    def incompressible(n: int) -> Iterator[dict[str, Any]]:
        for i in range(n):
            yield {"address": f"row-{i}", "payload": os.urandom(512).hex()}

    try:
        manifest = _loader(bucket, s3_client).write_snapshot_stream(
            source_id=TEST_SOURCE,
            dataset_name=TEST_DATASET,
            ingest_date=big_date,
            records=incompressible(20_000),
            min_records=1,
        )

        head = s3_client.head_object(Bucket=bucket, Key=big_key)

        assert manifest.stored_bytes > 8 * 1024 * 1024, (
            "payload compressed below the multipart threshold — the test no longer "
            "covers what it claims to"
        )
        assert head["ContentLength"] == manifest.stored_bytes
        # A multipart ETag is "<hash>-<partcount>"; a single PUT has no dash.
        # This is the assertion that the multipart actions were permitted.
        assert "-" in head["ETag"].strip('"'), "object was not uploaded as multipart"

        # Reading it back is the same round-trip guarantee, at a size where the
        # object was assembled from parts rather than written whole.
        round_tripped = read_ndjson(s3_client, bucket, big_key)
        assert len(round_tripped) == 20_000
        assert manifest.record_count == 20_000
    finally:
        # ~10 MB of random bytes has no reason to stay in the bucket. Only this
        # test's own throwaway partition is removed; nothing else here deletes.
        for key in (big_key, f"{big_prefix}/manifest.json"):
            s3_client.delete_object(Bucket=bucket, Key=key)
