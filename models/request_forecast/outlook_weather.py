"""Forecast weather -> daily series -> snowfall events, for M1's forward chain (H2-R12).

Standard library only, on purpose. The chain's inference half needs pandas (the
``ml`` extra, ``.venv-ml``), but this half has to be compared, row for row,
against the Spark implementation that produced every event M1 was trained on —
and pyspark lives in ``.venv``, which never carries the ``ml`` extra (O15).
A module with no third-party imports runs in both, which is the only way the
parity test and the chain can exercise the same code.

Nothing here knows which city it runs for. Timezone, field names and the event
rule come from ``config/models/outlook.yaml``.

Design: docs/dev/design/20260927-forecast-chain-rehearsal.md §3.1–§3.3.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any


class OutlookWeatherError(ValueError):
    """The weather input cannot be turned into a trustworthy daily series."""


@dataclass(frozen=True)
class DailyWeather:
    """One local calendar day. ``snowfall_cm`` is None when the source had no value."""

    day: date
    snowfall_cm: float | None
    min_temperature_c: float | None


@dataclass(frozen=True)
class EventRule:
    """The segmentation rule in force, as production applied it.

    Mirrors the arguments of ``spark.transforms.weather_archive.
    segment_snowfall_events``. ``version`` is stamped onto the output so a
    later rule change can never be mistaken for this one (ADR 0010 §5 O3).
    """

    version: str
    threshold_cm: float
    gap_days: int
    accum_window_days: int | None = None
    accum_threshold_cm: float | None = None

    def __post_init__(self) -> None:
        if self.threshold_cm < 0:
            raise ValueError(f"threshold_cm must be non-negative, got {self.threshold_cm}")
        if self.gap_days < 0:
            raise ValueError(f"gap_days must be non-negative, got {self.gap_days}")
        if (self.accum_window_days is None) != (self.accum_threshold_cm is None):
            raise ValueError("accum_window_days and accum_threshold_cm must be given together")

    @classmethod
    def from_config(cls, section: Mapping[str, Any]) -> EventRule:
        return cls(
            version=str(section["version"]),
            threshold_cm=float(section["threshold_cm"]),
            gap_days=int(section["gap_days"]),
            accum_window_days=(
                int(section["accum_window_days"])
                if section.get("accum_window_days") is not None else None
            ),
            accum_threshold_cm=(
                float(section["accum_threshold_cm"])
                if section.get("accum_threshold_cm") is not None else None
            ),
        )


@dataclass(frozen=True)
class SnowfallEvent:
    """One event, with the same attributes ``dim_snowfall_event`` carries."""

    start_date: date
    end_date: date
    duration_days: int
    total_snowfall_cm: float
    peak_daily_snowfall_cm: float | None
    min_temperature_c: float | None
    accum_flag: bool
    event_rule_version: str


# ── hourly -> daily ─────────────────────────────────────────────────────────────


def hourly_to_daily(
    records: Iterable[Mapping[str, Any]],
    *,
    time_field: str,
    snowfall_field: str,
    temperature_field: str,
    min_hours_per_day: int,
) -> list[DailyWeather]:
    """Aggregate hourly forecast rows into local calendar days.

    ``time`` is the upstream's naive local wall clock (the API was queried in
    the city's timezone), so the date part *is* the local day — no UTC
    conversion, for the same reason ``normalize_archive_dates`` has none.

    Snowfall is summed, temperature takes the minimum, matching the archive's
    ``snowfall_sum`` / ``temperature_2m_min`` that M1 was trained on.

    Days with fewer than ``min_hours_per_day`` distinct hours are **dropped**,
    not scaled: a forecast window's last day is routinely cut short, and half a
    day's snowfall counted as a whole day would bias every event at the horizon
    low. ``min_hours_per_day`` is below 24 so a DST changeover day, which has 23
    local hours, still counts.

    Raises:
        OutlookWeatherError: on a malformed timestamp — a record we cannot
            place on a day is a broken input, not a row to skip.
    """
    hours: dict[date, set[str]] = defaultdict(set)
    snow: dict[date, list[float]] = defaultdict(list)
    temps: dict[date, list[float]] = defaultdict(list)

    for record in records:
        raw = record.get(time_field)
        try:
            stamp = datetime.fromisoformat(str(raw))
        except ValueError as exc:
            raise OutlookWeatherError(f"unparseable {time_field!r}: {raw!r}") from exc
        day = stamp.date()
        hours[day].add(stamp.strftime("%H:%M"))
        if record.get(snowfall_field) is not None:
            snow[day].append(float(record[snowfall_field]))
        if record.get(temperature_field) is not None:
            temps[day].append(float(record[temperature_field]))

    out: list[DailyWeather] = []
    for day in sorted(hours):
        if len(hours[day]) < min_hours_per_day:
            continue
        out.append(
            DailyWeather(
                day=day,
                snowfall_cm=sum(snow[day]) if snow[day] else None,
                min_temperature_c=min(temps[day]) if temps[day] else None,
            )
        )
    return out


def splice(observed: Iterable[DailyWeather], forecast: Iterable[DailyWeather]) -> list[DailyWeather]:
    """Observed days, then forecast days from the first forecast day on.

    Where the two overlap the forecast wins: the overlap is the forecast's own
    ``past_days`` window, and keeping the observed value there would let an
    outcome leak into a series that is supposed to be what was known at issue
    time. The observed side exists only to give the rolling-accumulation
    criterion the days *before* the forecast begins.
    """
    forecast = sorted(forecast, key=lambda d: d.day)
    if not forecast:
        return sorted(observed, key=lambda d: d.day)
    first = forecast[0].day
    head = [d for d in observed if d.day < first]
    return sorted(head, key=lambda d: d.day) + forecast


def check_contiguous(days: list[DailyWeather]) -> None:
    """Refuse a series with a hole in it.

    The Spark rule reads a missing calendar day as zero snowfall. That is right
    for an archive with a genuinely absent row, and wrong here, where a hole
    means a dropped partial day or a splice that did not meet — both of which
    would silently cut an event in two.
    """
    for prev, cur in zip(days, days[1:], strict=False):
        if (cur.day - prev.day).days != 1:
            raise OutlookWeatherError(
                f"daily series has a gap between {prev.day} and {cur.day}; "
                f"refusing to segment across it"
            )


# ── segmentation ────────────────────────────────────────────────────────────────


def segment_events(days: Iterable[DailyWeather], rule: EventRule) -> list[SnowfallEvent]:
    """Cut a daily series into snowfall events — a port of the Spark rule.

    Must agree with ``spark.transforms.weather_archive.segment_snowfall_events``
    on every input; ``tests/unit/test_outlook_weather.py`` runs both on the same
    rows. The semantics carried over, each of which is easy to get wrong:

    - The rolling window is **range-based over calendar days**: a day absent
      from the input contributes 0, it does not shrink the window.
    - A null snowfall day is not a single-day hit, but it can still be a
      rolling hit (Spark's ``null OR true`` is true).
    - Totals, peak and minimum temperature aggregate over **qualifying days
      only**. A bridged gap day extends the calendar span but adds no snowfall.
    - ``duration_days`` is the calendar span, not the number of qualifying days.
    - ``accum_flag`` is ``peak < threshold``, defined on the aggregate.
    """
    series = sorted(days, key=lambda d: d.day)
    snow_by_day = {d.day: d.snowfall_cm for d in series}

    def rolling_total(day: date) -> float:
        assert rule.accum_window_days is not None
        return sum(
            snow_by_day.get(day - timedelta(days=i)) or 0.0
            for i in range(rule.accum_window_days)
        )

    qualifying: list[DailyWeather] = []
    for d in series:
        single = d.snowfall_cm is not None and d.snowfall_cm >= rule.threshold_cm
        rolling = (
            rule.accum_threshold_cm is not None
            and rolling_total(d.day) >= rule.accum_threshold_cm
        )
        if single or rolling:
            qualifying.append(d)

    runs: list[list[DailyWeather]] = []
    for d in qualifying:
        if runs and (d.day - runs[-1][-1].day).days <= rule.gap_days + 1:
            runs[-1].append(d)
        else:
            runs.append([d])
    return [_to_event(run, rule) for run in runs]


def _to_event(run: list[DailyWeather], rule: EventRule) -> SnowfallEvent:
    snow = [d.snowfall_cm for d in run if d.snowfall_cm is not None]
    temps = [d.min_temperature_c for d in run if d.min_temperature_c is not None]
    peak = max(snow) if snow else None
    return SnowfallEvent(
        start_date=run[0].day,
        end_date=run[-1].day,
        duration_days=(run[-1].day - run[0].day).days + 1,
        # Spark's SUM over all-null is null; an event only exists because some
        # day qualified, so at least the rolling criterion saw snow. Report 0.0
        # rather than None, and let accum_flag carry the "no single day" fact.
        total_snowfall_cm=sum(snow) if snow else 0.0,
        peak_daily_snowfall_cm=peak,
        min_temperature_c=min(temps) if temps else None,
        # Spark: peak < threshold, and NULL < x is NULL -> falsy in the output.
        accum_flag=peak is not None and peak < rule.threshold_cm,
        event_rule_version=rule.version,
    )


# ── severity ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SeverityBounds:
    """The min/max that ``dim_snowfall_event.severity_score`` normalised over.

    🔴 **Frozen, never recomputed with the new event included.** The dim DML
    min-max-normalises over its cohort, so adding an event moves the bounds and
    changes the severity of every historical event with it — including the ones
    the model's coefficient was fitted on. A forecast event is scored on the
    training cohort's scale, and may land outside [0, 1]; that is extrapolation,
    and it is flagged, not clipped.
    """

    snow_lo: float
    snow_hi: float
    cold_lo: float
    cold_hi: float
    snow_weight: float = 0.5

    @classmethod
    def from_cohort(
        cls,
        totals: Iterable[float],
        min_temperatures: Iterable[float | None],
        snow_weight: float = 0.5,
    ) -> SeverityBounds:
        totals = list(totals)
        colds = [cold_degrees(t) for t in min_temperatures]
        if not totals or not colds:
            raise ValueError("severity bounds need a non-empty cohort")
        return cls(min(totals), max(totals), min(colds), max(colds), snow_weight)

    def score(self, total_snowfall_cm: float, min_temperature_c: float | None) -> float:
        """Same arithmetic as ``sql/dml/dim_snowfall_event.sql``, term for term."""
        snow = (
            0.0 if self.snow_hi == self.snow_lo
            else (total_snowfall_cm - self.snow_lo) / (self.snow_hi - self.snow_lo)
        )
        cold_c = cold_degrees(min_temperature_c)
        cold = (
            0.0 if self.cold_hi == self.cold_lo
            else (cold_c - self.cold_lo) / (self.cold_hi - self.cold_lo)
        )
        return self.snow_weight * snow + (1.0 - self.snow_weight) * cold


def cold_degrees(min_temperature_c: float | None) -> float:
    """``GREATEST(0, -COALESCE(min_temperature_c, 0))`` — C16's rule."""
    return max(0.0, -(min_temperature_c if min_temperature_c is not None else 0.0))


def snow_season(day: date, first_month: int) -> str:
    """Season label, as ``dim_snowfall_event.snow_season`` builds it.

    ``first_month`` is the month a season starts in (11 for Winnipeg); it is
    configuration, because where a winter begins is a property of the city.
    """
    start_year = day.year if day.month >= first_month else day.year - 1
    return f"{start_year}-{start_year + 1}"
