"""
Unit tests for ingestion.loaders.s3_loader.S3BronzeLoader.

The loader is exercised in three flavors:
- ``write_daily()`` — splits records by their ``timestamp_field`` date and
  writes per-day files inside a month folder.
- ``write_monthly_shard()`` — one flat file per calendar month.
- ``write()`` — one file per ``ingest_date=`` partition.

The boto3 client is mocked, so these run without MinIO. Round-trip behaviour
against a real store is covered by tests/integration/.
"""

from __future__ import annotations

import gzip
import json
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ingestion.loaders.s3_loader import S3BronzeLoader


def _make_loader(timestamp_field: str = "created_date") -> tuple[S3BronzeLoader, MagicMock]:
    """Build an S3BronzeLoader with a mocked boto3 client.

    Returns:
        (loader, client_mock) — ``client_mock.uploaded`` is a list of
        (key, body_bytes, metadata) tuples captured for assertions.
    """
    client_mock = MagicMock(name="s3_client")
    uploaded: list[tuple[str, bytes, dict]] = []

    def _put_object(*, Bucket: str, Key: str, Body: bytes, **kwargs) -> dict:  # noqa: N803
        uploaded.append((Key, Body, dict(kwargs.get("Metadata") or {})))
        return {}

    client_mock.put_object.side_effect = _put_object
    client_mock.uploaded = uploaded  # type: ignore[attr-defined]

    loader = S3BronzeLoader(
        bucket_name="test-bucket",
        timestamp_field=timestamp_field,
        client=client_mock,
    )
    return loader, client_mock


def _payload(client: MagicMock, key: str) -> list[dict]:
    """Decompress a captured data object and parse its NDJSON lines."""
    body = next(body for (k, body, _) in client.uploaded if k == key)
    text = gzip.decompress(body).decode("utf-8")
    return [json.loads(line) for line in text.splitlines()]


def _manifest(client: MagicMock, key: str) -> dict:
    body = next(body for (k, body, _) in client.uploaded if k == key)
    return json.loads(body)


def _sample_311_records() -> list[dict]:
    return [
        {"unique_key": "A", "created_date": "2026-03-21T09:00:00.000"},
        {"unique_key": "B", "created_date": "2026-03-21T18:30:00.000"},
        {"unique_key": "C", "created_date": "2026-03-22T03:15:00.000"},
        {"unique_key": "D", "created_date": "2026-04-01T00:00:00.000"},
    ]


# ── Compression contract (ADR 0006 §4) ───────────────────────────────────────


def test_data_objects_are_gzipped_and_named_gz():
    """Spark picks its codec from the extension; a mismatch corrupts silently."""
    loader, client = _make_loader("created_date")

    loader.write_daily(
        source_id="SRC-NYC-311", dataset_name="nyc_311", records=_sample_311_records(),
    )

    key = "bronze/raw/SRC-NYC-311/nyc_311/2026-03/data_2026-03-21.ndjson.gz"
    body = next(b for (k, b, _) in client.uploaded if k == key)
    assert body[:2] == b"\x1f\x8b"  # gzip magic number
    assert gzip.decompress(body).decode("utf-8").count("\n") == 2


def test_manifests_stay_uncompressed_json():
    """Manifests must remain readable with `head` and greppable by the audit DAG."""
    loader, client = _make_loader("created_date")

    loader.write_daily(
        source_id="SRC-NYC-311", dataset_name="nyc_311", records=_sample_311_records(),
    )

    key = "bronze/raw/SRC-NYC-311/nyc_311/2026-03/manifest_2026-03-21.json"
    body = next(b for (k, b, _) in client.uploaded if k == key)
    assert body[:2] != b"\x1f\x8b"
    assert json.loads(body)["record_count"] == 2


def test_content_encoding_is_never_set():
    """Setting it makes some S3 clients decompress a second time (ADR 0006 §4.1)."""
    loader, client = _make_loader("created_date")

    loader.write_daily(
        source_id="SRC-NYC-311", dataset_name="nyc_311", records=_sample_311_records(),
    )

    for call in client.put_object.call_args_list:
        assert "ContentEncoding" not in call.kwargs


def test_checksum_describes_the_uncompressed_payload():
    """Idempotency check must not depend on the compression settings used."""
    import hashlib

    loader, client = _make_loader("created_date")

    manifests = loader.write_daily(
        source_id="SRC-NYC-311", dataset_name="nyc_311", records=_sample_311_records(),
    )

    key = "bronze/raw/SRC-NYC-311/nyc_311/2026-03/data_2026-03-21.ndjson.gz"
    body = next(b for (k, b, _) in client.uploaded if k == key)
    plain = gzip.decompress(body)

    day21 = manifests[0]
    assert day21.sha256_checksum == hashlib.sha256(plain).hexdigest()
    assert day21.file_size_bytes == len(plain)
    assert day21.stored_bytes == len(body)
    assert day21.compression == "gzip"


