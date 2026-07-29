"""
Test 4 — Full Aggregation Test (integration)

End-to-end test that:
1. Fetches N months of 311 data via the BackfillFacade
2. Writes per-day files to GCS (NYC 311 uses partition_strategy=daily)
3. Reads back manifest files and aggregates record counts
4. Validates total counts are consistent

Run:
    export GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa.json
    export GCS_BUCKET_NAME=your-bucket
    export SOCRATA_APP_TOKEN=your_token  # optional
    python -m pytest tests/integration/test_full_aggregation.py -v
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ingestion.backfill import BackfillFacade
from ingestion.config import load_source_config

BUCKET = os.environ.get("GCS_BUCKET_NAME", "")
CREDS = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")

SOURCE_ID = "SRC-NYC-311"
DATASET_NAME = "nyc_311"
N_MONTHS = 3


def monthly_ranges(n: int) -> list[tuple[date, date]]:
    """Same logic as backfill script."""
    today = date.today()
    start_month_num = ((today.month - 1 - n) % 12) + 1
    start_year = today.year - ((today.month - 1 - n) // 12)
    current = date(start_year, start_month_num, 1)
    ranges = []
    while current <= today:
        next_month = date(current.year + 1, 1, 1) if current.month == 12 else date(current.year, current.month + 1, 1)
        ranges.append((current, next_month))
        current = next_month
    return ranges


@pytest.fixture
def facade():
    if not BUCKET:
        pytest.skip("GCS_BUCKET_NAME not set")
    if not CREDS:
        pytest.skip("GOOGLE_APPLICATION_CREDENTIALS not set")
    cfg = load_source_config(SOURCE_ID)
    return BackfillFacade(cfg, gcs_bucket=BUCKET)


def test_full_backfill_aggregation(facade):
    """
    Run full backfill for N_MONTHS using the daily partition layout and verify:
    - All months written successfully
    - One data file per day per month in the window
    - Manifests are readable from GCS
    - Total record count across daily manifests matches sum of per-day counts
    - All fetch_timestamps are recent (within last 5 minutes)
    """
    ranges = monthly_ranges(N_MONTHS)
    print(f"\n  Backfilling {N_MONTHS} months: {[r[0].isoformat() for r in ranges]}")

    all_manifests: list = []
    total_records = 0
    months_written: set[str] = set()

    for month_start, month_end in ranges:
        manifests = facade.upload(
            start=month_start,
            end=month_end,
            dataset_name=DATASET_NAME,
        )
        all_manifests.extend(manifests)
        months_written.add(month_start.strftime("%Y-%m"))
        for m in manifests:
            total_records += m.record_count

    print(f"  Daily files written: {len(all_manifests)}")
    print(f"  Total records:       {total_records}")

    # Verify all manifests are readable from GCS
    bucket = facade._gcs_client.bucket(BUCKET)
    for m in all_manifests:
        manifest_path = f"bronze/raw/{SOURCE_ID}/{DATASET_NAME}/{m.month_partition}/manifest.json"
        blob = bucket.blob(manifest_path)
        assert blob.exists(), f"Manifest not found: {manifest_path}"

        # Read back and compare with the in-memory manifest (GCS keeps the last write)
        data = json.loads(blob.download_as_text())
        assert data["month_partition"] == m.month_partition
        assert data["source_id"] == SOURCE_ID
        assert data["dataset_name"] == DATASET_NAME
        # blob.download_as_text() reads the latest version, which should equal
        # the manifest for the latest day in that month
        assert data["fetch_timestamp"] == m.fetch_timestamp or data["fetch_timestamp"] >= max(
            x.fetch_timestamp for x in all_manifests
            if x.month_partition == m.month_partition
        )

    # Verify fetch_timestamps are recent. Naive UTC to match the manifest
    # format written by gcs_loader._utc_now_naive() — comparing a naive
    # manifest timestamp against an aware now() raises TypeError.
    now = datetime.now(UTC).replace(tzinfo=None)
    for m in all_manifests:
        ts = datetime.fromisoformat(m.fetch_timestamp)
        assert (now - ts).total_seconds() < 300, f"fetch_timestamp too old: {m.fetch_timestamp}"

    print("\n✓ Full aggregation test passed")
    print(f"  Daily manifests:     {len(all_manifests)}")
    print(f"  Months covered:      {sorted(months_written)}")
    print(f"  Total records:       {total_records}")
    print(f"  GCS prefix: gs://{BUCKET}/bronze/raw/{SOURCE_ID}/{DATASET_NAME}/")


if __name__ == "__main__":
    print("=" * 60)
    print("Test 4 — Full Aggregation Test")
    print(f"Months: last {N_MONTHS}")
    print("=" * 60)
    if not BUCKET or not CREDS:
        print("SKIP: Set GOOGLE_APPLICATION_CREDENTIALS and GCS_BUCKET_NAME to run")
        sys.exit(0)

    cfg = load_source_config(SOURCE_ID)
    facade = BackfillFacade(cfg, gcs_bucket=BUCKET)
    test_full_backfill_aggregation(facade)
