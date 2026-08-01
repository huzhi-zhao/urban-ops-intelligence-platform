"""
Unit tests for S3BronzeLoader.write_snapshot_stream — the streaming write path.

The properties that matter here are the ones the non-streaming path gets for
free and this one has to earn: the manifest still describes the uncompressed
payload even though nothing ever holds it, records are never materialised, and
a suspiciously small pull writes nothing at all.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
from datetime import date
from typing import Any
from unittest.mock import MagicMock

import pytest

from ingestion.loaders.s3_loader import S3BronzeLoader, SnapshotTooSmallError

SOURCE = "SRC-WPG-SNOW"
DATASET = "snow_clearing_status"
DAY = date(2026, 11, 3)
PREFIX = f"bronze/raw/{SOURCE}/{DATASET}/ingest_date=2026-11-03"


def _make_loader(timestamp_field: str = "") -> tuple[S3BronzeLoader, MagicMock]:
    """Loader with a mocked client that records both upload styles.

    ``upload_fileobj`` (streaming) and ``put_object`` (manifest) land in the same
    ``uploaded`` list so assertions can treat them uniformly.
    """
    client = MagicMock(name="s3_client")
    uploaded: list[tuple[str, bytes, dict]] = []

    def _upload_fileobj(fileobj, bucket, key, ExtraArgs=None) -> None:  # noqa: N803
        uploaded.append((key, fileobj.read(), dict((ExtraArgs or {}).get("Metadata", {}))))

    def _put_object(*, Bucket, Key, Body, **kwargs) -> dict:  # noqa: N803
        uploaded.append((Key, Body, dict(kwargs.get("Metadata") or {})))
        return {}

    client.upload_fileobj.side_effect = _upload_fileobj
    client.put_object.side_effect = _put_object
    client.uploaded = uploaded  # type: ignore[attr-defined]

    loader = S3BronzeLoader(
        bucket_name="uoip", timestamp_field=timestamp_field, client=client,
    )
    return loader, client


def _records(n: int) -> list[dict[str, Any]]:
    return [{"address_id": i, "has_street": i % 2 == 0} for i in range(n)]


def _stored(client: MagicMock, key: str) -> bytes:
    return next(body for (k, body, _) in client.uploaded if k == key)


# ── Round-trip ───────────────────────────────────────────────────────────────


def test_streamed_object_decompresses_to_the_records_that_went_in():
    loader, client = _make_loader()
    records = _records(50)

    loader.write_snapshot_stream(
        source_id=SOURCE, dataset_name=DATASET, ingest_date=DAY,
        records=iter(records), min_records=1,
    )

    body = _stored(client, f"{PREFIX}/data.ndjson.gz")
    lines = gzip.decompress(body).decode("utf-8").splitlines()
    assert [json.loads(line) for line in lines] == records


def test_streamed_layout_matches_the_non_streaming_snapshot_path():
    """Two write paths, one on-disk contract — the layout must not fork."""
    loader, client = _make_loader()

    loader.write_snapshot_stream(
        source_id=SOURCE, dataset_name=DATASET, ingest_date=DAY,
        records=iter(_records(5)), min_records=1,
    )

    assert sorted(k for (k, _, _) in client.uploaded) == [
        f"{PREFIX}/data.ndjson.gz",
        f"{PREFIX}/manifest.json",
    ]


def test_manifest_describes_the_uncompressed_payload():
    loader, client = _make_loader()
    records = _records(200)
    expected = "".join(
        json.dumps(r, ensure_ascii=False) + "\n" for r in records
    ).encode("utf-8")

    manifest = loader.write_snapshot_stream(
        source_id=SOURCE, dataset_name=DATASET, ingest_date=DAY,
        records=iter(records), min_records=1,
    )

    assert manifest.record_count == 200
    assert manifest.file_size_bytes == len(expected)
    assert manifest.sha256_checksum == hashlib.sha256(expected).hexdigest()
    assert manifest.compression == "gzip"
    assert manifest.stored_bytes == len(_stored(client, f"{PREFIX}/data.ndjson.gz"))
    assert manifest.stored_bytes < manifest.file_size_bytes


def test_streamed_manifest_is_written_uncompressed():
    loader, client = _make_loader()

    manifest = loader.write_snapshot_stream(
        source_id=SOURCE, dataset_name=DATASET, ingest_date=DAY,
        records=iter(_records(5)), min_records=1,
    )

    stored = json.loads(_stored(client, f"{PREFIX}/manifest.json"))
    assert stored["record_count"] == manifest.record_count
    assert stored["sha256_checksum"] == manifest.sha256_checksum
    assert stored["stored_bytes"] == manifest.stored_bytes


def test_streamed_checksum_equals_the_non_streaming_one():
    """Same records through either path ⇒ same checksum, or idempotency breaks."""
    streaming, _ = _make_loader()
    buffered, _ = _make_loader()
    records = _records(30)

    streamed = streaming.write_snapshot_stream(
        source_id=SOURCE, dataset_name=DATASET, ingest_date=DAY,
        records=iter(records), min_records=1,
    )
    written = buffered.write_snapshot(
        source_id=SOURCE, dataset_name=DATASET, ingest_date=DAY, records=records,
    )

    assert streamed.sha256_checksum == written.sha256_checksum
    assert streamed.file_size_bytes == written.file_size_bytes


# ── Streaming behaviour ──────────────────────────────────────────────────────


def test_records_are_consumed_lazily_and_not_held():
    """The reason this path exists: peak memory must not scale with the dataset."""
    loader, _ = _make_loader()
    live = 0
    peak = 0

    def _generator():
        nonlocal live, peak
        for i in range(500):
            live += 1
            peak = max(peak, live)
            yield {"address_id": i}
            live -= 1

    manifest = loader.write_snapshot_stream(
        source_id=SOURCE, dataset_name=DATASET, ingest_date=DAY,
        records=_generator(), min_records=1,
    )

    assert manifest.record_count == 500
    assert peak == 1, "records were materialised instead of streamed"


def test_upload_happens_after_the_stream_is_fully_consumed():
    """Nothing may reach the bucket until the pull has proven complete."""
    loader, client = _make_loader()
    uploads_seen_during_fetch: list[int] = []

    def _generator():
        for i in range(10):
            uploads_seen_during_fetch.append(client.upload_fileobj.call_count)
            yield {"address_id": i}

    loader.write_snapshot_stream(
        source_id=SOURCE, dataset_name=DATASET, ingest_date=DAY,
        records=_generator(), min_records=1,
    )

    assert uploads_seen_during_fetch == [0] * 10
    assert client.upload_fileobj.call_count == 1


def test_timestamp_field_is_optional():
    """The upstream this exists for has no time field at all."""
    loader, _ = _make_loader(timestamp_field="")

    manifest = loader.write_snapshot_stream(
        source_id=SOURCE, dataset_name=DATASET, ingest_date=DAY,
        records=iter(_records(5)), min_records=1,
    )

    assert manifest.data_date_min is None
    assert manifest.data_date_max is None


def test_date_range_is_accumulated_when_a_timestamp_field_exists():
    loader, _ = _make_loader(timestamp_field="observed_at")

    manifest = loader.write_snapshot_stream(
        source_id=SOURCE, dataset_name=DATASET, ingest_date=DAY,
        records=iter([
            {"observed_at": "2026-11-02T10:00:00"},
            {"observed_at": "2026-10-30T23:00:00"},
            {"observed_at": "not-a-date"},
            {"other": 1},
        ]),
        min_records=1,
    )

    assert manifest.data_date_min == "2026-10-30"
    assert manifest.data_date_max == "2026-11-02"


# ── The small-pull guard ─────────────────────────────────────────────────────


def test_a_pull_below_the_floor_writes_nothing_at_all():
    """A snapshot day cannot be re-collected, so a bad pull must not land."""
    loader, client = _make_loader()

    with pytest.raises(SnapshotTooSmallError, match="below the minimum"):
        loader.write_snapshot_stream(
            source_id=SOURCE, dataset_name=DATASET, ingest_date=DAY,
            records=iter(_records(9)), min_records=10,
        )

    assert client.uploaded == []
    client.upload_fileobj.assert_not_called()
    client.put_object.assert_not_called()


def test_an_empty_pull_is_rejected_by_default():
    loader, client = _make_loader()

    with pytest.raises(SnapshotTooSmallError):
        loader.write_snapshot_stream(
            source_id=SOURCE, dataset_name=DATASET, ingest_date=DAY,
            records=iter([]),
        )

    assert client.uploaded == []


def test_a_pull_exactly_at_the_floor_is_accepted():
    loader, client = _make_loader()

    manifest = loader.write_snapshot_stream(
        source_id=SOURCE, dataset_name=DATASET, ingest_date=DAY,
        records=iter(_records(10)), min_records=10,
    )

    assert manifest.record_count == 10
    assert len(client.uploaded) == 2


# ── Object metadata ──────────────────────────────────────────────────────────


def test_stream_upload_sets_content_type_but_never_content_encoding():
    loader, client = _make_loader()

    loader.write_snapshot_stream(
        source_id=SOURCE, dataset_name=DATASET, ingest_date=DAY,
        records=iter(_records(5)), min_records=1,
    )

    extra = client.upload_fileobj.call_args.kwargs["ExtraArgs"]
    assert extra["ContentType"] == "application/gzip"
    assert "ContentEncoding" not in extra
    assert extra["Metadata"]["record_count"] == "5"
    assert extra["Metadata"]["ingest_date"] == "2026-11-03"


def test_stream_writes_a_seekable_body_positioned_at_the_start():
    """boto3 reads from the current position — a stale offset truncates silently."""
    loader = S3BronzeLoader(bucket_name="uoip", timestamp_field="", client=MagicMock())
    captured: list[bytes] = []

    def _upload_fileobj(fileobj, bucket, key, ExtraArgs=None) -> None:  # noqa: N803
        assert fileobj.tell() == 0
        captured.append(fileobj.read())

    loader._client_override.upload_fileobj.side_effect = _upload_fileobj

    manifest = loader.write_snapshot_stream(
        source_id=SOURCE, dataset_name=DATASET, ingest_date=DAY,
        records=iter(_records(20)), min_records=1,
    )

    assert len(captured[0]) == manifest.stored_bytes
    assert len(gzip.open(io.BytesIO(captured[0])).read().splitlines()) == 20
