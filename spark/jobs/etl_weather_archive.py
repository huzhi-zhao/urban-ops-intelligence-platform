"""Bronze -> Silver ETL for SRC-Open-Meteo / weather_archive, plus BO-3 snowfall events.

Reads the daily-split Bronze NDJSON files for a `[start, end)` window and writes:

  s3a://{bucket}/silver/weather_archive/date=YYYY-MM-DD/*.parquet          (valid rows)
  s3a://{bucket}/silver/_rejects/weather_archive/date=YYYY-MM-DD/*.parquet (rejects)
  s3a://{bucket}/silver/snowfall_events/*.parquet                          (events)

Idempotent for the daily table: re-running the same window overwrites only the
date partitions it touches (`partitionOverwriteMode=dynamic`).

The event table is different and deliberately so. A snowfall event can span the
window boundary, so it cannot be computed from a slice — segmenting
[Jan 1, Feb 1) and [Feb 1, Mar 1) separately would cut any storm running across
midnight on Jan 31 into two events. `--emit-events` therefore reads the *whole*
Silver archive table and replaces the event table outright. Run it after a
backfill, not on every incremental window.

Usage:
    # incremental window, daily table only
    spark-submit spark/jobs/etl_weather_archive.py \
        --bucket uoip --start 2026-07-01 --end 2026-08-01

    # full history, then rebuild the event table from the whole series
    spark-submit spark/jobs/etl_weather_archive.py \
        --bucket uoip --start 2008-01-01 --end 2026-08-02 \
        --emit-events --snowfall-threshold-cm 2.0
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

from pyspark.sql import SparkSession

from spark.schemas.weather_schemas import (
    SNOWFALL_EVENT_SCHEMA,
    WEATHER_ARCHIVE_RAW_SCHEMA,
    WEATHER_ARCHIVE_SILVER_SCHEMA,
)
from spark.transforms.weather_archive import (
    enforce_schema,
    normalize_archive_dates,
    segment_snowfall_events,
    split_by_validity,
)

logger = logging.getLogger(__name__)

SOURCE_ID = "SRC-Open-Meteo"
DATASET = "weather_archive"

# One row per calendar day, so a healthy window has as many rows as it has days.
# Anything below this fraction means whole days are missing, not that the
# weather was quiet.
MIN_EXPECTED_ROW_FRACTION = 0.9


def _bronze_paths(bucket: str, start: date, end: date) -> list[str]:
    """The daily Bronze files covering `[start, end)`.

    Spans month boundaries by deriving each day's own YYYY-MM folder rather
    than assuming the whole window falls in one month.
    """
    paths = []
    day = start
    while day < end:
        month_folder = day.strftime("%Y-%m")
        paths.append(
            f"s3a://{bucket}/bronze/raw/{SOURCE_ID}/{DATASET}/{month_folder}/"
            f"data_{day.isoformat()}.ndjson.gz"
        )
        day += timedelta(days=1)
    return paths


def run(
    spark: SparkSession,
    bucket: str,
    start: date,
    end: date,
    *,
    emit_events: bool = False,
    snowfall_threshold_cm: float = 2.0,
    snowfall_gap_days: int = 0,
) -> None:
    silver_path = f"s3a://{bucket}/silver/{DATASET}"
    rejects_path = f"s3a://{bucket}/silver/_rejects/{DATASET}"
    events_path = f"s3a://{bucket}/silver/snowfall_events"

    raw = spark.read.schema(WEATHER_ARCHIVE_RAW_SCHEMA).json(_bronze_paths(bucket, start, end))

    normalized = normalize_archive_dates(raw, source_id=SOURCE_ID)
    valid, rejected = split_by_validity(normalized)
    valid = enforce_schema(valid, WEATHER_ARCHIVE_SILVER_SCHEMA)

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
    rejected_count = rejected.count()
    logger.info(
        "%s/%s: window=[%s, %s) days=%d valid_rows=%d rejected_rows=%d",
        SOURCE_ID, DATASET, start, end, window_days, row_count, rejected_count,
    )

    min_expected = int(window_days * MIN_EXPECTED_ROW_FRACTION)
    if row_count < min_expected:
        raise RuntimeError(
            f"{SOURCE_ID}/{DATASET}: only {row_count} valid Silver rows for "
            f"window=[{start}, {end}) covering {window_days} days (expected "
            f">= {min_expected}, one row per day) — days are missing from Bronze, "
            f"or the upstream schema changed. Escalate per CLAUDE.md."
        )

    if not emit_events:
        return

    # Read back the full table rather than reusing `valid`: an event may start
    # before this window and end inside it, and segmenting a slice would report
    # a truncated event as a whole one.
    full_series = spark.read.parquet(silver_path)
    events = segment_snowfall_events(
        full_series,
        source_id=SOURCE_ID,
        threshold_cm=snowfall_threshold_cm,
        gap_days=snowfall_gap_days,
    )
    events = enforce_schema(events, SNOWFALL_EVENT_SCHEMA)
    events.write.mode("overwrite").parquet(events_path)

    event_count = events.count()
    logger.info(
        "%s: %d snowfall events at threshold=%.1fcm gap_days=%d over the full series",
        SOURCE_ID, event_count, snowfall_threshold_cm, snowfall_gap_days,
    )
    if event_count == 0:
        raise RuntimeError(
            f"{SOURCE_ID}: snowfall segmentation produced 0 events at "
            f"threshold={snowfall_threshold_cm}cm. The archive covers winters, so "
            f"zero means the threshold is wrong or snowfall_sum_cm did not load."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--start", required=True, type=date.fromisoformat, help="Inclusive")
    parser.add_argument("--end", required=True, type=date.fromisoformat, help="Exclusive")
    parser.add_argument(
        "--emit-events", action="store_true",
        help="Rebuild the snowfall event table from the whole Silver series.",
    )
    parser.add_argument(
        "--snowfall-threshold-cm", type=float, default=2.0,
        help=(
            "Daily snowfall at or above which a day counts toward an event. "
            "Policy rather than physics — moves to config/semantics/ in batch 5."
        ),
    )
    parser.add_argument(
        "--snowfall-gap-days", type=int, default=0,
        help="Below-threshold days tolerated inside one event (0 = strictly consecutive).",
    )
    args = parser.parse_args()
    if args.start >= args.end:
        parser.error("--start must be before --end")

    spark = (
        SparkSession.builder
        .appName(f"etl_weather_archive_{args.start}_{args.end}")
        .getOrCreate()
    )
    try:
        run(
            spark,
            bucket=args.bucket,
            start=args.start,
            end=args.end,
            emit_events=args.emit_events,
            snowfall_threshold_cm=args.snowfall_threshold_cm,
            snowfall_gap_days=args.snowfall_gap_days,
        )
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
