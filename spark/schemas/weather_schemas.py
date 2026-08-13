"""StructType definitions for SRC-Open-Meteo.

Two datasets with different grains and different Bronze layouts:

``weather_forecast``  hourly, ``partition_strategy: snapshot``
    A rolling forward window. Each collection date holds that day's view of the
    next several days, so the same ``time`` appears under several
    ``ingest_date=`` partitions with different values. That is not duplication —
    it is the forecast-revision series.

``weather_archive``   daily, ``partition_strategy: daily``
    Observed history back to 2008, one row per calendar day. Drives BO-3's
    snowfall event segmentation and M1's training set.

Spark must never read either with ``spark.read.json()`` schema inference
(AGENTS.md rule) — import the matching ``*_RAW_SCHEMA`` instead.
"""

from __future__ import annotations

from pyspark.sql.types import (
    DateType,
    DoubleType,
    IntegerType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

# ── Forecast (hourly) ─────────────────────────────────────────────────────────

# Bronze NDJSON shape, one record per hour.
# `time` is a local wall-clock string with no UTC offset — see contract.
WEATHER_FORECAST_RAW_SCHEMA = StructType(
    [
        StructField("time", StringType(), nullable=False),
        StructField("temperature_2m", DoubleType(), nullable=True),
        StructField("precipitation", DoubleType(), nullable=True),
        StructField("snowfall", DoubleType(), nullable=True),
        StructField("windspeed_10m", DoubleType(), nullable=True),
    ]
)

# Silver grain: one row per UTC hour, citywide. No region id — that join
# happens in the Gold layer against dim_geography.
WEATHER_FORECAST_SILVER_SCHEMA = StructType(
    [
        StructField("time_utc", TimestampType(), nullable=False),
        StructField("date", StringType(), nullable=False),  # partition column, YYYY-MM-DD
        StructField("temperature_2m", DoubleType(), nullable=True),
        StructField("precipitation", DoubleType(), nullable=True),
        StructField("snowfall", DoubleType(), nullable=True),
        StructField("windspeed_10m", DoubleType(), nullable=True),
        StructField("source_id", StringType(), nullable=False),
        StructField("ingest_date", StringType(), nullable=False),  # collection date, YYYY-MM-DD
        StructField("loaded_at", TimestampType(), nullable=False),
    ]
)


# ── Archive (daily) ───────────────────────────────────────────────────────────

# Bronze NDJSON shape, one record per calendar day. `time` is a bare
# YYYY-MM-DD date string: a daily aggregate has no clock time, so unlike the
# hourly feed there is no timezone conversion to do — only a parse. The API is
# asked for the data in the city's own timezone, so a "day" is already the
# local calendar day the operations team means by it.
WEATHER_ARCHIVE_RAW_SCHEMA = StructType(
    [
        StructField("time", StringType(), nullable=False),
        StructField("snowfall_sum", DoubleType(), nullable=True),
        StructField("temperature_2m_min", DoubleType(), nullable=True),
        StructField("temperature_2m_max", DoubleType(), nullable=True),
    ]
)

# Units are carried in the column names. Open-Meteo returns cm for snowfall and
# °C for temperature, and a unit that lives only in a doc gets lost at the first
# join — the load score's snowfall term is meaningless if someone reads cm as mm.
WEATHER_ARCHIVE_SILVER_SCHEMA = StructType(
    [
        StructField("weather_date", DateType(), nullable=False),
        StructField("date", StringType(), nullable=False),  # partition column, YYYY-MM-DD
        StructField("snowfall_sum_cm", DoubleType(), nullable=True),
        StructField("temperature_2m_min_c", DoubleType(), nullable=True),
        StructField("temperature_2m_max_c", DoubleType(), nullable=True),
        StructField("source_id", StringType(), nullable=False),
        StructField("loaded_at", TimestampType(), nullable=False),
    ]
)

# BO-3 snowfall events: runs of consecutive days at or over the threshold,
# collapsed into one row per event. Grain is the event, not the day.
SNOWFALL_EVENT_SCHEMA = StructType(
    [
        StructField("event_id", StringType(), nullable=False),
        StructField("start_date", DateType(), nullable=False),
        StructField("end_date", DateType(), nullable=False),
        StructField("duration_days", IntegerType(), nullable=False),
        StructField("total_snowfall_cm", DoubleType(), nullable=False),
        StructField("peak_daily_snowfall_cm", DoubleType(), nullable=False),
        StructField("min_temperature_c", DoubleType(), nullable=True),
        # Semantic version of the rule combination that produced this row
        # (ADR 0010 §5 O3 / design §8 O3, e.g. "v1-3cm-or-10d10cm"). A future
        # threshold change gets a new value; it never overwrites the meaning
        # of an existing one.
        StructField("event_rule_version", StringType(), nullable=False),
        StructField("source_id", StringType(), nullable=False),
        StructField("loaded_at", TimestampType(), nullable=False),
    ]
)
