"""Bronze -> Silver ETL for SRC-Open-Meteo / nyc_weather_forecast.

Reads the daily-split Bronze NDJSON files for a `[start, end)` window, dedupes
by forecast freshness, normalizes timestamps to UTC, and writes:

  s3a://{bucket}/silver/weather/date=YYYY-MM-DD/*.parquet          (valid rows)
  s3a://{bucket}/silver/_rejects/weather/date=YYYY-MM-DD/*.parquet (quarantined rows)

Idempotent: re-running the same window overwrites only the date partitions it
touches (`partitionOverwriteMode=dynamic`), never the whole table.

Two callers use this same job with different window sizes:
  - dag_silver_open_meteo.py (daily incremental): a narrow 7-day lookback
    window ending at execution_date, to absorb late forecast revisions
    (matching the Socrata lookback convention in AGENTS.md).
  - one-time full historical backfill (manual spark-submit, no DAG): an
    arbitrary wide [start, end) range covering all accumulated Bronze
    history, analogous to scripts/backfill/ for the Bronze layer.

Usage:
    # daily incremental (narrow window)
    spark-submit spark/jobs/etl_open_meteo.py \
        --bucket nyc-uoip-prod --start 2026-06-22 --end 2026-06-29

    # one-time full backfill (wide window)
    spark-submit spark/jobs/etl_open_meteo.py \
        --bucket nyc-uoip-prod --start 2024-01-01 --end 2026-06-29
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from spark.schemas.weather_schemas import WEATHER_RAW_SCHEMA, WEATHER_SILVER_SCHEMA
from spark.transforms.weather import (
    dedupe_by_freshness,
    enforce_schema,
    normalize_timestamps,
    parse_ingest_date,
    split_by_validity,
)

logger = logging.getLogger(__name__)

SOURCE_ID = "SRC-Open-Meteo"
DATASET = "nyc_weather_forecast"
MIN_EXPECTED_ROWS_PER_DAY = 12  # alert threshold: half of the ~24/day baseline


def _bronze_paths(bucket: str, start: date, end: date) -> list[str]:
    """Build the glob of daily Bronze files covering `[start, end)`.

    Spans month boundaries by deriving each day's own YYYY-MM folder rather
    than assuming the whole window falls in one month.
    """
    paths = []
    day = start
    while day < end:
        month_folder = day.strftime("%Y-%m")
        paths.append(
            f"s3a://{bucket}/bronze/raw/{SOURCE_ID}/{DATASET}/{month_folder}/data_{day.isoformat()}.ndjson"
        )
        day += timedelta(days=1)
    return paths


def run(spark: SparkSession, bucket: str, start: date, end: date) -> None:
    paths = _bronze_paths(bucket, start, end)
    raw = (
        spark.read.schema(WEATHER_RAW_SCHEMA)
        .json(paths)
        .withColumn("_source_file", F.input_file_name())
    )

    raw = parse_ingest_date(raw).drop("_source_file")
    deduped = dedupe_by_freshness(raw)
    normalized = normalize_timestamps(deduped, source_id=SOURCE_ID)
    valid, rejected = split_by_validity(normalized)
    valid = enforce_schema(valid, WEATHER_SILVER_SCHEMA)

    silver_path = f"s3a://{bucket}/silver/weather"
    rejects_path = f"s3a://{bucket}/silver/_rejects/weather"

    (
        valid.write.partitionBy("date")
        .option("partitionOverwriteMode", "dynamic")
        .mode("overwrite")
        .parquet(silver_path)
    )
    if rejected.take(1):
        (
            rejected.write.partitionBy("date")
            .option("partitionOverwriteMode", "dynamic")
            .mode("overwrite")
            .parquet(rejects_path)
        )

    window_days = (end - start).days
    row_count = valid.count()
    logger.info(
        "%s/%s: window=[%s, %s) days=%d valid_rows=%d rejected_rows=%d",
        SOURCE_ID, DATASET, start, end, window_days, row_count, rejected.count(),
    )
    min_expected = window_days * MIN_EXPECTED_ROWS_PER_DAY
    if row_count < min_expected:
        raise RuntimeError(
            f"{SOURCE_ID}/{DATASET}: only {row_count} valid Silver rows for "
            f"window=[{start}, {end}) (expected >= {min_expected}, baseline ~24/day) — "
            f"possible API outage or upstream schema change. Escalate per CLAUDE.md."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--start", required=True, type=date.fromisoformat, help="Inclusive")
    parser.add_argument("--end", required=True, type=date.fromisoformat, help="Exclusive")
    args = parser.parse_args()
    if args.start >= args.end:
        parser.error("--start must be before --end")

    spark = SparkSession.builder.appName(f"etl_open_meteo_{args.start}_{args.end}").getOrCreate()
    try:
        run(spark, bucket=args.bucket, start=args.start, end=args.end)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
