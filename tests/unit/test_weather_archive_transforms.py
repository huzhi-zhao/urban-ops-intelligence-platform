"""Unit tests for spark.transforms.weather_archive (no object storage/cluster needed).

Uses a local in-process SparkSession (master=local[1]), same as
test_weather_forecast_transforms.py — no Spark cluster or cloud credentials,
so this stays in tests/unit per Makefile's test-unit target.
"""

from __future__ import annotations

from datetime import date

import pytest

pyspark = pytest.importorskip("pyspark")

from pyspark.sql import Row, SparkSession  # noqa: E402
from pyspark.sql.types import DoubleType, StringType, StructField, StructType  # noqa: E402

from spark.schemas.weather_schemas import WEATHER_ARCHIVE_RAW_SCHEMA  # noqa: E402
from spark.transforms.weather_archive import (  # noqa: E402
    enforce_schema,
    normalize_archive_dates,
    segment_snowfall_events,
    split_by_validity,
)

SOURCE_ID = "SRC-Open-Meteo"
RULE_VERSION = "v1-3cm-or-10d10cm"


@pytest.fixture(scope="module")
def spark():
    session = (
        SparkSession.builder.master("local[1]")
        .appName("test_weather_archive_transforms")
        .getOrCreate()
    )
    yield session
    session.stop()


def _raw(spark, rows):
    """Bronze-shaped archive rows: bare date string + the three daily measures.

    Built with the real raw schema rather than inference, so a row whose
    measurements are all null still types as double instead of failing to infer.
    """
    return spark.createDataFrame(list(rows), schema=WEATHER_ARCHIVE_RAW_SCHEMA)


def _silver(spark, rows):
    """Post-normalize rows: (weather_date, snowfall_sum_cm, temperature_2m_min_c)."""
    return spark.createDataFrame(
        [
            Row(weather_date=d, snowfall_sum_cm=snow, temperature_2m_min_c=tmin)
            for d, snow, tmin in rows
        ]
    )


# ── normalize_archive_dates ───────────────────────────────────────────────────


def test_normalize_archive_dates_parses_and_renames(spark):
    df = _raw(spark, [("2026-01-15", 4.0, -20.0, -8.0)])
    result = normalize_archive_dates(df, source_id=SOURCE_ID).collect()[0]

    assert result["weather_date"] == date(2026, 1, 15)
    assert result["date"] == "2026-01-15"
    assert result["snowfall_sum_cm"] == 4.0
    assert result["temperature_2m_min_c"] == -20.0
    assert result["temperature_2m_max_c"] == -8.0
    assert result["source_id"] == SOURCE_ID
    assert result["loaded_at"] is not None


def test_normalize_archive_dates_drops_the_raw_time_column(spark):
    df = _raw(spark, [("2026-01-15", 4.0, -20.0, -8.0)])
    assert "time" not in normalize_archive_dates(df, source_id=SOURCE_ID).columns


def test_normalize_archive_dates_does_not_shift_the_day(spark):
    """No timezone conversion here — the API is queried in the city's own tz.

    A UTC conversion would move roughly a third of the year's rows back one day,
    which would misalign every snowfall total against the 311 workload it drives.
    """
    df = _raw(spark, [("2026-01-15", 0.0, -20.0, -8.0)])
    assert normalize_archive_dates(df, source_id=SOURCE_ID).collect()[0]["date"] == "2026-01-15"


def test_normalize_archive_dates_yields_null_for_an_unparseable_date(spark):
    df = _raw(spark, [("not-a-date", 1.0, -5.0, 0.0)])
    assert normalize_archive_dates(df, source_id=SOURCE_ID).collect()[0]["weather_date"] is None


# ── split_by_validity ─────────────────────────────────────────────────────────


def test_split_by_validity_rejects_unparseable_dates_and_out_of_range_values(spark):
    df = _raw(
        spark,
        [
            ("2026-01-15", 4.0, -20.0, -8.0),      # valid
            ("2026-01-16", 900.0, -20.0, -8.0),    # snowfall far past physical range
            ("not-a-date", 1.0, -5.0, 0.0),        # unparseable date
        ],
    )
    valid, rejected = split_by_validity(normalize_archive_dates(df, source_id=SOURCE_ID))

    assert valid.count() == 1
    assert rejected.count() == 2
    assert valid.collect()[0]["weather_date"] == date(2026, 1, 15)


def test_split_by_validity_keeps_rows_with_null_measurements(spark):
    """The archive genuinely has gaps; a null column must not drop the whole day."""
    df = _raw(spark, [("2026-01-15", None, -20.0, None)])
    valid, rejected = split_by_validity(normalize_archive_dates(df, source_id=SOURCE_ID))

    assert valid.count() == 1
    assert rejected.count() == 0


