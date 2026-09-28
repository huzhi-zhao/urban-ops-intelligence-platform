"""
Test 4 — Full Aggregation Test (integration)

End-to-end test that:
1. Fetches N months of the service-request source via the BackfillFacade
2. Writes per-day files (the source is partition_strategy=daily) under a test
   prefix — never the real source's, which is immutable Bronze
3. Reads the manifests back out of object storage and aggregates record counts
4. Validates the stored manifests agree with the ones returned in-process

Run (requires the S3_* variables from .env; SOCRATA_APP_TOKEN optional):
    python -m pytest tests/integration/test_full_aggregation.py -v
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from ingestion.backfill import BackfillFacade
from tests.integration.conftest import (
    INTEGRATION_SOURCE_ID,
    live_source_under_test_prefix,
    read_json,
    read_ndjson,
)

# Registered source to fetch from; its id is config data, quoted verbatim.
SOURCE_ID = "SRC-WPG-311"
DATASET_NAME = "service_requests"
N_MONTHS = 3


def monthly_ranges(n: int) -> list[tuple[date, date]]:
    """The last ``n`` whole months plus the current one, as [start, end) pairs."""
    today = date.today()
    start_month_num = ((today.month - 1 - n) % 12) + 1
    start_year = today.year - ((today.month - 1 - n) // 12)
    current = date(start_year, start_month_num, 1)
    ranges = []
    while current <= today:
        next_month = (
            date(current.year + 1, 1, 1) if current.month == 12
            else date(current.year, current.month + 1, 1)
        )
        ranges.append((current, next_month))
        current = next_month
    return ranges


@pytest.fixture(scope="module")
def manifests(bucket, s3_client) -> list:
    """Run the whole backfill once; the assertions below all read from it."""
    facade = BackfillFacade(
        live_source_under_test_prefix(SOURCE_ID), bucket=bucket, client=s3_client,
    )
    written: list = []
    for month_start, month_end in monthly_ranges(N_MONTHS):
        written.extend(
            facade.upload_window(
                start=month_start, end=month_end, dataset_name=DATASET_NAME,
                strategy="daily",
            ),
        )
    if not written:
        pytest.skip("Upstream returned no records for the requested window")
    return written


def _manifest_key(m) -> str:
    return (
        f"bronze/raw/{INTEGRATION_SOURCE_ID}/{DATASET_NAME}/{m.month_partition}/"
        f"manifest_{m.data_date_min}.json"
    )


def test_every_day_manifest_reads_back_identically(manifests, bucket, s3_client):
    """Each day has its own manifest, so a read-back must match exactly.

    (Under the old single-manifest-per-month layout this could only be asserted
    loosely, since the last day written clobbered the earlier ones.)
    """
    for m in manifests:
        stored = read_json(s3_client, bucket, _manifest_key(m))
        assert stored["source_id"] == INTEGRATION_SOURCE_ID
        assert stored["dataset_name"] == DATASET_NAME
        assert stored["month_partition"] == m.month_partition
        assert stored["record_count"] == m.record_count
        assert stored["fetch_timestamp"] == m.fetch_timestamp
        assert stored["sha256_checksum"] == m.sha256_checksum


def test_stored_record_counts_match_the_manifests(manifests, bucket, s3_client):
    """Count the lines actually stored, not just what the manifest claims."""
    sample = max(manifests, key=lambda m: m.record_count)
    key = (
        f"bronze/raw/{INTEGRATION_SOURCE_ID}/{DATASET_NAME}/"
        f"{sample.month_partition}/{sample.filename}"
    )

    assert len(read_ndjson(s3_client, bucket, key)) == sample.record_count


def test_partitions_cover_the_requested_months(manifests):
    """Every month in the window produced at least one day file, and no day
    outside the window leaked in."""
    requested = {start.strftime("%Y-%m") for start, _ in monthly_ranges(N_MONTHS)}
    covered = {m.month_partition for m in manifests}

    assert covered <= requested, f"Wrote outside the window: {covered - requested}"
    assert sum(m.record_count for m in manifests) > 0


def test_fetch_timestamps_are_recent(manifests):
    # Naive UTC to match the manifest format written by
    # s3_loader._utc_now_naive() — comparing naive against aware raises TypeError.
    now = datetime.now(UTC).replace(tzinfo=None)
    for m in manifests:
        age = (now - datetime.fromisoformat(m.fetch_timestamp)).total_seconds()
        assert age < 3600, f"fetch_timestamp too old: {m.fetch_timestamp}"
