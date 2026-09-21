"""
Unit tests for the four fetchers in ``ingestion.backfill.fetchers`` plus the
:func:`build_fetcher` factory.

Each fetcher is exercised with its external dependency mocked at the module
boundary:

- ``SocrataFetcher`` / ``SocrataGeoJsonFetcher`` — patch
  ``ingestion.backfill.fetchers.socrata.SocrataClient`` /
  ``.socrata_geojson.SocrataClient``
- ``OpenMeteoFetcher`` — patch ``ingestion.backfill.fetchers.open_meteo.requests.get``
- ``GenericRestFetcher`` — patch ``ingestion.backfill.fetchers.generic_rest.requests.get``

The goal is to verify the dispatch logic, request construction, and
error-wrapping at the fetcher boundary — not the behavior of
``SocrataClient`` or ``requests`` (those are library code).
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ingestion.backfill.fetchers import build_fetcher  # noqa: E402
from ingestion.backfill.fetchers.generic_rest import GenericRestFetcher  # noqa: E402
from ingestion.backfill.fetchers.open_meteo import (  # noqa: E402
    ARCHIVE_BASE_URL,
    MAX_PAST_DAYS,
    OpenMeteoFetcher,
)
from ingestion.backfill.fetchers.socrata import SocrataFetcher  # noqa: E402
from ingestion.backfill.fetchers.socrata_geojson import SocrataGeoJsonFetcher  # noqa: E402
from ingestion.config import ApiType, DatasetConfig  # noqa: E402

# ── Shared fixtures / builders ───────────────────────────────────────────────


def _socrata_ds(
    resource_id: str = "test-dly1",
    domain: str = "data.example-portal.gov",
    timestamp_field: str = "opened_at",
) -> DatasetConfig:
    return DatasetConfig(
        name="service_requests",
        api_type=ApiType.SOCRATA,
        resource_id=resource_id,
        domain=domain,
        timestamp_field=timestamp_field,
    )


def _open_meteo_ds(
    endpoint: str = "https://api.open-meteo.com/v1/forecast",
    query_params: dict | None = None,
) -> DatasetConfig:
    return DatasetConfig(
        name="nyc_weather_forecast",
        api_type=ApiType.OPEN_METEO,
        endpoint=endpoint,
        query_params=query_params,
        timestamp_field="time",
    )


# 🔴 Open-Meteo routing is decided against ``date.today()``, so a window
# written as a calendar literal changes meaning as real time passes. These
# tests once carried ``date(2026, 6, 10)`` as "a recent window"; on
# 2026-09-14 that start was 96 days old, crossed ``MAX_PAST_DAYS = 92``, and
# the fetcher correctly routed to the archive API — whereupon the test
# asserting ``past_days`` in the query params failed. The defect was in the
# test, not the fetcher: a literal cannot express "recent".
#
# Every Open-Meteo window below is therefore anchored to today. A window that
# must stay on one side of the 92-day boundary says so in days.


def _recent_window(days_ago: int = 10, length: int = 3) -> tuple[date, date]:
    """A ``[start, end)`` window that always routes to the forecast API."""
    start = date.today() - timedelta(days=days_ago)
    return start, start + timedelta(days=length)


def _old_window(days_ago: int = 530, length: int = 90) -> tuple[date, date]:
    """A ``[start, end)`` window that always routes to the archive API."""
    start = date.today() - timedelta(days=days_ago)
    return start, start + timedelta(days=length)


def _generic_rest_ds(
    endpoint: str = "https://example.com/api",
    query_params: dict | None = None,
) -> DatasetConfig:
    return DatasetConfig(
        name="example",
        api_type=ApiType.GENERIC_REST,
        endpoint=endpoint,
        query_params=query_params,
    )


# ── build_fetcher factory ────────────────────────────────────────────────────


def _geojson_ds() -> DatasetConfig:
    return DatasetConfig(
        name="job_zone_boundaries",
        api_type=ApiType.SOCRATA_GEOJSON,
        resource_id="aaaa-bbbb",
        domain="data.example-portal.gov",
        format="geojson",
    )


def test_build_fetcher_dispatches_to_socrata_class():
    fetcher = build_fetcher(
        _socrata_ds(), start=date(2026, 6, 1), end=date(2026, 6, 8), strategy="daily",
    )
    assert isinstance(fetcher, SocrataFetcher)


def test_build_fetcher_dispatches_to_socrata_geojson_class():
    fetcher = build_fetcher(
        _geojson_ds(), start=date(2026, 6, 1), end=date(2026, 6, 8), strategy="static",
    )
    assert isinstance(fetcher, SocrataGeoJsonFetcher)


def test_build_fetcher_dispatches_to_open_meteo_class():
    fetcher = build_fetcher(
        _open_meteo_ds(), start=date(2026, 6, 1), end=date(2026, 6, 8), strategy="daily",
    )
    assert isinstance(fetcher, OpenMeteoFetcher)


def test_build_fetcher_dispatches_to_generic_rest_class():
    fetcher = build_fetcher(
        _generic_rest_ds(), start=date(2026, 6, 1), end=date(2026, 6, 8), strategy="monthly",
    )
    assert isinstance(fetcher, GenericRestFetcher)


# A Socrata dataset partitioned `static` has no timestamp_field — the config
# layer forbids one — so the factory must build a whole-table fetcher for it.
# Getting this wrong is not a subtle failure: the windowed fetcher raises
# "missing timestamp_field" and the source can never be ingested at all.


def test_build_fetcher_makes_a_static_socrata_dataset_fetch_the_whole_table():
    ds = DatasetConfig(
        name="job_shifts", api_type=ApiType.SOCRATA,
        resource_id="aaaa-bbbb", domain="data.example-portal.gov",
    )
    fetcher = build_fetcher(
        ds, start=date(2026, 6, 1), end=date(2026, 6, 8), strategy="static",
    )
    assert isinstance(fetcher, SocrataFetcher)
    assert fetcher.full_table is True


def test_build_fetcher_keeps_a_windowed_socrata_dataset_windowed():
    fetcher = build_fetcher(
        _socrata_ds(), start=date(2026, 6, 1), end=date(2026, 6, 8), strategy="daily",
    )
    assert fetcher.full_table is False


# ── SocrataFetcher ───────────────────────────────────────────────────────────


def test_socrata_fetcher_init_raises_without_resource_id():
    # model_construct() bypasses Pydantic validation so we can verify the
    # fetcher's own defense-in-depth check fires.
    ds = DatasetConfig.model_construct(
        name="x", api_type=ApiType.SOCRATA, timestamp_field="t",
        resource_id=None, domain="d.com", endpoint=None, query_params=None, format=None,
    )
    with pytest.raises(ValueError, match="resource_id"):
        SocrataFetcher(ds, start=date(2026, 6, 1), end=date(2026, 6, 8))


def test_socrata_fetcher_init_raises_without_domain():
    ds = DatasetConfig.model_construct(
        name="x", api_type=ApiType.SOCRATA, timestamp_field="t",
        resource_id="abc", domain=None, endpoint=None, query_params=None, format=None,
    )
    with pytest.raises(ValueError, match="domain"):
        SocrataFetcher(ds, start=date(2026, 6, 1), end=date(2026, 6, 8))


def test_socrata_fetcher_init_raises_without_timestamp_field():
    ds = DatasetConfig.model_construct(
        name="x", api_type=ApiType.SOCRATA, timestamp_field=None,
        resource_id="abc", domain="d.com", endpoint=None, query_params=None, format=None,
    )
    with pytest.raises(ValueError, match="timestamp_field"):
        SocrataFetcher(ds, start=date(2026, 6, 1), end=date(2026, 6, 8))


def test_socrata_fetcher_fetch_calls_client_with_window():
    """fetch() converts start/end → datetime at midnight, then delegates to
    ``SocrataClient.fetch_all_paginated``."""
    with patch("ingestion.backfill.fetchers.socrata.SocrataClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.fetch_all_paginated.return_value = iter([{"x": 1}, {"x": 2}])
        mock_client_cls.return_value = mock_client

        fetcher = SocrataFetcher(_socrata_ds(), start=date(2026, 6, 1), end=date(2026, 6, 8))
        records = list(fetcher.fetch())

    assert records == [{"x": 1}, {"x": 2}]
    mock_client.fetch_all_paginated.assert_called_once()
    kwargs = mock_client.fetch_all_paginated.call_args.kwargs
    assert kwargs["timestamp_field"] == "opened_at"
    # The start_dt / end_dt should be datetimes, not bare dates.
    assert kwargs["start_dt"].date() == date(2026, 6, 1)
    assert kwargs["end_dt"].date() == date(2026, 6, 8)


def test_socrata_fetcher_windowed_fetch_is_ordered_by_id():
    """A windowed fetch pages too, so it needs the same stable order the
    whole-table walk does.

    Filtering does not make a Socrata result set ordered: any window wider than
    one page can repeat or drop rows at a page boundary without it. The 2019
    Silver backfill hit exactly that — one record duplicated inside a 3,585-row
    day — and only the primary-key assertion caught it.
    """
    with patch("ingestion.backfill.fetchers.socrata.SocrataClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.fetch_all_paginated.return_value = iter([{"x": 1}])
        mock_client_cls.return_value = mock_client

        fetcher = SocrataFetcher(_socrata_ds(), start=date(2026, 6, 1), end=date(2026, 6, 8))
        list(fetcher.fetch())

    assert mock_client.fetch_all_paginated.call_args.kwargs["order_by"] == ":id"


def test_socrata_fetcher_reads_app_token_from_env(monkeypatch):
    """``SOCRATA_APP_TOKEN`` is read at __init__ time, not earlier."""
    monkeypatch.setenv("SOCRATA_APP_TOKEN", "test-token-xyz")
    with patch("ingestion.backfill.fetchers.socrata.SocrataClient") as mock_client_cls:
        SocrataFetcher(_socrata_ds(), start=date(2026, 6, 1), end=date(2026, 6, 8))
        assert mock_client_cls.call_args.kwargs["app_token"] == "test-token-xyz"


def test_socrata_fetcher_handles_missing_app_token(monkeypatch):
    monkeypatch.delenv("SOCRATA_APP_TOKEN", raising=False)
    with patch("ingestion.backfill.fetchers.socrata.SocrataClient") as mock_client_cls:
        SocrataFetcher(_socrata_ds(), start=date(2026, 6, 1), end=date(2026, 6, 8))
        # None, not empty string
        assert mock_client_cls.call_args.kwargs["app_token"] is None


def test_socrata_fetcher_wraps_socrata_fetch_error_as_runtime_error():
    from ingestion.clients.socrata_client import SocrataFetchError

    with patch("ingestion.backfill.fetchers.socrata.SocrataClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.fetch_all_paginated.side_effect = SocrataFetchError("API down")
        mock_client_cls.return_value = mock_client

        fetcher = SocrataFetcher(_socrata_ds(), start=date(2026, 6, 1), end=date(2026, 6, 8))
        with pytest.raises(RuntimeError, match="Socrata fetch failed"):
            list(fetcher.fetch())


# ── SocrataGeoJsonFetcher ────────────────────────────────────────────────────


def test_socrata_geojson_fetcher_init_raises_without_resource_id():
    ds = DatasetConfig.model_construct(
        name="x", api_type=ApiType.SOCRATA_GEOJSON,
        resource_id=None, domain="x.com", format="geojson",
        timestamp_field=None, endpoint=None, query_params=None,
    )
    with pytest.raises(ValueError, match="resource_id"):
        SocrataGeoJsonFetcher(ds)


def test_socrata_geojson_fetcher_init_raises_without_domain():
    ds = DatasetConfig.model_construct(
        name="x", api_type=ApiType.SOCRATA_GEOJSON,
        resource_id="abc", domain=None, format="geojson",
        timestamp_field=None, endpoint=None, query_params=None,
    )
    with pytest.raises(ValueError, match="domain"):
        SocrataGeoJsonFetcher(ds)


def test_socrata_geojson_fetcher_walks_the_whole_table_ordered_by_id():
    """No time filter, and paginated rather than one capped page.

    ``$order=:id`` is asserted because unordered limit/offset paging can repeat
    or skip rows between pages, and a geometry table that quietly loses rows
    yields a spatial join that drops records without raising anywhere.
    """
    with patch("ingestion.backfill.fetchers.socrata_geojson.SocrataClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.fetch_all_paginated.return_value = iter([
            {"type": "Feature", "id": 1},
            {"type": "Feature", "id": 2},
        ])
        mock_client_cls.return_value = mock_client

        fetcher = SocrataGeoJsonFetcher(_geojson_ds())
        records = list(fetcher.fetch())

    assert records == [
        {"type": "Feature", "id": 1},
        {"type": "Feature", "id": 2},
    ]
    mock_client.fetch_all_paginated.assert_called_once_with(order_by=":id")


def test_socrata_fetcher_full_table_sends_no_time_filter():
    with patch("ingestion.backfill.fetchers.socrata.SocrataClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.fetch_all_paginated.return_value = iter([{"id": 1}])
        mock_client_cls.return_value = mock_client

        ds = DatasetConfig(
            name="job_shifts", api_type=ApiType.SOCRATA,
            resource_id="aaaa-bbbb", domain="data.example-portal.gov",
        )
        fetcher = SocrataFetcher(
            ds, start=date(2026, 6, 1), end=date(2026, 6, 8), full_table=True,
        )
        records = list(fetcher.fetch())

    assert records == [{"id": 1}]
    mock_client.fetch_all_paginated.assert_called_once_with(order_by=":id")


def test_socrata_fetcher_still_requires_a_timestamp_field_when_windowed():
    """A windowed dataset missing its timestamp_field must fail loudly.

    Inferring "no timestamp_field ⇒ fetch everything" would turn a `monthly`
    source whose config forgot the field into a full-table pull repeated once
    per month, writing the same whole table into every month's Bronze shard.
    """
    ds = DatasetConfig.model_construct(
        name="job_shifts", api_type=ApiType.SOCRATA,
        resource_id="aaaa-bbbb", domain="data.example-portal.gov",
        timestamp_field=None,
    )
    with pytest.raises(ValueError, match="missing timestamp_field"):
        SocrataFetcher(ds, start=date(2026, 6, 1), end=date(2026, 6, 8))


# ── OpenMeteoFetcher ─────────────────────────────────────────────────────────


def test_open_meteo_fetcher_init_raises_without_endpoint():
    # model_construct() bypasses Pydantic's api_type=open_meteo check so the
    # fetcher's own defense-in-depth check is the one that fires.
    ds = DatasetConfig.model_construct(
        name="x", api_type=ApiType.OPEN_METEO,
        endpoint=None, query_params=None, timestamp_field="t",
        resource_id=None, domain=None, format=None,
    )
    with pytest.raises(ValueError, match="endpoint"):
        OpenMeteoFetcher(ds, start=date(2026, 6, 1), end=date(2026, 6, 8))


def test_open_meteo_fetcher_passes_past_and_forecast_in_query_params():
    with patch("ingestion.backfill.fetchers.open_meteo.requests.get") as mock_get:
        mock_get.return_value.json.return_value = {"hourly": {"time": []}}
        mock_get.return_value.raise_for_status = MagicMock()

        start, end = _recent_window()
        fetcher = OpenMeteoFetcher(
            _open_meteo_ds(query_params={"latitude": 40.7, "longitude": -74.0}),
            start=start, end=end,
        )
        list(fetcher.fetch())

    params = mock_get.call_args.kwargs["params"]
    # past_days, forecast_days were added on top of the YAML defaults.
    assert "past_days" in params
    assert "forecast_days" in params
    # The YAML query_params are preserved.
    assert params["latitude"] == 40.7
    assert params["longitude"] == -74.0


def test_open_meteo_fetcher_flattens_hourly_response():
    """The Open-Meteo API returns ``{hourly: {time: [...], ...}}`` — the
    fetcher explodes this into one record per hour."""
    with patch("ingestion.backfill.fetchers.open_meteo.requests.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "hourly": {
                "time": ["2026-06-13T00:00", "2026-06-13T01:00"],
                "temperature_2m": [20.0, 21.5],
                "precipitation": [0.0, 0.1],
            },
        }
        mock_get.return_value.raise_for_status = MagicMock()

        start, end = _recent_window(days_ago=1, length=1)
        fetcher = OpenMeteoFetcher(_open_meteo_ds(), start=start, end=end)
        records = list(fetcher.fetch())

    assert records == [
        {"time": "2026-06-13T00:00", "temperature_2m": 20.0, "precipitation": 0.0},
        {"time": "2026-06-13T01:00", "temperature_2m": 21.5, "precipitation": 0.1},
    ]


def test_open_meteo_fetcher_routes_to_archive_for_old_windows(monkeypatch):
    """Windows starting more than MAX_PAST_DAYS ago must route to the archive
    API (archive-api.open-meteo.com/v1/archive) with start_date / end_date,
    NOT to the forecast API. No ValueError should be raised."""
    archive_response = {
        "hourly": {"time": ["2025-01-01T00:00"], "temperature_2m": [5.0]},
    }
    with patch("ingestion.backfill.fetchers.open_meteo.requests.get") as mock_get:
        mock_get.return_value.json.return_value = archive_response
        mock_get.return_value.raise_for_status = lambda: None
        start, end = _old_window()
        fetcher = OpenMeteoFetcher(_open_meteo_ds(), start=start, end=end)
        records = list(fetcher.fetch())
    assert records == [{"time": "2025-01-01T00:00", "temperature_2m": 5.0}]
    call_url = mock_get.call_args[0][0]
    assert call_url == ARCHIVE_BASE_URL, (
        f"Expected archive API URL {ARCHIVE_BASE_URL!r}, got {call_url!r}"
    )
    params = mock_get.call_args[1]["params"]
    assert params["start_date"] == start.isoformat()
    assert params["end_date"] == (end - timedelta(days=1)).isoformat()  # end is exclusive
    assert "past_days" not in params
    assert "forecast_days" not in params


@pytest.mark.parametrize(
    ("days_ago", "expect_archive"),
    [(MAX_PAST_DAYS - 1, False), (MAX_PAST_DAYS, False), (MAX_PAST_DAYS + 1, True)],
)
def test_open_meteo_routing_is_measured_from_today_not_from_a_calendar_date(
    days_ago: int, expect_archive: bool,
) -> None:
    """The forecast/archive boundary walks with the clock.

    🔴 The two paths do not fail differently — the archive path simply
    answers without ``past_days``. So a window that drifts across the
    boundary produces a *successful* call to the wrong API, which is how a
    test asserting ``past_days`` came to fail months after it was written
    while the fetcher was behaving correctly the whole time.
    """
    with patch("ingestion.backfill.fetchers.open_meteo.requests.get") as mock_get:
        mock_get.return_value.json.return_value = {"hourly": {"time": []}}
        mock_get.return_value.raise_for_status = MagicMock()

        start = date.today() - timedelta(days=days_ago)
        fetcher = OpenMeteoFetcher(
            _open_meteo_ds(), start=start, end=start + timedelta(days=1),
        )
        list(fetcher.fetch())

    used_archive = mock_get.call_args[0][0] == ARCHIVE_BASE_URL
    assert used_archive is expect_archive
    assert ("past_days" in mock_get.call_args.kwargs["params"]) is not expect_archive


def test_open_meteo_fetcher_raises_on_forecast_days_exceeding_limit(monkeypatch):
    monkeypatch.setattr(
        "ingestion.backfill.fetchers.open_meteo._window_to_past_forecast",
        lambda *args, **kwargs: (0, 30),  # 30 > MAX_FORECAST_DAYS (16)
    )
    with patch("ingestion.backfill.fetchers.open_meteo.requests.get") as mock_get:
        start, end = _recent_window(days_ago=30, length=31)
        fetcher = OpenMeteoFetcher(_open_meteo_ds(), start=start, end=end)
        with pytest.raises(ValueError, match="16"):
            list(fetcher.fetch())
        mock_get.assert_not_called()


def test_open_meteo_fetcher_propagates_http_error():
    with patch("ingestion.backfill.fetchers.open_meteo.requests.get") as mock_get:
        mock_get.return_value.raise_for_status.side_effect = RuntimeError("503 Service Unavailable")
        start, end = _recent_window(days_ago=1, length=1)
        fetcher = OpenMeteoFetcher(_open_meteo_ds(), start=start, end=end)
        with pytest.raises(RuntimeError, match="503"):
            list(fetcher.fetch())


# ── GenericRestFetcher ──────────────────────────────────────────────────────


def test_generic_rest_fetcher_init_raises_without_endpoint():
    ds = DatasetConfig.model_construct(
        name="x", api_type=ApiType.GENERIC_REST,
        endpoint=None, query_params=None, timestamp_field=None,
        resource_id=None, domain=None, format=None,
    )
    with pytest.raises(ValueError, match="endpoint"):
        GenericRestFetcher(ds, start=date(2026, 6, 1), end=date(2026, 6, 8))


def test_generic_rest_fetcher_passes_start_end_as_query_params():
    with patch("ingestion.backfill.fetchers.generic_rest.requests.get") as mock_get:
        mock_get.return_value.json.return_value = []
        mock_get.return_value.raise_for_status = MagicMock()

        fetcher = GenericRestFetcher(
            _generic_rest_ds(),
            start=date(2026, 6, 1), end=date(2026, 6, 8),
        )
        list(fetcher.fetch())

    params = mock_get.call_args.kwargs["params"]
    assert params["start_date"] == "2026-06-01"
    assert params["end_date"] == "2026-06-08"


def test_generic_rest_fetcher_does_not_override_existing_query_params():
    """If the YAML already specifies start_date / end_date, the fetcher
    must NOT overwrite them with the caller's window — caller intent wins."""
    with patch("ingestion.backfill.fetchers.generic_rest.requests.get") as mock_get:
        mock_get.return_value.json.return_value = []
        mock_get.return_value.raise_for_status = MagicMock()

        ds = _generic_rest_ds(query_params={"start_date": "2025-01-01", "end_date": "2025-12-31"})
        fetcher = GenericRestFetcher(
            ds, start=date(2026, 6, 1), end=date(2026, 6, 8),
        )
        list(fetcher.fetch())

    params = mock_get.call_args.kwargs["params"]
    assert params["start_date"] == "2025-01-01"
    assert params["end_date"] == "2025-12-31"


