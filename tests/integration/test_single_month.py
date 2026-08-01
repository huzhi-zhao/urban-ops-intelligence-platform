"""
Test 3 — Single Month Retrieval Test (integration)

Fetches one full month of 311 data from the Socrata API and writes it to Bronze.
This validates the complete fetch → write pipeline for a single partition.

NYC 311 uses ``partition_strategy: daily`` — records are split by their
``created_date`` into per-day files inside the month folder, each with its own
``manifest_YYYY-MM-DD.json``.

Run (requires the S3_* variables from .env; SOCRATA_APP_TOKEN optional):
    python -m pytest tests/integration/test_single_month.py -v

Uses a hard-coded past month to keep results stable and reproducible.
"""

from __future__ import annotations

from datetime import date

import pytest

from ingestion.backfill import BackfillFacade
from ingestion.config import load_source_config
from tests.integration.conftest import object_exists

SOURCE_ID = "SRC-NYC-311"
DATASET_NAME = "nyc_311"
TEST_MONTH = "2026-02"

# [start, end) of the test month
TEST_START = date(2026, 2, 1)
TEST_END = date(2026, 3, 1)

PREFIX = f"bronze/raw/{SOURCE_ID}/{DATASET_NAME}/{TEST_MONTH}"


@pytest.fixture(scope="module")
def manifests(bucket, s3_client) -> list:
    """Fetch the month once and share the result across the assertions below.

    Module-scoped on purpose: a full month of 311 is one slow upstream call, and
    four tests asserting different properties of the same write should not mean
    four fetches.
    """
    facade = BackfillFacade(
        load_source_config(SOURCE_ID), bucket=bucket, client=s3_client,
    )
    written = facade.upload_window(
        start=TEST_START, end=TEST_END, dataset_name=DATASET_NAME,
    )
    if not written:
        pytest.skip(f"Upstream returned no records for {TEST_MONTH}")
    return written


def test_every_manifest_belongs_to_the_test_month(manifests):
    for m in manifests:
        assert m.month_partition == TEST_MONTH
        assert m.filename.startswith("data_2026-02-")
        assert m.filename.endswith(".ndjson.gz")
        assert m.fetch_timestamp


def test_each_daily_group_covers_exactly_one_date(manifests):
    """A day file that spans two dates means the grouping key is wrong."""
    for m in manifests:
        assert m.data_date_min == m.data_date_max


def test_each_day_has_a_paired_data_object_and_manifest(manifests, bucket, s3_client):
    for m in manifests:
        day = m.data_date_min
        assert object_exists(s3_client, bucket, f"{PREFIX}/{m.filename}"), (
            f"Data not found for {day}"
        )
        assert object_exists(s3_client, bucket, f"{PREFIX}/manifest_{day}.json"), (
            f"Manifest not found for {day}"
        )


def test_record_counts_are_positive(manifests):
    assert sum(m.record_count for m in manifests) > 0
    assert all(m.record_count > 0 for m in manifests)