# ── segment_snowfall_events ───────────────────────────────────────────────────


def test_segment_snowfall_events_collapses_a_run_of_snowy_days(spark):
    df = _silver(
        spark,
        [
            (date(2026, 1, 10), 0.5, -10.0),   # below threshold
            (date(2026, 1, 11), 3.0, -12.0),
            (date(2026, 1, 12), 7.5, -18.0),
            (date(2026, 1, 13), 2.0, -14.0),
            (date(2026, 1, 14), 0.0, -9.0),    # below threshold
        ],
    )
    events = segment_snowfall_events(
        df, source_id=SOURCE_ID, threshold_cm=2.0, event_rule_version=RULE_VERSION
    ).collect()

    assert len(events) == 1
    event = events[0]
    assert event["start_date"] == date(2026, 1, 11)
    assert event["end_date"] == date(2026, 1, 13)
    assert event["duration_days"] == 3
    assert event["total_snowfall_cm"] == pytest.approx(12.5)
    assert event["peak_daily_snowfall_cm"] == 7.5
    assert event["min_temperature_c"] == -18.0
    assert event["event_id"] == "SNOW-20260111"
    assert event["source_id"] == SOURCE_ID


def test_segment_snowfall_events_splits_on_a_gap_by_default(spark):
    df = _silver(
        spark,
        [
            (date(2026, 1, 11), 3.0, -12.0),
            (date(2026, 1, 12), 0.0, -12.0),   # quiet day between two storms
            (date(2026, 1, 13), 4.0, -15.0),
        ],
    )
    events = segment_snowfall_events(
        df, source_id=SOURCE_ID, threshold_cm=2.0, event_rule_version=RULE_VERSION
    ).collect()

    assert len(events) == 2
    assert sorted(e["start_date"] for e in events) == [date(2026, 1, 11), date(2026, 1, 13)]


def test_segment_snowfall_events_bridges_a_gap_when_allowed(spark):
    """gap_days=1 makes a storm that pauses for a day one operational event."""
    df = _silver(
        spark,
        [
            (date(2026, 1, 11), 3.0, -12.0),
            (date(2026, 1, 12), 0.0, -12.0),
            (date(2026, 1, 13), 4.0, -15.0),
        ],
    )
    events = segment_snowfall_events(
        df, source_id=SOURCE_ID, threshold_cm=2.0, event_rule_version=RULE_VERSION, gap_days=1
    ).collect()

    assert len(events) == 1
    event = events[0]
    assert event["start_date"] == date(2026, 1, 11)
    assert event["end_date"] == date(2026, 1, 13)
    # Calendar span, not the count of qualifying days: the quiet middle day is
    # inside the event, so the operations team's "how long did it run" is 3.
    assert event["duration_days"] == 3
    # ...but the quiet day contributes no snow, since it never qualified.
    assert event["total_snowfall_cm"] == pytest.approx(7.0)


def test_segment_snowfall_events_keeps_a_lone_day_as_an_event(spark):
    df = _silver(spark, [(date(2026, 1, 11), 5.0, -12.0)])
    events = segment_snowfall_events(
        df, source_id=SOURCE_ID, threshold_cm=2.0, event_rule_version=RULE_VERSION
    ).collect()

    assert len(events) == 1
    assert events[0]["duration_days"] == 1


def test_segment_snowfall_events_includes_days_exactly_at_the_threshold(spark):
    df = _silver(spark, [(date(2026, 1, 11), 2.0, -12.0)])
    assert segment_snowfall_events(
        df, source_id=SOURCE_ID, threshold_cm=2.0, event_rule_version=RULE_VERSION
    ).count() == 1


def test_segment_snowfall_events_returns_nothing_when_no_day_qualifies(spark):
    df = _silver(spark, [(date(2026, 1, 11), 0.5, -12.0)])
    assert segment_snowfall_events(
        df, source_id=SOURCE_ID, threshold_cm=2.0, event_rule_version=RULE_VERSION
    ).count() == 0


def test_segment_snowfall_events_rejects_negative_arguments(spark):
    df = _silver(spark, [(date(2026, 1, 11), 5.0, -12.0)])

    with pytest.raises(ValueError, match="threshold_cm"):
        segment_snowfall_events(
            df, source_id=SOURCE_ID, threshold_cm=-1.0, event_rule_version=RULE_VERSION
        )
    with pytest.raises(ValueError, match="gap_days"):
        segment_snowfall_events(
            df, source_id=SOURCE_ID, threshold_cm=2.0,
            event_rule_version=RULE_VERSION, gap_days=-1,
        )


