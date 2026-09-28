"""
Test 3 — Single Month Retrieval Test (integration)

Fetches one full month of the service-request source from its live Socrata API
and writes it to object storage. This validates the complete fetch → write
pipeline for a single partition.

The source is ``partition_strategy: daily`` — records are split by their
timestamp field into per-day files inside the month folder, each with its own
``manifest_YYYY-MM-DD.json``.

🔴 It writes under ``INTEGRATION_SOURCE_ID``, never under the real source's
prefix: Bronze is immutable, and this test would otherwise overwrite a genuine
month of it on every run.

Run (requires the S3_* variables from .env; SOCRATA_APP_TOKEN optional):
    python -m pytest tests/integration/test_single_month.py -v

Uses a hard-coded past month to keep results stable and reproducible.
"""

from __future__ import annotations

from datetime import date

import pytest

from ingestion.backfill import BackfillFacade
from tests.integration.conftest import (
    INTEGRATION_SOURCE_ID,
    live_source_under_test_prefix,
    object_exists,
)

# Registered source to fetch from; its id is config data, quoted verbatim.
SOURCE_ID = "SRC-WPG-311"
DATASET_NAME = "service_requests"
TEST_MONTH = "2026-02"

# [start, end) of the test month
TEST_START = date(2026, 2, 1)
TEST_END = date(2026, 3, 1)

PREFIX = f"bronze/raw/{INTEGRATION_SOURCE_ID}/{DATASET_NAME}/{TEST_MONTH}"


@pytest.fixture(scope="module")
def manifests(bucket, s3_client) -> list:
    """Fetch the month once and share the result across the assertions below.

    Module-scoped on purpose: a full month is one slow upstream call, and
    four tests asserting different properties of the same write should not mean
    four fetches.
    """
    facade = BackfillFacade(
        live_source_under_test_prefix(SOURCE_ID), bucket=bucket, client=s3_client,
    )
    written = facade.upload_window(
        start=TEST_START, end=TEST_END, dataset_name=DATASET_NAME, strategy="daily",
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
