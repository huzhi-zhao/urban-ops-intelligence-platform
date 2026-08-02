"""Unit tests for spark.transforms.weather_forecast (no object storage/cluster needed).

Uses a local in-process SparkSession (master=local[1]) — no Spark cluster or
cloud credentials required, so this stays in tests/unit per Makefile's
test-unit target.
"""

from __future__ import annotations

from datetime import datetime

import pytest

pyspark = pytest.importorskip("pyspark")

from pyspark.sql import Row, SparkSession  # noqa: E402
from pyspark.sql.types import DoubleType, StringType, StructField, StructType  # noqa: E402

from spark.transforms.weather_forecast import (  # noqa: E402
    dedupe_by_freshness,
    enforce_schema,
    normalize_timestamps,
    parse_ingest_date,
    split_by_validity,
)

_SNAPSHOT_PATH = (
    "s3a://bucket/bronze/raw/SRC-Open-Meteo/weather_forecast/"
    "ingest_date=2026-06-28/data.ndjson.gz"
)


@pytest.fixture(scope="module")
def spark():
    session = (
        SparkSession.builder.master("local[1]")
        .appName("test_weather_forecast_transforms")
        .getOrCreate()
    )
    yield session
    session.stop()


def test_parse_ingest_date_reads_the_snapshot_partition(spark):
    df = spark.createDataFrame([Row(_source_file=_SNAPSHOT_PATH)])
    result = parse_ingest_date(df).collect()
    assert result[0]["ingest_date"] == "2026-06-28"


def test_parse_ingest_date_rejects_the_daily_layout(spark):
    """A daily-layout path means the job is pointed at the wrong dataset.

    It must raise rather than yield "": an empty ingest_date on every row leaves
    dedupe_by_freshness ordering by a constant, which still emits one row per
    hour and so produces a wrong table that looks right.
    """
    df = spark.createDataFrame(
        [
            Row(
                _source_file=(
                    "s3a://bucket/bronze/raw/SRC-Open-Meteo/weather_archive/"
                    "2026-06/data_2026-06-28.ndjson.gz"
                )
            )
        ]
    )
    with pytest.raises(ValueError, match="ingest_date="):
        parse_ingest_date(df)


def test_dedupe_by_freshness_keeps_latest_ingest_date(spark):
    df = spark.createDataFrame(
        [
            Row(time="2026-06-28T10:00", ingest_date="2026-06-27", temperature_2m=20.0),
            Row(time="2026-06-28T10:00", ingest_date="2026-06-28", temperature_2m=21.0),
        ]
    )
    result = dedupe_by_freshness(df).collect()
    assert len(result) == 1
    assert result[0]["temperature_2m"] == 21.0
    assert result[0]["ingest_date"] == "2026-06-28"


def test_normalize_timestamps_converts_local_wall_clock_to_utc(spark):
    # 2026-06-28T10:00 America/Winnipeg (CDT, UTC-5) -> 2026-06-28T15:00 UTC
    df = spark.createDataFrame([Row(time="2026-06-28T10:00:00")])
    result = normalize_timestamps(
        df, source_id="SRC-Open-Meteo", source_tz="America/Winnipeg"
    ).collect()
    assert result[0]["time_utc"] == datetime(2026, 6, 28, 15, 0, 0)
    assert result[0]["date"] == "2026-06-28"
    assert result[0]["source_id"] == "SRC-Open-Meteo"


def test_normalize_timestamps_honours_the_source_timezone(spark):
    """The tz is a parameter, so another deployment's wall clock shifts differently."""
    df = spark.createDataFrame([Row(time="2026-06-28T10:00:00")])
    result = normalize_timestamps(
        df, source_id="SRC-Open-Meteo", source_tz="America/New_York"
    ).collect()
    # EDT is UTC-4, one hour east of Winnipeg's CDT
    assert result[0]["time_utc"] == datetime(2026, 6, 28, 14, 0, 0)


def test_split_by_validity_rejects_out_of_range_and_null_timestamp(spark):
    df = spark.createDataFrame(
        [
            Row(
                time_utc=datetime(2026, 6, 28, 14, 0),
                temperature_2m=20.0, precipitation=1.0, snowfall=0.0, windspeed_10m=10.0,
            ),
            Row(
                time_utc=datetime(2026, 6, 28, 15, 0),
                temperature_2m=999.0, precipitation=1.0, snowfall=0.0, windspeed_10m=10.0,
            ),
            Row(
                time_utc=None,
                temperature_2m=20.0, precipitation=1.0, snowfall=0.0, windspeed_10m=10.0,
            ),
        ]
    )
    valid, rejected = split_by_validity(df)
    assert valid.count() == 1
    assert rejected.count() == 2
    reasons = {row["_reject_reason"] for row in rejected.collect()}
    assert reasons == {"temperature_2m_out_of_range", "null_time_utc"}


def test_enforce_schema_passes_through_matching_columns(spark):
    schema = StructType(
        [StructField("a", StringType(), True), StructField("b", DoubleType(), True)]
    )
    df = spark.createDataFrame([Row(b=1.0, a="x")])  # deliberately out of declared order
    result = enforce_schema(df, schema)
    assert result.columns == ["a", "b"]
    assert result.collect() == [Row(a="x", b=1.0)]


def test_enforce_schema_raises_on_missing_column():
    schema = StructType(
        [StructField("a", StringType(), True), StructField("b", DoubleType(), True)]
    )

    class _FakeDF:
        columns = ["a"]

    with pytest.raises(ValueError, match="missing=\\['b'\\]"):
        enforce_schema(_FakeDF(), schema)


def test_enforce_schema_raises_on_unexpected_column():
    schema = StructType([StructField("a", StringType(), True)])

    class _FakeDF:
        columns = ["a", "extra"]

    with pytest.raises(ValueError, match="unexpected=\\['extra'\\]"):
        enforce_schema(_FakeDF(), schema)
