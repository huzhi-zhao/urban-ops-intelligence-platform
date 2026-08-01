"""
Test 2 — Small Upload Test (integration, requires MinIO)

Uploads a tiny (2-record) payload to Bronze and verifies the full round-trip:
- the data object is written, is really gzipped, and decompresses to the
  records that went in
- the manifest object is written uncompressed and its fields are populated
- a re-run overwrites in place rather than accumulating objects

Run (requires the S3_* variables from .env):
    python -m pytest tests/integration/test_small_upload.py -v

Safe to re-run: it overwrites the same two objects each time.
"""

from __future__ import annotations

import time
from datetime import datetime

from ingestion.loaders.s3_loader import S3BronzeLoader
from tests.integration.conftest import object_exists, read_json, read_ndjson

# Minimal 2-record payload
SMALL_PAYLOAD = [
    {
        "unique_key": "TEST001",
        "created_date": "2026-03-01T09:00:00.000",
        "complaint_type": "Test Complaint",
        "descriptor": "Test descriptor",
        "borough": "MANHATTAN",
        "incident_zip": "10001",
        "latitude": "40.714",
        "longitude": "-74.006",
        "status": "Closed",
    },
    {
        "unique_key": "TEST002",
        "created_date": "2026-03-15T14:30:00.000",
        "complaint_type": "Test Heating",
        "descriptor": "No heat",
        "borough": "BROOKLYN",
        "incident_zip": "11213",
        "latitude": "40.678",
        "longitude": "-73.944",
        "status": "Open",
    },
]

TEST_SOURCE = "SRC-NYC-311"
TEST_DATASET = "nyc_311"
TEST_MONTH = "2026-03"

DATA_KEY = f"bronze/raw/{TEST_SOURCE}/{TEST_DATASET}/data_{TEST_MONTH}.ndjson.gz"
MANIFEST_KEY = f"bronze/raw/{TEST_SOURCE}/{TEST_DATASET}/manifest_{TEST_MONTH}.json"


def _loader(bucket: str, s3_client) -> S3BronzeLoader:
    return S3BronzeLoader(
        bucket_name=bucket, timestamp_field="created_date", client=s3_client,
    )


def test_small_upload_writes_data_and_manifest(bucket, s3_client):
    manifest = _loader(bucket, s3_client).write_monthly_shard(
        source_id=TEST_SOURCE,
        dataset_name=TEST_DATASET,
        month_partition=TEST_MONTH,
        records=SMALL_PAYLOAD,
    )

    assert manifest.record_count == 2
    assert manifest.month_partition == TEST_MONTH
    assert manifest.filename == f"data_{TEST_MONTH}.ndjson.gz"
    assert manifest.sha256_checksum
    assert manifest.data_date_min == "2026-03-01"
    assert manifest.data_date_max == "2026-03-15"
    assert manifest.fetch_timestamp

    assert object_exists(s3_client, bucket, DATA_KEY), f"Data not found: {DATA_KEY}"
    assert object_exists(s3_client, bucket, MANIFEST_KEY), f"Manifest not found: {MANIFEST_KEY}"


def test_uploaded_data_decompresses_to_the_records_that_went_in(bucket, s3_client):
    """The check a mock cannot make: gzip on the way out, records on the way back."""
    _loader(bucket, s3_client).write_monthly_shard(
        source_id=TEST_SOURCE,
        dataset_name=TEST_DATASET,
        month_partition=TEST_MONTH,
        records=SMALL_PAYLOAD,
    )

    round_tripped = read_ndjson(s3_client, bucket, DATA_KEY)

    assert round_tripped == SMALL_PAYLOAD


def test_stored_object_is_smaller_than_the_payload_it_encodes(bucket, s3_client):
    """gzip is a precondition, not an optimisation (ADR 0006 §4)."""
    manifest = _loader(bucket, s3_client).write_monthly_shard(
        source_id=TEST_SOURCE,
        dataset_name=TEST_DATASET,
        month_partition=TEST_MONTH,
        records=SMALL_PAYLOAD,
    )

    head = s3_client.head_object(Bucket=bucket, Key=DATA_KEY)

    assert manifest.compression == "gzip"
    assert head["ContentLength"] == manifest.stored_bytes
    assert manifest.stored_bytes < manifest.file_size_bytes
    # Content-Encoding must stay unset — some clients would double-decompress.
    assert "ContentEncoding" not in head


def test_reupload_overwrites_in_place_and_refreshes_fetch_timestamp(bucket, s3_client):
    loader = _loader(bucket, s3_client)

    first = loader.write_monthly_shard(
        source_id=TEST_SOURCE, dataset_name=TEST_DATASET,
        month_partition=TEST_MONTH, records=SMALL_PAYLOAD,
    )
    time.sleep(1.1)  # fetch_timestamp has second resolution
    second = loader.write_monthly_shard(
        source_id=TEST_SOURCE, dataset_name=TEST_DATASET,
        month_partition=TEST_MONTH, records=SMALL_PAYLOAD,
    )

    assert datetime.fromisoformat(second.fetch_timestamp) > datetime.fromisoformat(
        first.fetch_timestamp,
    )
    # Same records ⇒ same checksum: it describes the payload, not the object.
    assert second.sha256_checksum == first.sha256_checksum

    stored = read_json(s3_client, bucket, MANIFEST_KEY)
    assert stored["fetch_timestamp"] == second.fetch_timestamp
    assert stored["source_id"] == TEST_SOURCE
    assert stored["record_count"] == 2
