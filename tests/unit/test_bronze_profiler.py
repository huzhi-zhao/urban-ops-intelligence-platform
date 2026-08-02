"""Unit tests for scripts.profiling.bronze_profiler (no object-storage calls)."""

import gzip
import json
from unittest.mock import MagicMock

import pytest

from scripts.profiling.bronze_profiler import (
    Blob,
    Bucket,
    _download_json,
    _download_records,
    build_coverage_map,
    flatten_open_meteo,
    profile_records,
    render_markdown,
    sample_data_blobs,
)

# ── Object-storage helpers ────────────────────────────────────────────────────


def _client_with(objects: dict[str, bytes]) -> MagicMock:
    """Stub boto3 client serving a fixed key → body mapping."""
    client = MagicMock(name="s3_client")
    client.get_object.side_effect = lambda Bucket, Key: {  # noqa: N803
        "Body": MagicMock(read=lambda: objects[Key]),
    }
    paginator = MagicMock()
    paginator.paginate.side_effect = lambda Bucket, Prefix: [  # noqa: N803
        {"Contents": [{"Key": k} for k in sorted(objects) if k.startswith(Prefix)]},
    ]
    client.get_paginator.return_value = paginator
    return client


def _blob(client: MagicMock, key: str) -> Blob:
    return Blob(name=key, _client=client, _bucket="uoip")


def test_download_records_gunzips_and_parses_ndjson():
    body = gzip.compress(b'{"a": 1}\n{"a": 2}\n')
    client = _client_with({"data.ndjson.gz": body})

    assert _download_records(_blob(client, "data.ndjson.gz")) == [{"a": 1}, {"a": 2}]


def test_download_records_reads_uncompressed_ndjson():
    client = _client_with({"data.ndjson": b'{"a": 1}\n'})

    assert _download_records(_blob(client, "data.ndjson")) == [{"a": 1}]


def test_download_records_skips_blank_lines():
    client = _client_with({"data.ndjson": b'{"a": 1}\n\n{"a": 2}\n'})

    assert len(_download_records(_blob(client, "data.ndjson"))) == 2


def test_download_records_drops_a_corrupt_line_instead_of_aborting():
    """A partly-corrupt partition is exactly what the profiler exists to surface."""
    client = _client_with({"data.ndjson": b'{"a": 1}\nnot json\n{"a": 3}\n'})

    assert _download_records(_blob(client, "data.ndjson")) == [{"a": 1}, {"a": 3}]


def test_download_json_reads_an_uncompressed_manifest():
    client = _client_with({"manifest.json": json.dumps({"record_count": 7}).encode()})

    assert _download_json(_blob(client, "manifest.json"))["record_count"] == 7


def test_sample_data_blobs_selects_gzipped_data_files_only():
    prefix = "bronze/raw/SRC-X/ds/"
    client = _client_with({
        f"{prefix}2026-03/data_2026-03-01.ndjson.gz": b"",
        f"{prefix}2026-03/manifest_2026-03-01.json": b"",
    })

    picked = sample_data_blobs(Bucket(client, "uoip"), "SRC-X", "ds", n=3)

    assert [b.name for b in picked] == [f"{prefix}2026-03/data_2026-03-01.ndjson.gz"]


def test_sample_data_blobs_spreads_the_sample_across_available_files():
    prefix = "bronze/raw/SRC-X/ds/2026-03/"
    client = _client_with({
        f"{prefix}data_2026-03-{d:02d}.ndjson.gz": b"" for d in range(1, 10)
    })

    picked = sample_data_blobs(Bucket(client, "uoip"), "SRC-X", "ds", n=3)

    assert len(picked) == 3
    assert len({b.name for b in picked}) == 3


# ── build_coverage_map ────────────────────────────────────────────────────────

def test_coverage_empty():
    result = build_coverage_map([])
    assert result["total_records"] == 0
    assert result["partition_count"] == 0
    assert result["gaps"] == []


def test_coverage_basic():
    manifests = [
        {"month_partition": "2024-01", "record_count": 1000},
        {"month_partition": "2024-02", "record_count": 0},
        {"month_partition": "2024-03", "record_count": 10},   # well below 50% of median(505)
    ]
    result = build_coverage_map(manifests)
    assert result["total_records"] == 1010
    assert result["partition_count"] == 3
    assert "2024-02" in result["gaps"]
    # median([0,10,1000])=10, threshold=5; 10 is not < 5, so check a clearer anomaly
    # Instead verify the anomaly_low logic with a value clearly below 50% of median


def test_coverage_anomaly_low():
    # median([500, 500, 50]) = 500, threshold = 250; 50 is anomaly-low
    manifests = [
        {"month_partition": "2024-01", "record_count": 500},
        {"month_partition": "2024-02", "record_count": 500},
        {"month_partition": "2024-03", "record_count": 50},
    ]
    result = build_coverage_map(manifests)
    assert "2024-03" in result["anomaly_low"]
    assert result["gaps"] == []


def test_coverage_no_gaps_no_anomalies():
    manifests = [{"month_partition": f"2024-{i:02d}", "record_count": 500} for i in range(1, 4)]
    result = build_coverage_map(manifests)
    assert result["gaps"] == []
    assert result["anomaly_low"] == []


# ── profile_records ───────────────────────────────────────────────────────────

