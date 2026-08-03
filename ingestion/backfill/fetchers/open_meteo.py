"""Open-Meteo fetcher — routes to forecast or archive API based on window age.

Two Open-Meteo APIs are used:
- Forecast API  (api.open-meteo.com/v1/forecast):  past_days ≤ 92 + forecast_days ≤ 16.
  Used only for recent/future windows that fall within those limits.
- Archive API   (archive-api.open-meteo.com/v1/archive): start_date / end_date params.
  Used for any window that starts more than MAX_PAST_DAYS days ago.
  Supports arbitrary historical dates (back to 1940-01-01).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import date
from typing import Any

import requests

from ingestion.backfill.fetchers.base import Fetcher
from ingestion.config import DatasetConfig

logger = logging.getLogger(__name__)

# Open-Meteo forecast API limits (free tier)
MAX_PAST_DAYS = 92
MAX_FORECAST_DAYS = 16

ARCHIVE_BASE_URL = "https://archive-api.open-meteo.com/v1/archive"

# Query params that are not valid on the archive API
_FORECAST_ONLY_PARAMS = {"past_days", "forecast_days"}


def _window_to_past_forecast(
    start: date,
    end: date,
    today: date,
) -> tuple[int, int]:
    """Translate a ``[start, end)`` window to ``(past_days, forecast_days)``.

    Both values are relative to *today*:
    - ``past_days``     = how many days before today the window starts.
    - ``forecast_days`` = how many days after today the window ends.

    If the window is entirely in the past, ``forecast_days = 0``.
    If entirely in the future, ``past_days = 0``.
    If it straddles today, both are > 0.
    """
    past = max((today - start).days, 0)
    forecast = max((end - today).days, 0)
    return past, forecast


def _uses_archive(start: date, today: date) -> bool:
    """True when the window starts before the forecast API's past_days limit."""
    return (today - start).days > MAX_PAST_DAYS


class OpenMeteoFetcher(Fetcher):
    """Fetch Open-Meteo data for the ``[start, end)`` window.

    Routing logic:
    - Window starts within the last MAX_PAST_DAYS days  → forecast API
      (uses past_days / forecast_days query params).
    - Window starts further back (historical backfill)   → archive API
      (uses start_date / end_date query params, no day-count limit).
    """

    def __init__(self, ds: DatasetConfig, start: date, end: date) -> None:
        if not ds.endpoint:
            raise ValueError(
                f"Open-Meteo dataset {ds.name!r} missing endpoint",
            )
        self.forecast_endpoint = ds.endpoint
        self.query_params = dict(ds.query_params or {})
        self.start = start
        self.end = end
        self.dataset_name = ds.name

    def fetch(self) -> Iterator[dict[str, Any]]:
        today = date.today()

        if _uses_archive(self.start, today):
            yield from self._fetch_archive()
        else:
            yield from self._fetch_forecast(today)

    # ── Archive path ──────────────────────────────────────────────────────────

    def _fetch_archive(self) -> Iterator[dict[str, Any]]:
        """Use archive API with start_date / end_date (arbitrary history)."""
        # end is exclusive; archive API's end_date is inclusive → subtract 1 day
        from datetime import timedelta
        end_inclusive = self.end - timedelta(days=1)

        # Strip forecast-only params that the archive endpoint does not accept
        params = {k: v for k, v in self.query_params.items() if k not in _FORECAST_ONLY_PARAMS}
        params["start_date"] = self.start.isoformat()
        params["end_date"] = end_inclusive.isoformat()

        logger.info(
            "Open-Meteo ARCHIVE fetch: dataset=%s window=[%s, %s) "
            "-> start_date=%s end_date=%s",
            self.dataset_name, self.start, self.end,
            params["start_date"], params["end_date"],
        )

        resp = requests.get(ARCHIVE_BASE_URL, params=params, timeout=120)
        resp.raise_for_status()
        yield from self._flatten_series(resp.json())

    # ── Forecast path ─────────────────────────────────────────────────────────

    def _fetch_forecast(self, today: date) -> Iterator[dict[str, Any]]:
        """Use forecast API with past_days / forecast_days (recent windows).

        A dataset that declares ``past_days`` / ``forecast_days`` in its
        ``query_params`` keeps them: an explicitly configured relative window
        wins over one derived from ``[start, end)``. This is what a forecast
        dataset needs — it is collected with ``partition_strategy: snapshot``,
        whose window is always ``[today, today + 1)`` and therefore describes
        the *collection date*, not the range of data being asked for. Deriving
        from it would silently shrink a configured 7-day outlook to 1 day.
        """
        configured_past = self.query_params.get("past_days")
        configured_forecast = self.query_params.get("forecast_days")
        if configured_past is not None or configured_forecast is not None:
            past_days = int(configured_past or 0)
            forecast_days = int(configured_forecast or 0)
        else:
            past_days, forecast_days = _window_to_past_forecast(
                self.start, self.end, today,
            )

        if past_days > MAX_PAST_DAYS:
            raise ValueError(
                f"Window starts {past_days} days before today; "
                f"Open-Meteo forecast API allows at most {MAX_PAST_DAYS} past_days. "
                f"Use a window within the last {MAX_PAST_DAYS} days, or let the "
                f"fetcher route to the archive API automatically.",
            )
        if forecast_days > MAX_FORECAST_DAYS:
            raise ValueError(
                f"Window ends {forecast_days} days after today; "
                f"Open-Meteo allows at most {MAX_FORECAST_DAYS} forecast_days",
            )

        params = dict(self.query_params)
        params["past_days"] = past_days
        params["forecast_days"] = forecast_days

        logger.info(
            "Open-Meteo FORECAST fetch: dataset=%s window=[%s, %s) "
            "-> past_days=%d forecast_days=%d",
            self.dataset_name, self.start, self.end, past_days, forecast_days,
        )

        resp = requests.get(self.forecast_endpoint, params=params, timeout=60)
        resp.raise_for_status()
        yield from self._flatten_series(resp.json())

    # ── Shared ────────────────────────────────────────────────────────────────

    @staticmethod
    def _flatten_series(data: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """Flatten an Open-Meteo response into one record per time step.

        Open-Meteo returns parallel arrays under a granularity key — ``hourly``
        for hourly variables, ``daily`` for aggregates like ``snowfall_sum``.
        Which one is present follows from the query params, so the granularity
        is read off the response rather than configured twice.

        Both are handled because the two weather datasets need different
        granularities: the forecast is hourly, the archive is daily (BO-3's
        snowfall event segmentation works on daily totals going back to 2008).
        """
        for granularity in ("hourly", "daily"):
            block = data.get(granularity)
            if not isinstance(block, dict) or not block.get("time"):
                continue
            times = block["time"]
            for i, t in enumerate(times):
                record: dict[str, Any] = {"time": t}
                for k, v in block.items():
                    if k == "time" or not isinstance(v, list):
                        continue
                    if i < len(v):
                        record[k] = v[i]
                yield record
            return

        logger.warning(
            "Open-Meteo response carried neither an 'hourly' nor a 'daily' "
            "block; keys were %s", sorted(data),
        )
