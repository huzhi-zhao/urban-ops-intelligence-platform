"""
Unit tests for SnapshotCollector, its notification layer, and the CLI entry.

Upstream is always mocked; the collector's job here is orchestration — which
datasets get attempted, what happens when one fails, and whether the alert and
watchdog channels fire on the right outcomes. Getting that wrong is how a
project discovers a month later that it stopped collecting.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ingestion.config import ApiType, DatasetConfig, SourceConfig, SourceMetadata
from ingestion.loaders.s3_loader import SnapshotTooSmallError
from ingestion.snapshot import SnapshotCollectionError, SnapshotCollector
from ingestion.snapshot.notify import notify_failure, ping_watchdog

SOURCE_ID = "SRC-SNAP-TEST"


def _mk_dataset(name: str, timestamp_field: str | None = None) -> DatasetConfig:
    return DatasetConfig.model_construct(
        name=name, api_type=ApiType.SOCRATA, timestamp_field=timestamp_field,
        resource_id="abcd-1234", domain="example.com",
        endpoint=None, query_params=None, format=None,
    )


def _mk_source(
    partition_strategy: str = "snapshot",
    datasets: list[DatasetConfig] | None = None,
) -> SourceConfig:
    return SourceConfig.model_construct(
        source=SourceMetadata(
            id=SOURCE_ID, name="snap", type="rest_api_socrata",  # type: ignore[arg-type]
            owner="city_operations", priority="P0", status="production",
            partition_strategy=partition_strategy,  # type: ignore[arg-type]
            description=None,
        ),
        datasets=datasets or [_mk_dataset("ds1")],
    )


def _fake_manifest(record_count: int = 100) -> SimpleNamespace:
    return SimpleNamespace(
        record_count=record_count, stored_bytes=1024, file_size_bytes=10240,
    )


def _collector(src: SourceConfig | None = None, **kwargs) -> SnapshotCollector:
    kwargs.setdefault("client", MagicMock(name="s3_client"))
    return SnapshotCollector(src or _mk_source(), bucket="uoip", **kwargs)


# ── Guard: wrong strategy ────────────────────────────────────────────────────


@pytest.mark.parametrize("strategy", ["daily", "monthly", "static"])
def test_collector_refuses_a_non_snapshot_source(strategy):
    with pytest.raises(ValueError, match="snapshot"):
        _collector(_mk_source(partition_strategy=strategy))


# ── Happy path ───────────────────────────────────────────────────────────────


def test_collect_streams_each_dataset_into_the_ingest_date_partition():
    collector = _collector(
        _mk_source(datasets=[_mk_dataset("ds1"), _mk_dataset("ds2")]),
    )
    loader = MagicMock()
    loader.write_snapshot_stream.return_value = _fake_manifest(237_867)

    with patch("ingestion.snapshot.collector.S3BronzeLoader", return_value=loader), \
         patch("ingestion.snapshot.collector.fetch_snapshot_records", return_value=iter([])):
        results = collector.collect(ingest_date=date(2026, 11, 3))

    assert [r.dataset_name for r in results] == ["ds1", "ds2"]
    assert all(r.ok for r in results)
    assert all(r.record_count == 237_867 for r in results)
    assert loader.write_snapshot_stream.call_count == 2
    assert loader.write_snapshot_stream.call_args.kwargs["ingest_date"] == date(
        2026, 11, 3,
    )


def test_collect_defaults_the_partition_to_today():
    collector = _collector()
    loader = MagicMock()
    loader.write_snapshot_stream.return_value = _fake_manifest()

    with patch("ingestion.snapshot.collector.S3BronzeLoader", return_value=loader), \
         patch("ingestion.snapshot.collector.fetch_snapshot_records", return_value=iter([])):
        collector.collect()

    assert loader.write_snapshot_stream.call_args.kwargs["ingest_date"] == date.today()


def test_collect_passes_the_min_records_floor_through_to_the_loader():
    collector = _collector(min_records=5000)
    loader = MagicMock()
    loader.write_snapshot_stream.return_value = _fake_manifest()

    with patch("ingestion.snapshot.collector.S3BronzeLoader", return_value=loader), \
         patch("ingestion.snapshot.collector.fetch_snapshot_records", return_value=iter([])):
        collector.collect()

    assert loader.write_snapshot_stream.call_args.kwargs["min_records"] == 5000


def test_each_dataset_gets_a_loader_carrying_its_own_timestamp_field():
    collector = _collector(
        _mk_source(datasets=[
            _mk_dataset("with_ts", timestamp_field="observed_at"),
            _mk_dataset("without_ts", timestamp_field=None),
        ]),
    )
    loader = MagicMock()
    loader.write_snapshot_stream.return_value = _fake_manifest()

    with patch(
        "ingestion.snapshot.collector.S3BronzeLoader", return_value=loader,
    ) as loader_cls, patch(
        "ingestion.snapshot.collector.fetch_snapshot_records", return_value=iter([]),
    ):
        collector.collect()

    fields = [c.kwargs["timestamp_field"] for c in loader_cls.call_args_list]
    assert fields == ["observed_at", ""]


# ── Failure handling ─────────────────────────────────────────────────────────


def test_a_small_pull_is_reported_as_a_failed_collection():
    collector = _collector()
    loader = MagicMock()
    loader.write_snapshot_stream.side_effect = SnapshotTooSmallError("only 3 records")

    with patch("ingestion.snapshot.collector.S3BronzeLoader", return_value=loader), \
         patch("ingestion.snapshot.collector.fetch_snapshot_records", return_value=iter([])), \
         pytest.raises(SnapshotCollectionError, match="only 3 records"):
        collector.collect()


def test_one_failing_dataset_does_not_stop_the_others():
    """A multi-dataset source should collect everything it can before alerting."""
    collector = _collector(
        _mk_source(datasets=[
            _mk_dataset("ok1"), _mk_dataset("boom"), _mk_dataset("ok2"),
        ]),
    )
    attempted: list[str] = []

    def _write(**kwargs):
        attempted.append(kwargs["dataset_name"])
        if kwargs["dataset_name"] == "boom":
            raise RuntimeError("upstream 503")
        return _fake_manifest()

    loader = MagicMock()
    loader.write_snapshot_stream.side_effect = _write

    with patch("ingestion.snapshot.collector.S3BronzeLoader", return_value=loader), \
         patch("ingestion.snapshot.collector.fetch_snapshot_records", return_value=iter([])), \
         pytest.raises(SnapshotCollectionError) as exc:
        collector.collect()

    assert attempted == ["ok1", "boom", "ok2"]
    assert "boom" in str(exc.value)
    assert "upstream 503" in str(exc.value)


def test_failure_message_names_the_source_and_the_failure_count():
    collector = _collector(_mk_source(datasets=[_mk_dataset("a"), _mk_dataset("b")]))
    loader = MagicMock()
    loader.write_snapshot_stream.side_effect = RuntimeError("nope")

    with patch("ingestion.snapshot.collector.S3BronzeLoader", return_value=loader), \
         patch("ingestion.snapshot.collector.fetch_snapshot_records", return_value=iter([])), \
         pytest.raises(SnapshotCollectionError) as exc:
        collector.collect()

    message = str(exc.value)
    assert SOURCE_ID in message
    assert "2/2" in message


# ── Notification channels ────────────────────────────────────────────────────


def test_notify_failure_posts_to_the_configured_webhook(monkeypatch):
    monkeypatch.setenv("SNAPSHOT_ALERT_WEBHOOK_URL", "https://hooks.example/abc")

    with patch("ingestion.snapshot.notify.requests.post") as post:
        assert notify_failure(SOURCE_ID, "everything is on fire") is True

    assert post.call_args.args[0] == "https://hooks.example/abc"
    assert "everything is on fire" in post.call_args.kwargs["json"]["text"]
    assert post.call_args.kwargs["json"]["source_id"] == SOURCE_ID


def test_notify_failure_is_a_no_op_when_unconfigured(monkeypatch):
    monkeypatch.delenv("SNAPSHOT_ALERT_WEBHOOK_URL", raising=False)

    with patch("ingestion.snapshot.notify.requests.post") as post:
        assert notify_failure(SOURCE_ID, "boom") is False

    post.assert_not_called()


def test_notify_failure_swallows_its_own_transport_error(monkeypatch):
    """A broken alert channel must not replace the original error."""
    import requests

    monkeypatch.setenv("SNAPSHOT_ALERT_WEBHOOK_URL", "https://hooks.example/abc")

    with patch(
        "ingestion.snapshot.notify.requests.post",
        side_effect=requests.ConnectionError("no route"),
    ):
        assert notify_failure(SOURCE_ID, "boom") is False


def test_watchdog_ping_hits_the_configured_url(monkeypatch):
    monkeypatch.setenv("SNAPSHOT_WATCHDOG_URL", "https://hc.example/uuid")

    with patch("ingestion.snapshot.notify.requests.get") as get:
        assert ping_watchdog(SOURCE_ID) is True

    assert get.call_args.args[0] == "https://hc.example/uuid"


def test_watchdog_ping_is_a_no_op_when_unconfigured(monkeypatch):
    monkeypatch.delenv("SNAPSHOT_WATCHDOG_URL", raising=False)

    with patch("ingestion.snapshot.notify.requests.get") as get:
        assert ping_watchdog(SOURCE_ID) is False

    get.assert_not_called()


# ── CLI entry point ──────────────────────────────────────────────────────────


def test_cli_pings_the_watchdog_only_after_a_successful_run(monkeypatch):
    import scripts.collect_snapshot as cli

    monkeypatch.setenv("S3_BUCKET_NAME", "uoip")
    collector = MagicMock()
    collector.collect.return_value = [
        SimpleNamespace(dataset_name="ds1", record_count=100, status="ok"),
    ]

    with patch.object(cli.SnapshotCollector, "for_source", return_value=collector), \
         patch.object(cli, "ping_watchdog") as ping, \
         patch.object(cli, "notify_failure") as alert:
        code = cli.main(["--source", SOURCE_ID])

    assert code == cli.EXIT_OK
    ping.assert_called_once_with(SOURCE_ID)
    alert.assert_not_called()


def test_cli_alerts_and_exits_2_on_a_failed_collection(monkeypatch):
    import scripts.collect_snapshot as cli

    monkeypatch.setenv("S3_BUCKET_NAME", "uoip")
    collector = MagicMock()
    collector.collect.side_effect = SnapshotCollectionError("1/1 dataset(s) failed")

    with patch.object(cli.SnapshotCollector, "for_source", return_value=collector), \
         patch.object(cli, "ping_watchdog") as ping, \
         patch.object(cli, "notify_failure") as alert:
        code = cli.main(["--source", SOURCE_ID])

    assert code == cli.EXIT_COLLECTION_FAILED
    alert.assert_called_once()
    # Checking in on a failed run would tell the watchdog the opposite of the truth.
    ping.assert_not_called()


def test_cli_exits_1_without_alerting_on_a_config_error(monkeypatch):
    """Nothing was attempted, so no history was lost and no alert is warranted."""
    import scripts.collect_snapshot as cli

    monkeypatch.setenv("S3_BUCKET_NAME", "uoip")

    with patch.object(cli, "notify_failure") as alert:
        code = cli.main(["--source", "SRC-DOES-NOT-EXIST"])

    assert code == cli.EXIT_USAGE
    alert.assert_not_called()


def test_cli_requires_a_bucket(monkeypatch):
    import scripts.collect_snapshot as cli

    monkeypatch.delenv("S3_BUCKET_NAME", raising=False)

    with pytest.raises(SystemExit, match="S3_BUCKET_NAME"):
        cli.main(["--source", SOURCE_ID])


def test_cli_dry_run_writes_nothing(monkeypatch):
    import scripts.collect_snapshot as cli

    monkeypatch.setenv("S3_BUCKET_NAME", "uoip")

    with patch.object(cli, "load_source_config", return_value=_mk_source()), \
         patch.object(cli, "fetch_snapshot_records", return_value=iter([{"a": 1}] * 7)), \
         patch.object(cli.SnapshotCollector, "for_source") as for_source, \
         patch.object(cli, "ping_watchdog") as ping:
        code = cli.main(["--source", SOURCE_ID, "--dry-run"])

    assert code == cli.EXIT_OK
    for_source.assert_not_called()
    ping.assert_not_called()