def test_segment_snowfall_events_stamps_the_rule_version(spark):
    df = _silver(spark, [(date(2026, 1, 11), 5.0, -12.0)])
    event = segment_snowfall_events(
        df, source_id=SOURCE_ID, threshold_cm=2.0, event_rule_version=RULE_VERSION
    ).collect()[0]

    assert event["event_rule_version"] == RULE_VERSION


def test_segment_snowfall_events_rejects_accum_args_given_alone(spark):
    """window without threshold (or vice versa) has no defined meaning."""
    df = _silver(spark, [(date(2026, 1, 11), 5.0, -12.0)])

    with pytest.raises(ValueError, match="accum_window_days"):
        segment_snowfall_events(
            df, source_id=SOURCE_ID, threshold_cm=2.0,
            event_rule_version=RULE_VERSION, accum_window_days=10,
        )
    with pytest.raises(ValueError, match="accum_window_days"):
        segment_snowfall_events(
            df, source_id=SOURCE_ID, threshold_cm=2.0,
            event_rule_version=RULE_VERSION, accum_threshold_cm=10.0,
        )


def test_segment_snowfall_events_accum_criterion_catches_a_sub_threshold_run(spark):
    """A storm spread thin enough that no single day crosses the daily
    threshold, but the 3-day rolling total does on its last day, is a hit on
    that day — the exact shape plow_without_snowfall (2026-08-09) found the
    single-day criterion structurally blind to. Only the day the rolling
    total actually clears the bar qualifies, mirroring
    scripts/analysis/snowfall_events.py::rolling_accumulation_hits — the
    criterion flags days, not runs, so days 10-11 here stay non-events.
    """
    df = _silver(
        spark,
        [
            (date(2026, 1, 10), 1.0, -10.0),  # below daily threshold
            (date(2026, 1, 11), 1.0, -12.0),  # below daily threshold
            (date(2026, 1, 12), 1.0, -14.0),  # below daily threshold; rolling 3d = 3.0
        ],
    )
    without_accum = segment_snowfall_events(
        df, source_id=SOURCE_ID, threshold_cm=2.0, event_rule_version=RULE_VERSION
    )
    assert without_accum.count() == 0

    with_accum = segment_snowfall_events(
        df, source_id=SOURCE_ID, threshold_cm=2.0, event_rule_version=RULE_VERSION,
        accum_window_days=3, accum_threshold_cm=3.0,
    ).collect()
    assert len(with_accum) == 1
    assert with_accum[0]["start_date"] == date(2026, 1, 12)
    assert with_accum[0]["end_date"] == date(2026, 1, 12)


def test_segment_snowfall_events_accum_window_does_not_leak_across_a_gap_in_days(spark):
    """A missing calendar day contributes 0 to the rolling total rather than
    silently shrinking the window to fewer days of real coverage.
    """
    df = _silver(
        spark,
        [
            (date(2026, 1, 1), 1.5, -10.0),   # below daily threshold, far outside day 10's window
            (date(2026, 1, 10), 1.0, -12.0),  # below daily threshold; rolling 3d = 1.0
        ],
    )
    events = segment_snowfall_events(
        df, source_id=SOURCE_ID, threshold_cm=2.0, event_rule_version=RULE_VERSION,
        accum_window_days=3, accum_threshold_cm=3.0,
    )
    assert events.count() == 0


# ── enforce_schema ────────────────────────────────────────────────────────────


def test_enforce_schema_projects_to_the_declared_order(spark):
    schema = StructType(
        [StructField("a", StringType(), True), StructField("b", DoubleType(), True)]
    )
    df = spark.createDataFrame([Row(b=1.0, a="x")])
    result = enforce_schema(df, schema)

    assert result.columns == ["a", "b"]
    assert result.collect() == [Row(a="x", b=1.0)]


def test_enforce_schema_raises_on_missing_column(spark):
    schema = StructType(
        [StructField("a", StringType(), True), StructField("b", DoubleType(), True)]
    )
    df = spark.createDataFrame([Row(a="x")])

    with pytest.raises(ValueError, match="missing expected column"):
        enforce_schema(df, schema)


def test_enforce_schema_raises_on_unexpected_column(spark):
    schema = StructType([StructField("a", StringType(), True)])
    df = spark.createDataFrame([Row(a="x", extra=1.0)])

    with pytest.raises(ValueError, match="unexpected column"):
        enforce_schema(df, schema)
