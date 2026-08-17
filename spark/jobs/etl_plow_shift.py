"""Bronze -> Silver ETL for SRC-WPG-PLOW-SHIFT / plow_shifts.

Reads the single static Bronze NDJSON file, interprets the two floating
timestamps as America/Winnipeg wall clock and converts them to UTC, casts
shift_number to int, and writes a 418-row Silver Parquet:

  s3a://{bucket}/silver/plow_shift/

Static source: no date partitioning, no incremental window, one file out
(coalesce(1)). Re-running always overwrites the whole table. Run manually
when Bronze is re-pulled — these reference tables get no ingest or Silver
DAG (design §4, "DAG 数量纪律").

Two things this job deliberately does not do:

- **No join to the parking bans.** ``snow_ban_id`` is carried through
  verbatim; resolving it is Gold's business. Silver does no cross-fact JOIN.
- **No duration or completion metric from shift_end_utc.** The upstream is a
  *plan*: shift_end is the end of a 12h template window, identical across
  every zone in the same shift_number, not the time the work finished
  (ADR 0008). A "clearing duration" derived here would look reasonable and
  be fiction.

Usage:
    spark-submit spark/jobs/etl_plow_shift.py --bucket uoip
"""

from __future__ import annotations

import argparse
import logging

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from spark.schemas.plow_shift_schemas import (
    PLOW_SHIFT_RAW_SCHEMA,
    PLOW_SHIFT_SILVER_SCHEMA,
)
from spark.transforms.reference_table import (
    assert_row_count,
    assert_unique,
    enforce_schema,
    split_by_validity,
)
from spark.transforms.timestamp_normalizer import localize_naive_to_utc

logger = logging.getLogger(__name__)

SOURCE_ID = "SRC-WPG-PLOW-SHIFT"
DATASET = "plow_shifts"
SILVER_TABLE = "plow_shift"

# Socrata floating timestamps: local wall clock, no offset, no 'Z'. Mirrors
# source_timezone in contracts/api-contracts/winnipeg-plow-shifts.yaml.
SOURCE_TZ = "America/Winnipeg"

# 19 city-wide plow operations x 22 scheduled zones, measured 2026-08-02.
EXPECTED_ROW_COUNT = 418
# The boundary table has 25 zones; X, B/D and Downtown have no scheduled
# shift at all and never appear here (launch 20260803 §7.2).
EXPECTED_ZONE_COUNT = 22


def _bronze_path(bucket: str) -> str:
    return f"s3a://{bucket}/bronze/raw/{SOURCE_ID}/{DATASET}/data_static.ndjson.gz"


def _silver_path(bucket: str) -> str:
    return f"s3a://{bucket}/silver/{SILVER_TABLE}"


def _rejects_path(bucket: str) -> str:
    return f"s3a://{bucket}/silver/_rejects/{SILVER_TABLE}"


def run(spark: SparkSession, bucket: str) -> None:
    raw = spark.read.schema(PLOW_SHIFT_RAW_SCHEMA).json(_bronze_path(bucket))

    renamed = raw.withColumnRenamed("shift_start", "shift_start_utc").withColumnRenamed(
        "shift_end", "shift_end_utc"
    )
    localized = localize_naive_to_utc(renamed, "shift_start_utc", SOURCE_TZ)
    localized = localize_naive_to_utc(localized, "shift_end_utc", SOURCE_TZ)

    typed = (
        localized.withColumn("shift_number", F.col("shift_number").cast("int"))
        .withColumn("source_id", F.lit(SOURCE_ID))
        .withColumn("loaded_at", F.current_timestamp())
    )

    valid, rejected = split_by_validity(
        typed,
        required_fields=(
            "id",
            "snow_ban_id",
            "shift_number",
            "shift_start_utc",
            "shift_end_utc",
            "plow_zone",
        ),
    )
    valid = enforce_schema(valid, PLOW_SHIFT_SILVER_SCHEMA).cache()

    silver_path = _silver_path(bucket)
    rejects_path = _rejects_path(bucket)

    valid.coalesce(1).write.mode("overwrite").parquet(silver_path)

    if rejected.take(1):
        rejected.coalesce(1).write.mode("overwrite").parquet(rejects_path)
        logger.warning(
            "%s/%s: %d rejected rows written to %s",
            SOURCE_ID, DATASET, rejected.count(), rejects_path,
        )

    row_count = assert_row_count(valid, EXPECTED_ROW_COUNT, f"{SOURCE_ID}/{DATASET}")
    assert_unique(valid, ("id",), f"{SOURCE_ID}/{DATASET}")

    zone_count = valid.select("plow_zone").distinct().count()
    if zone_count != EXPECTED_ZONE_COUNT:
        raise RuntimeError(
            f"{SOURCE_ID}/{DATASET}: {zone_count} distinct plow_zone values, "
            f"expected {EXPECTED_ZONE_COUNT}. Gold models the unscheduled "
            f"zones explicitly against this exact cardinality — a change here "
            f"invalidates dim_plow_zone.has_plow_schedule. Escalate per CLAUDE.md."
        )

    out_of_order = valid.filter(F.col("shift_start_utc") >= F.col("shift_end_utc")).count()
    if out_of_order:
        raise RuntimeError(
            f"{SOURCE_ID}/{DATASET}: {out_of_order} rows with "
            f"shift_start_utc >= shift_end_utc. Most likely one of the two "
            f"timestamps was not localized from {SOURCE_TZ}. Escalate per CLAUDE.md."
        )

    logger.info(
        "%s/%s: wrote %d Silver rows to %s (%d zones)",
        SOURCE_ID, DATASET, row_count, silver_path, zone_count,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bucket", required=True, help="Object-storage bucket name (no s3a:// prefix)"
    )
    args = parser.parse_args()

    spark = SparkSession.builder.appName(f"etl_{DATASET}").getOrCreate()
    try:
        run(spark, bucket=args.bucket)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
