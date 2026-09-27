"""
Snapshot collection of the Open-Meteo forecast — H2-R12 batch 0.

The forecast was registered as ``partition_strategy: snapshot`` on 2026-08-02
and never collected once: the collector only knew how to walk a Socrata table,
and a forecast pull (a few hundred rows) sat far below the Socrata-sized
small-pull floor. Either defect alone was enough to lose every day. These tests
pin both, against the **real** source config rather than a synthetic one,
because the failure lived in the combination of config and code.

Design: docs/dev/design/20260927-forecast-chain-rehearsal.md §0.1 and batch 0.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest
import requests

from ingestion.config import ApiType, DatasetConfig, SourceConfig, load_source_config
from ingestion.snapshot import SnapshotCollector
from ingestion.snapshot.collector import DEFAULT_MIN_RECORDS, resolve_min_records
from ingestion.snapshot.fetch import SnapshotFetchError, fetch_snapshot_records

SOURCE_ID = "SRC-Open-Meteo"
FORECAST = "weather_forecast"

# (past_days + forecast_days) x 24 hours, as configured.
HOURS_PER_PULL = (3 + 16) * 24


def _weather() -> SourceConfig:
    return load_source_config(SOURCE_ID)


def _forecast_ds() -> DatasetConfig:
    return next(d for d in _weather().datasets if d.name == FORECAST)


def _hourly_response(hours: int) -> dict:
    return {
        "hourly": {
            "time": [f"2026-11-01T{h % 24:02d}:00" for h in range(hours)],
            "temperature_2m": [-5.0] * hours,
            "precipitation": [0.0] * hours,
            "snowfall": [0.0] * hours,
            "windspeed_10m": [10.0] * hours,
        },
    }


def _mock_get(hours: int = HOURS_PER_PULL) -> MagicMock:
    get = MagicMock(name="requests.get")
    get.return_value.json.return_value = _hourly_response(hours)
    get.return_value.raise_for_status = MagicMock()
    return get


# ── Fetch ────────────────────────────────────────────────────────────────────


def test_forecast_snapshot_asks_for_the_configured_relative_window():
    """The collection window is [today, today+1); the request must not be.

    Deriving past_days/forecast_days from the collection date would ask for a
    one-day outlook and look like a healthy, small pull.
    """
    get = _mock_get()
    with patch("ingestion.backfill.fetchers.open_meteo.requests.get", get):
        list(fetch_snapshot_records(_forecast_ds()))

    params = get.call_args.kwargs["params"]
    assert params["past_days"] == 3
    assert params["forecast_days"] == 16
    assert "hourly" in params
    assert get.call_args.args[0] == _forecast_ds().endpoint


def test_forecast_snapshot_yields_one_record_per_hour():
    get = _mock_get(hours=5)
    with patch("ingestion.backfill.fetchers.open_meteo.requests.get", get):
        records = list(fetch_snapshot_records(_forecast_ds()))

    assert len(records) == 5
    assert set(records[0]) == {
        "time", "temperature_2m", "precipitation", "snowfall", "windspeed_10m",
    }


def test_a_transport_failure_surfaces_as_a_snapshot_fetch_error():
    get = MagicMock(side_effect=requests.ConnectionError("unreachable"))
    with patch("ingestion.backfill.fetchers.open_meteo.requests.get", get), \
         pytest.raises(SnapshotFetchError, match=FORECAST):
        list(fetch_snapshot_records(_forecast_ds()))


def test_an_unsupported_api_type_is_still_refused():
    ds = DatasetConfig.model_construct(
        name="x", api_type=ApiType.GENERIC_REST, endpoint="https://example.com",
        query_params=None, timestamp_field=None, resource_id=None, domain=None,
        format=None,
    )
    with pytest.raises(ValueError, match="generic_rest"):
        list(fetch_snapshot_records(ds))


# ── Small-pull floor ─────────────────────────────────────────────────────────


def test_a_healthy_forecast_pull_clears_its_floor():
    """The defect that would have rejected every collection, pinned."""
    floor = resolve_min_records(_forecast_ds())
    assert floor < HOURS_PER_PULL
    # Still a real guard: an empty or one-day response must not pass.
    assert floor > 24


def test_the_socrata_snapshot_keeps_the_default_floor():
    snow = load_source_config("SRC-WPG-SNOW").datasets[0]
    assert resolve_min_records(snow) == DEFAULT_MIN_RECORDS


def test_an_operator_override_beats_the_dataset_floor():
    assert resolve_min_records(_forecast_ds(), override=5) == 5


def test_a_floor_on_a_non_snapshot_dataset_is_rejected():
    raw = _weather().model_dump(mode="json")
    archive = next(d for d in raw["datasets"] if d["name"] == "weather_archive")
    archive["snapshot_min_records"] = 10

    with pytest.raises(ValueError, match="snapshot_min_records"):
        SourceConfig.model_validate(raw)


# ── Collector over the real weather source ───────────────────────────────────


def test_the_collector_takes_only_the_forecast_and_applies_its_floor():
    """The archive is `daily` and must never land in an ingest_date= partition."""
    loader = MagicMock()
    with patch("ingestion.snapshot.collector.S3BronzeLoader", return_value=loader), \
         patch("ingestion.snapshot.collector.fetch_snapshot_records", return_value=iter([])):
        loader.write_snapshot_stream.return_value = MagicMock(
            record_count=HOURS_PER_PULL, stored_bytes=1, file_size_bytes=1,
        )
        results = SnapshotCollector(_weather(), bucket="uoip", client=MagicMock()).collect(
            ingest_date=date(2026, 11, 1),
        )

    assert [r.dataset_name for r in results] == [FORECAST]
    kwargs = loader.write_snapshot_stream.call_args.kwargs
    assert kwargs["dataset_name"] == FORECAST
    assert kwargs["min_records"] == _forecast_ds().snapshot_min_records


# ── CLI dry run ──────────────────────────────────────────────────────────────


@pytest.fixture
def cli(monkeypatch):
    import scripts.collect_snapshot as module

    monkeypatch.setattr(module, "load_cli_env", lambda: None)
    return module


def test_dry_run_fetches_only_the_snapshot_datasets(cli):
    fetch = MagicMock(return_value=iter([{}] * HOURS_PER_PULL))
    with patch.object(cli, "fetch_snapshot_records", fetch):
        code = cli.main(["--source", SOURCE_ID, "--dry-run"])

    assert code == cli.EXIT_OK
    assert [c.args[0].name for c in fetch.call_args_list] == [FORECAST]


def test_dry_run_exits_non_zero_when_the_real_run_would_be_rejected(cli):
    with patch.object(cli, "fetch_snapshot_records", return_value=iter([{}] * 3)), \
         patch.object(cli, "notify_failure") as alert:
        code = cli.main(["--source", SOURCE_ID, "--dry-run"])

    assert code == cli.EXIT_COLLECTION_FAILED
    # A pre-deployment check, not a lost day: nobody gets paged.
    alert.assert_not_called()