def test_compression_is_reproducible_across_runs():
    """gzip embeds mtime by default, which would make identical data differ."""
    loader_a, client_a = _make_loader("created_date")
    loader_b, client_b = _make_loader("created_date")

    loader_a.write_daily(
        source_id="SRC-NYC-311", dataset_name="nyc_311", records=_sample_311_records(),
    )
    loader_b.write_daily(
        source_id="SRC-NYC-311", dataset_name="nyc_311", records=_sample_311_records(),
    )

    key = "bronze/raw/SRC-NYC-311/nyc_311/2026-03/data_2026-03-21.ndjson.gz"
    assert next(b for (k, b, _) in client_a.uploaded if k == key) == next(
        b for (k, b, _) in client_b.uploaded if k == key
    )


# ── write_daily() — grouping & path layout ──────────────────────────────────


def test_write_daily_splits_records_into_per_day_files():
    loader, client = _make_loader("created_date")

    manifests = loader.write_daily(
        source_id="SRC-NYC-311",
        dataset_name="nyc_311",
        records=_sample_311_records(),
    )

    # 3 days, 3 manifests in chronological order
    assert [m.filename for m in manifests] == [
        "data_2026-03-21.ndjson.gz",
        "data_2026-03-22.ndjson.gz",
        "data_2026-04-01.ndjson.gz",
    ]
    # Record counts per day: A+B, C, D
    assert [m.record_count for m in manifests] == [2, 1, 1]
    # data_date_min == data_date_max for a daily group
    for m in manifests:
        assert m.data_date_min == m.data_date_max

    keys = [k for (k, _, _) in client.uploaded]
    # 3 data files + 3 per-day manifests = 6 uploads
    assert len(keys) == 6
    assert "bronze/raw/SRC-NYC-311/nyc_311/2026-03/data_2026-03-21.ndjson.gz" in keys
    assert "bronze/raw/SRC-NYC-311/nyc_311/2026-03/data_2026-03-22.ndjson.gz" in keys
    assert "bronze/raw/SRC-NYC-311/nyc_311/2026-04/data_2026-04-01.ndjson.gz" in keys
    # Per-day manifest files (one per data file)
    assert "bronze/raw/SRC-NYC-311/nyc_311/2026-03/manifest_2026-03-21.json" in keys
    assert "bronze/raw/SRC-NYC-311/nyc_311/2026-03/manifest_2026-03-22.json" in keys
    assert "bronze/raw/SRC-NYC-311/nyc_311/2026-04/manifest_2026-04-01.json" in keys


def test_write_daily_writes_only_records_within_their_day():
    loader, client = _make_loader("created_date")
    loader.write_daily(
        source_id="SRC-NYC-311",
        dataset_name="nyc_311",
        records=_sample_311_records(),
    )

    payload = _payload(
        client, "bronze/raw/SRC-NYC-311/nyc_311/2026-03/data_2026-03-21.ndjson.gz",
    )
    assert sorted(r["unique_key"] for r in payload) == ["A", "B"]


def test_write_daily_per_day_manifest_describes_that_day():
    """Each day gets its own manifest_YYYY-MM-DD.json describing its data file."""
    loader, client = _make_loader("created_date")
    loader.write_daily(
        source_id="SRC-NYC-311",
        dataset_name="nyc_311",
        records=_sample_311_records(),
    )

    day21 = _manifest(
        client, "bronze/raw/SRC-NYC-311/nyc_311/2026-03/manifest_2026-03-21.json",
    )
    assert day21["filename"] == "data_2026-03-21.ndjson.gz"
    assert day21["record_count"] == 2
    assert day21["data_date_min"] == "2026-03-21"
    assert day21["data_date_max"] == "2026-03-21"
    assert day21["month_partition"] == "2026-03"

    day22 = _manifest(
        client, "bronze/raw/SRC-NYC-311/nyc_311/2026-03/manifest_2026-03-22.json",
    )
    assert day22["filename"] == "data_2026-03-22.ndjson.gz"
    assert day22["record_count"] == 1
    assert day22["data_date_min"] == "2026-03-22"
    assert day22["data_date_max"] == "2026-03-22"


