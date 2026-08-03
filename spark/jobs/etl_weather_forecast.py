"""Bronze -> Silver ETL for SRC-Open-Meteo / weather_forecast (hourly outlook).

Reads the `snapshot` Bronze partitions for a `[start, end)` window of **collection
dates**, keeps the freshest revision of each forecast hour, normalizes timestamps
to UTC, and writes:

  s3a://{bucket}/silver/weather_forecast/date=YYYY-MM-DD/*.parquet          (valid)
  s3a://{bucket}/silver/_rejects/weather_forecast/date=YYYY-MM-DD/*.parquet (rejects)

Two windows, two meanings — do not confuse them:
  - `--start` / `--end` select **collection dates** (`ingest_date=` partitions).
  - the output `date=` partitions are **record dates**, and a single collection
    date contributes rows to ~11 of them (past_days 3 + forecast_days 7).
    A run therefore rewrites record-date partitions outside its own window,
    which is correct: a later collection revises the earlier forecast.

Reads the whole dataset via a glob and filters on the parsed collection date,
rather than enumerating one path per day. A snapshot day that was never collected
cannot be recovered (CLAUDE.md), so missing partitions are normal and must not
fail the read the way an enumerated missing path would.

No consumer yet: the forecast feeds M1 inference, which is not built. There is
deliberately no DAG for this job — Bronze collection runs on the storage node
under `ingestion/snapshot/`, not Airflow, and a Silver table nothing reads is not
worth a scheduled run. Invoke it by hand until M1 exists.

Usage:
    spark-submit spark/jobs/etl_weather_forecast.py \
        --bucket uoip --start 2026-07-01 --end 2026-08-02
"""

from __future__ import annotations

import argparse
import logging
from datetime import date

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from spark.schemas.weather_schemas import (
    WEATHER_FORECAST_RAW_SCHEMA,
    WEATHER_FORECAST_SILVER_SCHEMA,
)
from spark.transforms.weather_forecast import (
    dedupe_by_freshness,
    enforce_schema,
    normalize_timestamps,
    parse_ingest_date,
    split_by_validity,
)

logger = logging.getLogger(__name__)

SOURCE_ID = "SRC-Open-Meteo"
DATASET = "weather_forecast"

# Mirrors config/sources/open_meteo.yaml -> datasets[weather_forecast].query_params.timezone.
# Open-Meteo returns naive wall clock in the timezone it was queried with, so this
# must track that field; the two disagreeing shifts every row by hours, silently.
SOURCE_TZ = "America/Winnipeg"

# One collection covers past_days + forecast_days at hourly grain (~264 rows).
# Half of one day's worth is a floor no healthy collection can fall under.
MIN_EXPECTED_ROWS_PER_COLLECTION = 12


def _bronze_glob(bucket: str) -> str:
    """Glob covering every collection date of the forecast dataset."""
    return f"s3a://{bucket}/bronze/raw/{SOURCE_ID}/{DATASET}/ingest_date=*/data.ndjson.gz"


def run(spark: SparkSession, bucket: str, start: date, end: date) -> None:
    raw = (
        spark.read.schema(WEATHER_FORECAST_RAW_SCHEMA)
        .json(_bronze_glob(bucket))
        .withColumn("_source_file", F.input_file_name())
    )

    raw = parse_ingest_date(raw).drop("_source_file")
    windowed = raw.filter(
        (F.col("ingest_date") >= F.lit(start.isoformat()))
        & (F.col("ingest_date") < F.lit(end.isoformat()))
    ).cache()

    collection_dates = windowed.select("ingest_date").distinct().count()
    if collection_dates == 0:
        raise RuntimeError(
            f"{SOURCE_ID}/{DATASET}: no collection dates in [{start}, {end}). "
            f"Either the window predates snapshot collection, or days were missed — "
            f"a missed snapshot day is unrecoverable. See docs/guide/snapshot-collection.md."
        )

    deduped = dedupe_by_freshness(windowed)
    normalized = normalize_timestamps(deduped, source_id=SOURCE_ID, source_tz=SOURCE_TZ)
    valid, rejected = split_by_validity(normalized)
    valid = enforce_schema(valid, WEATHER_FORECAST_SILVER_SCHEMA)

    silver_path = f"s3a://{bucket}/silver/{DATASET}"
    rejects_path = f"s3a://{bucket}/silver/_rejects/{DATASET}"

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

    row_count = valid.count()
    logger.info(
        "%s/%s: collection_dates=[%s, %s) found=%d valid_rows=%d rejected_rows=%d",
        SOURCE_ID, DATASET, start, end, collection_dates, row_count, rejected.count(),
    )
    min_expected = collection_dates * MIN_EXPECTED_ROWS_PER_COLLECTION
    if row_count < min_expected:
        raise RuntimeError(
            f"{SOURCE_ID}/{DATASET}: only {row_count} valid Silver rows from "
            f"{collection_dates} collection date(s) in [{start}, {end}) "
            f"(expected >= {min_expected}) — possible API outage or upstream "
            f"schema change. Escalate per CLAUDE.md."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", required=True)
    parser.add_argument(
        "--start", required=True, type=date.fromisoformat,
        help="Inclusive collection date",
    )
    parser.add_argument(
        "--end", required=True, type=date.fromisoformat,
        help="Exclusive collection date",
    )
    args = parser.parse_args()
    if args.start >= args.end:
        parser.error("--start must be before --end")

    spark = (
        SparkSession.builder
        .appName(f"etl_weather_forecast_{args.start}_{args.end}")
        .getOrCreate()
    )
    try:
        run(spark, bucket=args.bucket, start=args.start, end=args.end)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