# Synthetic service-request records. Field and value names are generic on
# purpose: the profiler takes the region field as a parameter, so nothing here
# should name a real city's regions.
SAMPLE_REQUESTS = [
    {
        "unique_key": "1",
        "created_date": "2024-03-10T08:00:00",
        "region": "REGION ONE",
        "latitude": "40.7",
        "longitude": "-74.0",
        "agency": "DEPT-A",
    },
    {
        "unique_key": "2",
        "created_date": "2024-03-10T09:00:00",
        "region": "REGION TWO",
        "latitude": None,
        "longitude": None,
        "agency": None,
    },
    {
        "unique_key": "3",
        "created_date": None,      # missing timestamp
        "region": "R1",            # dirty value: an abbreviation, not canonical
        "latitude": "40.8",
        "longitude": "-73.9",
        "agency": "DEPT-B",
    },
]

# Canonical region names, lowercased — what the profiler compares against to
# decide a value is "dirty". Passed in per source rather than baked into the
# profiler, so no city's region list lives in the code.
VALID_REGIONS = {"region one", "region two"}


def test_profile_record_count():
    result = profile_records(SAMPLE_REQUESTS, "created_date", "region", VALID_REGIONS, [])
    assert result["record_count"] == 3


def test_null_rates():
    result = profile_records(SAMPLE_REQUESTS, "created_date", "region", VALID_REGIONS, [])
    null_rates = result["null_rates"]
    # agency: 1 of 3 null → ~0.33
    assert null_rates["agency"] == pytest.approx(1 / 3, rel=0.01)
    # unique_key: all present
    assert null_rates["unique_key"] == 0.0


def test_timestamp_missing():
    result = profile_records(SAMPLE_REQUESTS, "created_date", "region", VALID_REGIONS, [])
    ts = result["timestamp_analysis"]
    assert ts["missing_pct"] == pytest.approx(1 / 3, rel=0.01)
    assert ts["epoch_zero_count"] == 0
    assert ts["far_future_count"] == 0


def test_timestamp_epoch_zero():
    records = [{"ts": "1970-01-01T00:00:00"}, {"ts": "2024-01-01T00:00:00"}]
    result = profile_records(records, "ts", None, set(), [])
    assert result["timestamp_analysis"]["epoch_zero_count"] == 1


def test_timestamp_far_future():
    records = [{"ts": "2099-06-01T00:00:00"}]
    result = profile_records(records, "ts", None, set(), [])
    assert result["timestamp_analysis"]["far_future_count"] == 1


def test_region_dirty_value():
    result = profile_records(SAMPLE_REQUESTS, "created_date", "region", VALID_REGIONS, [])
    dirty = result["region_analysis"]["dirty_values"]
    assert "R1" in dirty


def test_region_missing():
    records = [{"region": None}, {"region": "REGION ONE"}]
    result = profile_records(records, None, "region", VALID_REGIONS, [])
    assert result["region_analysis"]["missing_pct"] == pytest.approx(0.5)


def test_numeric_distribution():
    records = [
        {"injured": "2", "killed": "0"},
        {"injured": "0", "killed": "1"},
        {"injured": None, "killed": "0"},
    ]
    result = profile_records(records, None, None, set(), ["injured", "killed"])
    dist = result["numeric_distributions"]
    assert dist["injured"]["missing_pct"] == pytest.approx(1 / 3, rel=0.01)
    assert dist["injured"]["min"] == 0.0
    assert dist["injured"]["max"] == 2.0
    assert dist["killed"]["mean"] == pytest.approx(1 / 3, rel=0.01)


# ── flatten_open_meteo ────────────────────────────────────────────────────────

def test_flatten_open_meteo_basic():
    raw = {
        "hourly": {
            "time": ["2024-01-01T00:00", "2024-01-01T01:00"],
            "temperature_2m": [5.0, 5.5],
            "precipitation": [0.0, 0.1],
        }
    }
    rows = flatten_open_meteo(raw)
    assert len(rows) == 2
    assert rows[0]["time"] == "2024-01-01T00:00"
    assert rows[0]["temperature_2m"] == 5.0
    assert rows[1]["precipitation"] == 0.1


def test_flatten_open_meteo_empty():
    assert flatten_open_meteo({}) == []
    assert flatten_open_meteo({"hourly": {}}) == []


def test_flatten_open_meteo_short_array():
    """If a variable array is shorter than time, fill with None."""
    raw = {
        "hourly": {
            "time": ["T0", "T1", "T2"],
            "temperature_2m": [1.0],  # shorter than times
        }
    }
    rows = flatten_open_meteo(raw)
    assert rows[0]["temperature_2m"] == 1.0
    assert rows[1]["temperature_2m"] is None
    assert rows[2]["temperature_2m"] is None


# ── render_markdown ───────────────────────────────────────────────────────────

def test_render_markdown_contains_source():
    results = {
        "SRC-TEST-DAILY": {
            "service_requests": {
                "coverage": {
                    "total_records": 5000,
                    "partition_count": 5,
                    "min_count": 800,
                    "max_count": 1200,
                    "median_count": 1000,
                    "gaps": [],
                    "anomaly_low": [],
                },
                "files_sampled": 2,
                "sample_profile": {"record_count": 0},
            }
        }
    }
    md = render_markdown(results, "my-bucket")
    assert "SRC-TEST-DAILY" in md
    assert "5,000" in md
    assert "my-bucket" in md