def test_write_daily_handles_open_meteo_time_field():
    """Open-Meteo records have an ISO time like '2026-03-21T00:00' (no microseconds)."""
    records = [
        {"time": "2026-03-21T00:00", "temperature_2m": 1.0},
        {"time": "2026-03-21T12:00", "temperature_2m": 5.5},
        {"time": "2026-03-22T00:00", "temperature_2m": 0.5},
    ]
    loader, client = _make_loader("time")
    manifests = loader.write_daily(
        source_id="SRC-Open-Meteo",
        dataset_name="nyc_weather_forecast",
        records=records,
    )
    assert [m.filename for m in manifests] == [
        "data_2026-03-21.ndjson.gz",
        "data_2026-03-22.ndjson.gz",
    ]
    keys = [k for (k, _, _) in client.uploaded]
    assert "bronze/raw/SRC-Open-Meteo/nyc_weather_forecast/2026-03/manifest_2026-03-21.json" in keys
    assert "bronze/raw/SRC-Open-Meteo/nyc_weather_forecast/2026-03/manifest_2026-03-22.json" in keys


def test_write_daily_handles_z_suffix_iso_timestamps():
    """Timestamps with a 'Z' suffix (UTC) are parsed correctly."""
    records = [
        {"created_date": "2026-03-21T23:30:00Z", "x": 1},
        {"created_date": "2026-03-22T00:30:00Z", "x": 2},
    ]
    loader, _ = _make_loader("created_date")
    manifests = loader.write_daily(
        source_id="SRC-NYC-311",
        dataset_name="nyc_311",
        records=records,
    )
    assert [m.filename for m in manifests] == [
        "data_2026-03-21.ndjson.gz",
        "data_2026-03-22.ndjson.gz",
    ]


def test_write_daily_drops_records_with_missing_or_bad_timestamp():
    records = [
        {"created_date": "2026-03-21T09:00:00.000", "x": 1},
        {"created_date": None, "x": 2},          # missing
        {"x": 3},                                # missing
        {"created_date": "not-a-date", "x": 4},  # unparseable
    ]
    loader, _ = _make_loader("created_date")
    manifests = loader.write_daily(
        source_id="SRC-NYC-311",
        dataset_name="nyc_311",
        records=records,
    )
    assert len(manifests) == 1
    assert manifests[0].record_count == 1
    assert manifests[0].filename == "data_2026-03-21.ndjson.gz"


def test_write_daily_returns_empty_list_when_no_usable_records():
    loader, client = _make_loader("created_date")
    manifests = loader.write_daily(
        source_id="SRC-NYC-311",
        dataset_name="nyc_311",
        records=[{"x": 1}, {"created_date": "garbage"}],
    )
    assert manifests == []
    assert client.uploaded == []


def test_write_daily_raises_if_timestamp_field_not_configured():
    """Calling write_daily() on a loader with empty timestamp_field is a programming error."""
    loader, _ = _make_loader(timestamp_field="")
    with pytest.raises(ValueError, match="timestamp_field"):
        loader.write_daily(
            source_id="SRC-NYC-311",
            dataset_name="nyc_311",
            records=[{"created_date": "2026-03-21T00:00:00"}],
        )


# ── write_monthly_shard() ────────────────────────────────────────────────────


def test_write_monthly_shard_path_layout():
    loader, client = _make_loader("crash_date")

    manifest = loader.write_monthly_shard(
        source_id="SRC-NYPD",
        dataset_name="nypd_collisions",
        month_partition="2026-03",
        records=[{"crash_date": "2026-03-05T00:00:00.000", "id": 1}],
    )

    keys = [k for (k, _, _) in client.uploaded]
    assert keys == [
        "bronze/raw/SRC-NYPD/nypd_collisions/data_2026-03.ndjson.gz",
        "bronze/raw/SRC-NYPD/nypd_collisions/manifest_2026-03.json",
    ]
    assert manifest.filename == "data_2026-03.ndjson.gz"
    assert manifest.month_partition == "2026-03"
    assert manifest.record_count == 1


def test_write_monthly_shard_is_idempotent_on_rerun():
    """Re-running the same month overwrites the same two keys, adding no partitions."""
    loader, client = _make_loader("crash_date")
    records = [{"crash_date": "2026-03-05T00:00:00.000", "id": 1}]

    loader.write_monthly_shard(
        source_id="SRC-NYPD", dataset_name="nypd_collisions",
        month_partition="2026-03", records=records,
    )
    loader.write_monthly_shard(
        source_id="SRC-NYPD", dataset_name="nypd_collisions",
        month_partition="2026-03", records=records,
    )

    keys = [k for (k, _, _) in client.uploaded]
    assert len(keys) == 4
    assert len(set(keys)) == 2


# ── write() — ingest_date partition ──────────────────────────────────────────


