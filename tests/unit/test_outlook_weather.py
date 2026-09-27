"""Unit tests for models.request_forecast.outlook_weather (H2-R12 batch A).

Standard library only, so this runs in ``.venv`` beside pyspark — which is the
point: the segmentation port is checked row for row against the Spark rule
that produced every event M1 was trained on. That comparison cannot run in
``.venv-ml`` (no pyspark) and the Spark rule cannot run in the chain.

Measured on production data, 2026-09-27: the port reproduces all 99
``dim_snowfall_event`` rows (dates, totals, peak, duration, minimum
temperature, accum_flag, severity). These tests pin the semantics that result
depends on, so a later edit cannot quietly break it.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

from models.request_forecast.outlook_weather import (
    DailyWeather,
    EventRule,
    OutlookWeatherError,
    SeverityBounds,
    check_contiguous,
    hourly_to_daily,
    segment_events,
    snow_season,
    splice,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTLOOK_CONFIG = yaml.safe_load((REPO_ROOT / "config/models/outlook.yaml").read_text())

PRODUCTION_RULE = EventRule.from_config(OUTLOOK_CONFIG["event_rule"])


def _hourly(day: str, hours: range, snow: float = 0.0, temp: float = -5.0) -> list[dict]:
    return [
        {"time": f"{day}T{h:02d}:00", "snowfall": snow, "temperature_2m": temp - h * 0.1}
        for h in hours
    ]


def _daily(start: date, snow: list[float | None], tmin: float = -10.0) -> list[DailyWeather]:
    return [
        DailyWeather(start + timedelta(days=i), s, tmin - i) for i, s in enumerate(snow)
    ]


def _to_daily(records: list[dict], min_hours: int = 23) -> list[DailyWeather]:
    return hourly_to_daily(
        records, time_field="time", snowfall_field="snowfall",
        temperature_field="temperature_2m", min_hours_per_day=min_hours,
    )


# ── hourly -> daily ─────────────────────────────────────────────────────────────


def test_hours_sum_to_daily_snowfall_and_take_the_minimum_temperature():
    days = _to_daily(_hourly("2026-11-15", range(24), snow=0.5, temp=-2.0))

    assert len(days) == 1
    assert days[0].day == date(2026, 11, 15)
    assert days[0].snowfall_cm == pytest.approx(12.0)
    assert days[0].min_temperature_c == pytest.approx(-2.0 - 2.3)


def test_a_partial_day_is_dropped_not_scaled():
    """The horizon's last day is routinely cut short; half a day's snow
    counted as a whole day would bias every horizon event low."""
    records = _hourly("2026-11-15", range(24), snow=1.0) + _hourly("2026-11-16", range(12), snow=1.0)

    assert [d.day for d in _to_daily(records)] == [date(2026, 11, 15)]


def test_a_dst_changeover_day_with_23_hours_still_counts():
    records = [r for r in _hourly("2026-03-08", range(24)) if not r["time"].endswith("02:00")]

    assert [d.day for d in _to_daily(records)] == [date(2026, 3, 8)]


def test_an_unparseable_timestamp_is_refused():
    with pytest.raises(OutlookWeatherError, match="unparseable"):
        _to_daily([{"time": "not-a-time", "snowfall": 0.0, "temperature_2m": 0.0}])


def test_a_day_with_no_snowfall_values_reports_none_not_zero():
    records = [{"time": f"2026-11-15T{h:02d}:00", "temperature_2m": -1.0} for h in range(24)]

    assert _to_daily(records)[0].snowfall_cm is None


# ── splice / contiguity ─────────────────────────────────────────────────────────


def test_the_forecast_wins_where_it_overlaps_the_archive():
    """The overlap is the forecast's own past_days. Keeping the archive there
    would put an outcome into a series meant to be what was known at issue."""
    observed = _daily(date(2026, 11, 1), [1.0] * 10)
    forecast = _daily(date(2026, 11, 8), [9.0] * 5)

    series = splice(observed, forecast)

    assert [d.day for d in series] == [date(2026, 11, 1) + timedelta(days=i) for i in range(12)]
    assert [d.snowfall_cm for d in series[7:]] == [9.0] * 5


def test_a_hole_in_the_series_is_refused():
    days = _daily(date(2026, 11, 1), [0.0, 0.0]) + _daily(date(2026, 11, 5), [0.0])

    with pytest.raises(OutlookWeatherError, match="gap"):
        check_contiguous(days)


# ── segmentation semantics ──────────────────────────────────────────────────────


def test_a_bridged_gap_day_extends_the_span_but_adds_no_snow():
    rule = EventRule("t", threshold_cm=3.0, gap_days=1)
    days = _daily(date(2026, 1, 10), [5.0, 1.0, 4.0])

    [event] = segment_events(days, rule)

    assert (event.start_date, event.end_date, event.duration_days) == (
        date(2026, 1, 10), date(2026, 1, 12), 3,
    )
    assert event.total_snowfall_cm == pytest.approx(9.0)


def test_rolling_accumulation_alone_makes_an_event_with_accum_flag():
    days = _daily(date(2026, 1, 1), [2.5] * 5)

    events = segment_events(days, PRODUCTION_RULE)

    assert len(events) == 1
    assert events[0].accum_flag is True
    assert events[0].peak_daily_snowfall_cm == pytest.approx(2.5)


def test_a_missing_calendar_day_counts_as_zero_in_the_rolling_window():
    """Range-based, not row-based: an absent day must not shrink the window."""
    rule = EventRule("t", threshold_cm=100.0, gap_days=0, accum_window_days=3,
                     accum_threshold_cm=6.0)
    days = [DailyWeather(date(2026, 1, 1), 4.0, -5.0), DailyWeather(date(2026, 1, 3), 1.0, -5.0)]

    # A row-based window over the two rows would sum 5.0 on 01-03 either way;
    # the case that distinguishes them needs the window to span the gap.
    assert segment_events(days, rule) == []
    days.append(DailyWeather(date(2026, 1, 2), 1.5, -5.0))
    assert len(segment_events(days, rule)) == 1


def test_severity_reproduces_the_dim_sql_and_extrapolates_beyond_the_cohort():
    bounds = SeverityBounds.from_cohort([1.0, 11.0], [-10.0, -30.0])

    assert bounds.score(6.0, -20.0) == pytest.approx(0.5)
    # Frozen bounds: a storm larger than any in the cohort scores above 1.
    assert bounds.score(21.0, -30.0) > 1.0


def test_a_missing_temperature_contributes_no_cold():
    bounds = SeverityBounds.from_cohort([0.0, 10.0], [0.0, -20.0])

    assert bounds.score(0.0, None) == pytest.approx(0.0)


@pytest.mark.parametrize(
    ("day", "season"),
    [(date(2026, 11, 1), "2026-2027"), (date(2027, 3, 5), "2026-2027"), (date(2026, 10, 31), "2025-2026")],
)
def test_snow_season_matches_the_dim_rule(day, season):
    assert snow_season(day, first_month=11) == season


# ── config ──────────────────────────────────────────────────────────────────────


def test_the_lookback_covers_the_rolling_window():
    assert OUTLOOK_CONFIG["observed_lookback_days"] >= PRODUCTION_RULE.accum_window_days - 1


# ── parity with the Spark rule ──────────────────────────────────────────────────

PARITY_CASES = {
    "single_day_hits": [0.0, 4.0, 5.0, 0.0, 0.0, 0.0, 3.0, 0.0],
    "gap_bridged": [5.0, 1.0, 4.0, 0.0, 0.0, 0.0, 0.0],
    "gap_too_wide": [5.0, 0.0, 0.0, 4.0],
    "accum_only": [2.5, 2.5, 2.5, 2.5, 2.5, 0.0, 0.0],
    "accum_and_single": [1.0, 2.0, 2.0, 2.0, 6.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    "null_days": [None, 4.0, None, 2.0, 2.0, 2.0, 2.0, None, 0.0],
    "all_quiet": [0.0, 0.5, 0.0, 1.0],
}


@pytest.fixture(scope="module")
def spark():
    pytest.importorskip("pyspark")
    from pyspark.sql import SparkSession

    session = (
        SparkSession.builder.master("local[1]").appName("test_outlook_weather").getOrCreate()
    )
    yield session
    session.stop()


@pytest.mark.parametrize("case", sorted(PARITY_CASES))
def test_the_port_agrees_with_the_spark_rule(spark, case):
    from pyspark.sql.types import DateType, DoubleType, StructField, StructType

    from spark.transforms.weather_archive import segment_snowfall_events

    days = _daily(date(2026, 1, 1), PARITY_CASES[case])
    schema = StructType([
        StructField("weather_date", DateType()),
        StructField("snowfall_sum_cm", DoubleType()),
        StructField("temperature_2m_min_c", DoubleType()),
    ])
    df = spark.createDataFrame(
        [(d.day, d.snowfall_cm, d.min_temperature_c) for d in days], schema=schema
    )
    rule = PRODUCTION_RULE

    theirs = sorted(
        (
            r["start_date"], r["end_date"], r["duration_days"],
            round(r["total_snowfall_cm"] or 0.0, 9), r["peak_daily_snowfall_cm"],
            r["min_temperature_c"], bool(r["accum_flag"]),
        )
        for r in segment_snowfall_events(
            df, source_id="SRC-TEST", threshold_cm=rule.threshold_cm,
            event_rule_version=rule.version, gap_days=rule.gap_days,
            accum_window_days=rule.accum_window_days,
            accum_threshold_cm=rule.accum_threshold_cm,
        ).collect()
    )
    ours = sorted(
        (
            e.start_date, e.end_date, e.duration_days, round(e.total_snowfall_cm, 9),
            e.peak_daily_snowfall_cm, e.min_temperature_c, e.accum_flag,
        )
        for e in segment_events(days, rule)
    )

    assert ours == theirs


def test_the_config_rule_is_the_rule_production_ran():
    """gap_days is not in the version string, so it is compared field by field."""
    pytest.importorskip("pyspark")
    from spark.jobs import etl_weather_archive as job

    assert PRODUCTION_RULE == EventRule(
        version=job.EVENT_RULE_VERSION,
        threshold_cm=job.DEFAULT_SNOWFALL_THRESHOLD_CM,
        gap_days=job.DEFAULT_SNOWFALL_GAP_DAYS,
        accum_window_days=job.DEFAULT_ACCUM_WINDOW_DAYS,
        accum_threshold_cm=job.DEFAULT_ACCUM_THRESHOLD_CM,
    )
