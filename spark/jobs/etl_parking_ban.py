"""Bronze -> Silver ETL for SRC-WPG-PARKING-BAN / parking_bans.

Reads the single static Bronze NDJSON file, converts the two floating
timestamps from America/Winnipeg to UTC, and writes a 49-row Silver Parquet:

  s3a://{bucket}/silver/parking_ban/

Static source: no date partitioning, no incremental window, one file out.
Re-running overwrites the whole table.

``description`` and ``description_french`` are dropped: they are a bilingual
rendering of the same 3-value distinction ``ban_type_id`` already carries, and
free text in Silver is text everyone downstream is tempted to parse.
``ban_type_id`` itself stays a **string** — its domain is 1/2/4 with 3 absent,
so it is a code, not a quantity, and casting it to int invites someone to
average it.

🔴 Only 19 of these 49 bans have any plow-shift rows. The other 30 are not
missing data: a ban is a *declaration* and a shift is *execution*, and
season-long declarations (ANNUAL SNOW ROUTE) produce no per-zone shifts by
design. Every consumer must **left-join** from this table. An inner join
silently drops 61% of the bans and would read as a clean result.

Usage:
    spark-submit spark/jobs/etl_parking_ban.py --bucket uoip
"""

from __future__ import annotations

import argparse
import logging

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from spark.schemas.parking_ban_schemas import (
    PARKING_BAN_RAW_SCHEMA,
    PARKING_BAN_SILVER_SCHEMA,
)
from spark.transforms.reference_table import (
    assert_row_count,
    assert_unique,
    enforce_schema,
    split_by_validity,
)
from spark.transforms.timestamp_normalizer import localize_naive_to_utc

logger = logging.getLogger(__name__)

SOURCE_ID = "SRC-WPG-PARKING-BAN"
DATASET = "parking_bans"
SILVER_TABLE = "parking_ban"

# Mirrors source_timezone in contracts/api-contracts/winnipeg-parking-bans.yaml.
SOURCE_TZ = "America/Winnipeg"

# Measured 2026-08-02 against the full table, covering 2015-12-01 to 2026-04-02.
EXPECTED_ROW_COUNT = 49


def _bronze_path(bucket: str) -> str:
    return f"s3a://{bucket}/bronze/raw/{SOURCE_ID}/{DATASET}/data_static.ndjson.gz"


def _silver_path(bucket: str) -> str:
    return f"s3a://{bucket}/silver/{SILVER_TABLE}"


def _rejects_path(bucket: str) -> str:
    return f"s3a://{bucket}/silver/_rejects/{SILVER_TABLE}"


def run(spark: SparkSession, bucket: str) -> None:
    raw = spark.read.schema(PARKING_BAN_RAW_SCHEMA).json(_bronze_path(bucket))

    renamed = raw.withColumnRenamed("ban_start", "ban_start_utc").withColumnRenamed(
        "ban_end", "ban_end_utc"
    )
    localized = localize_naive_to_utc(renamed, "ban_start_utc", SOURCE_TZ)
    localized = localize_naive_to_utc(localized, "ban_end_utc", SOURCE_TZ)

    typed = localized.withColumn("source_id", F.lit(SOURCE_ID)).withColumn(
        "loaded_at", F.current_timestamp()
    )

    # ban_end_utc is nullable in Silver and so is not required here — an open
    # ban has no end yet, which is a state, not a defect.
    valid, rejected = split_by_validity(
        typed, required_fields=("id", "ban_start_utc", "ban_type_id")
    )
    valid = enforce_schema(valid, PARKING_BAN_SILVER_SCHEMA).cache()

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

    out_of_order = valid.filter(
        F.col("ban_end_utc").isNotNull() & (F.col("ban_start_utc") >= F.col("ban_end_utc"))
    ).count()
    if out_of_order:
        raise RuntimeError(
            f"{SOURCE_ID}/{DATASET}: {out_of_order} rows with "
            f"ban_start_utc >= ban_end_utc. Most likely one of the two "
            f"timestamps was not localized from {SOURCE_TZ}. Escalate per CLAUDE.md."
        )

    logger.info(
        "%s/%s: wrote %d Silver rows to %s",
        SOURCE_ID, DATASET, row_count, silver_path,
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