def test_write_uses_ingest_date_partition_layout():
    loader, client = _make_loader("created_date")

    manifest = loader.write(
        source_id="SRC-NYC-311",
        dataset_name="nyc_311",
        ingest_date=date(2026, 3, 21),
        records=[{"created_date": "2026-03-20T09:00:00.000", "unique_key": "A"}],
    )

    keys = [k for (k, _, _) in client.uploaded]
    assert keys == [
        "bronze/raw/SRC-NYC-311/nyc_311/ingest_date=2026-03-21/data.ndjson.gz",
        "bronze/raw/SRC-NYC-311/nyc_311/ingest_date=2026-03-21/manifest.json",
    ]
    # ingest_date is the collection date; data_date_* still come from the records.
    assert manifest.ingest_date == "2026-03-21"
    assert manifest.data_date_min == "2026-03-20"


def test_write_object_metadata_carries_source_and_count():
    loader, client = _make_loader("created_date")

    loader.write(
        source_id="SRC-NYC-311",
        dataset_name="nyc_311",
        ingest_date=date(2026, 3, 21),
        records=[{"created_date": "2026-03-21T09:00:00.000"}],
    )

    _, _, meta = client.uploaded[0]
    assert meta["source_id"] == "SRC-NYC-311"
    assert meta["dataset_name"] == "nyc_311"
    assert meta["ingest_date"] == "2026-03-21"
    assert meta["record_count"] == "1"


# ── write_snapshot() ─────────────────────────────────────────────────────────


def test_write_snapshot_uses_the_ingest_date_partition():
    """Snapshots partition by collection date, so consecutive days coexist."""
    loader, client = _make_loader(timestamp_field="")

    loader.write_snapshot(
        source_id="SRC-WPG-SNOW", dataset_name="snow_clearing_status",
        ingest_date=date(2026, 11, 3), records=[{"address_id": 1, "has_street": True}],
    )
    loader.write_snapshot(
        source_id="SRC-WPG-SNOW", dataset_name="snow_clearing_status",
        ingest_date=date(2026, 11, 4), records=[{"address_id": 1, "has_street": False}],
    )

    keys = [k for (k, _, _) in client.uploaded]
    base = "bronze/raw/SRC-WPG-SNOW/snow_clearing_status"
    assert f"{base}/ingest_date=2026-11-03/data.ndjson.gz" in keys
    assert f"{base}/ingest_date=2026-11-04/data.ndjson.gz" in keys
    # Four distinct objects: yesterday must not have been overwritten.
    assert len(set(keys)) == 4


def test_write_snapshot_works_without_a_timestamp_field():
    """The upstream this exists for has no time field at all."""
    loader, client = _make_loader(timestamp_field="")

    manifest = loader.write_snapshot(
        source_id="SRC-WPG-SNOW", dataset_name="snow_clearing_status",
        ingest_date=date(2026, 11, 3),
        records=[{"address_id": 1}, {"address_id": 2}],
    )

    assert manifest.record_count == 2
    assert manifest.data_date_min is None
    assert manifest.data_date_max is None
    assert manifest.timestamp_field == ""
    assert _payload(
        client, "bronze/raw/SRC-WPG-SNOW/snow_clearing_status/"
                "ingest_date=2026-11-03/data.ndjson.gz",
    ) == [{"address_id": 1}, {"address_id": 2}]


def test_write_snapshot_rerun_on_same_day_overwrites_that_day():
    loader, client = _make_loader(timestamp_field="")

    for value in (True, False):
        loader.write_snapshot(
            source_id="SRC-WPG-SNOW", dataset_name="snow_clearing_status",
            ingest_date=date(2026, 11, 3), records=[{"has_street": value}],
        )

    keys = [k for (k, _, _) in client.uploaded]
    assert len(keys) == 4
    assert len(set(keys)) == 2


# ── Lazy client construction ─────────────────────────────────────────────────


def test_client_is_not_built_until_a_write_happens(monkeypatch):
    """A dry run must not require S3 credentials to be configured."""
    import ingestion.loaders.s3_loader as module

    def _explode() -> None:
        raise AssertionError("client built during construction")

    monkeypatch.setattr(module, "build_s3_client", _explode)

    S3BronzeLoader(bucket_name="uoip", timestamp_field="created_date")


# ── _group_by_date() helper ─────────────────────────────────────────────────


def test_group_by_date_preserves_record_order_within_a_day():
    loader = _make_loader("created_date")[0]
    records = [
        {"created_date": "2026-03-21T10:00:00.000", "i": 0},
        {"created_date": "2026-03-21T09:00:00.000", "i": 1},  # earlier same day
        {"created_date": "2026-03-22T00:00:00.000", "i": 2},
    ]
    groups = loader._group_by_date(records)
    # Day-1 records should be in input order, not sorted by timestamp
    assert [r["i"] for r in groups["2026-03-21"]] == [0, 1]
    assert [r["i"] for r in groups["2026-03-22"]] == [2]