def test_generic_rest_fetcher_yields_each_item_from_list_response():
    with patch("ingestion.backfill.fetchers.generic_rest.requests.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {"id": 1, "v": "a"},
            {"id": 2, "v": "b"},
            {"id": 3, "v": "c"},
        ]
        mock_get.return_value.raise_for_status = MagicMock()

        fetcher = GenericRestFetcher(
            _generic_rest_ds(), start=date(2026, 6, 1), end=date(2026, 6, 8),
        )
        records = list(fetcher.fetch())

    assert records == [
        {"id": 1, "v": "a"},
        {"id": 2, "v": "b"},
        {"id": 3, "v": "c"},
    ]


def test_generic_rest_fetcher_yields_singleton_for_dict_response():
    with patch("ingestion.backfill.fetchers.generic_rest.requests.get") as mock_get:
        mock_get.return_value.json.return_value = {"data": "singleton"}
        mock_get.return_value.raise_for_status = MagicMock()

        fetcher = GenericRestFetcher(
            _generic_rest_ds(), start=date(2026, 6, 1), end=date(2026, 6, 8),
        )
        records = list(fetcher.fetch())

    assert records == [{"data": "singleton"}]


def test_generic_rest_fetcher_propagates_http_error():
    with patch("ingestion.backfill.fetchers.generic_rest.requests.get") as mock_get:
        mock_get.return_value.raise_for_status.side_effect = RuntimeError("404")
        fetcher = GenericRestFetcher(
            _generic_rest_ds(), start=date(2026, 6, 1), end=date(2026, 6, 8),
        )
        with pytest.raises(RuntimeError, match="404"):
            list(fetcher.fetch())


# ── Parametrized smoke: build_fetcher returns the right class for each api_type ──


@pytest.mark.parametrize(
    "api_type,strategy,expected_class",
    [
        (ApiType.SOCRATA, "daily", SocrataFetcher),
        (ApiType.SOCRATA_GEOJSON, "static", SocrataGeoJsonFetcher),
        (ApiType.OPEN_METEO, "daily", OpenMeteoFetcher),
        (ApiType.GENERIC_REST, "monthly", GenericRestFetcher),
    ],
    ids=["socrata", "socrata_geojson", "open_meteo", "generic_rest"],
)
def test_factory_dispatch_matrix(api_type, strategy, expected_class):
    """Round-trip: each api_type → the right concrete class."""
    if api_type == ApiType.SOCRATA:
        ds = _socrata_ds()
    elif api_type == ApiType.SOCRATA_GEOJSON:
        ds = _geojson_ds()
    elif api_type == ApiType.OPEN_METEO:
        ds = _open_meteo_ds()
    else:
        ds = _generic_rest_ds()
    fetcher = build_fetcher(
        ds, start=date(2026, 6, 1), end=date(2026, 6, 8), strategy=strategy,
    )
    assert isinstance(fetcher, expected_class)
