"""Bronze -> Silver ETL for SRC-WPG-SNOW / snow_clearing_status.

Collapses ~237,867 address x street-side records to one row per plow zone by
point-in-polygon count against silver_plow_zone_boundary, and writes:

  s3a://{bucket}/silver/snow_clearing_address/

This is the only Silver job that reads a ``snapshot`` Bronze layout besides
the weather forecast, and the only Silver lineage
``dim_plow_zone.address_count`` has — BO-6's normalization denominator, which
the objectives call load-bearing rather than decorative: without it,
r(rank, request volume) inflates from +0.017 to +0.139 and the three-factor
independence argument stops holding.

**One snapshot date, not a time series.** The upstream overwrites in place, so
each ``ingest_date=`` partition is the only copy of that day's state that will
ever exist. But the *product* here is a point-in-time address count used to
normalize a decade of historical scoring — Gold reads the latest
``snapshot_date`` only. This job therefore builds exactly one snapshot date
per run and overwrites the table with it, rather than folding every collected
day into one table. Default is the freshest collection available; any past
collection day can be rebuilt with ``--snapshot-date``, since Bronze keeps
them all.

The zone attribution goes through spark.transforms.zone_assignment, the same
implementation the service-request job uses. That sharing is not tidiness:
BO-4's spatial hit rate is only comparable between the two tables if both
counted the same way, against the same repaired geometries.

Usage:
    spark-submit spark/jobs/etl_snow_clearing_address.py --bucket uoip
    spark-submit spark/jobs/etl_snow_clearing_address.py --bucket uoip \\
        --snapshot-date 2026-08-15

Note: this job carries a Python UDF, so it needs the three extra --conf flags
documented in spark/jobs/etl_plow_zone_boundary.py and dags/_spark_common.py.
"""

from __future__ import annotations

import argparse
import logging
from datetime import date

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from spark.schemas.snow_clearing_address_schemas import (
    SNOW_CLEARING_ADDRESS_SILVER_SCHEMA,
)
from spark.transforms.reference_table import assert_row_count, assert_unique, enforce_schema
from spark.transforms.snapshot_partition import parse_ingest_date
from spark.transforms.zone_assignment import (
    add_point_coordinates,
    assign_zone,
    build_zone_polygons,
    spatial_hit_rate,
)

logger = logging.getLogger(__name__)

SOURCE_ID = "SRC-WPG-SNOW"
DATASET = "snow_clearing_status"
SILVER_TABLE = "snow_clearing_address"

# The GeoJSON Point column on g3p4-h83y (docs/dev/requirements/
# winnipeg-data-sources.md §4.3). Winnipeg-specific field name, which is why
# it lives in the job and not in the transform.
GEOMETRY_FIELD = "location"
ZONE_FIELD = "plow_zone"

# One row per plow zone. The boundary table has 25 zones and every one of them
# contains addresses, including the three with no plow schedule.
EXPECTED_ZONE_COUNT = 25

# Total g3p4-h83y records measured 2026-08-02. Used only as a floor check: a
# collection that returned a fraction of this is a truncated fetch, and
# writing it would understate every zone's denominator at once.
MIN_EXPECTED_ADDRESS_ROWS = 200_000


def _bronze_glob(bucket: str) -> str:
    """Glob over every collection date.

    A glob rather than an enumerated path per day: a snapshot day that was
    never collected cannot be recovered, so missing partitions are normal and
    must not fail the read the way a missing enumerated path would.
    """
    return f"s3a://{bucket}/bronze/raw/{SOURCE_ID}/{DATASET}/ingest_date=*/data.ndjson.gz"


def _boundary_path(bucket: str) -> str:
    return f"s3a://{bucket}/silver/plow_zone_boundary"


def _silver_path(bucket: str) -> str:
    return f"s3a://{bucket}/silver/{SILVER_TABLE}"


def run(spark: SparkSession, bucket: str, snapshot_date: date | None = None) -> None:
    # Schema inference is intentional: only the geometry column is needed and
    # it is a nested GeoJSON struct, the same reason etl_plow_zone_boundary.py
    # infers rather than declares. The declared RAW schema documents the shape.
    raw = spark.read.json(_bronze_glob(bucket)).withColumn(
        "_source_file", F.input_file_name()
    )
    raw = parse_ingest_date(raw, label="The address-snapshot dataset")

    if snapshot_date is None:
        latest = raw.agg(F.max("ingest_date").alias("d")).collect()[0]["d"]
        if latest is None:
            raise RuntimeError(
                f"{SOURCE_ID}/{DATASET}: no collection dates found under "
                f"{_bronze_glob(bucket)}. Snapshot collection runs on the "
                f"storage node outside Airflow — see docs/guide/snapshot-collection.md."
            )
        target = latest
    else:
        target = snapshot_date.isoformat()

    addresses = raw.filter(F.col("ingest_date") == F.lit(target)).cache()

    address_rows = addresses.count()
    if address_rows == 0:
        raise RuntimeError(
            f"{SOURCE_ID}/{DATASET}: collection date {target} has no rows. "
            f"That day was never collected and cannot be re-collected; pick "
            f"another --snapshot-date. See docs/guide/snapshot-collection.md."
        )
    if address_rows < MIN_EXPECTED_ADDRESS_ROWS:
        raise RuntimeError(
            f"{SOURCE_ID}/{DATASET}: collection date {target} has only "
            f"{address_rows} rows (expected >= {MIN_EXPECTED_ADDRESS_ROWS}). "
            f"A truncated collection would understate every zone's address "
            f"denominator at once. Escalate per CLAUDE.md."
        )

    boundary = spark.read.parquet(_boundary_path(bucket))
    zone_polygons = build_zone_polygons(boundary, zone_field=ZONE_FIELD)

    located = add_point_coordinates(addresses, geometry_field=GEOMETRY_FIELD)
    assigned = assign_zone(located, zone_polygons, zone_col=ZONE_FIELD).cache()

    matched, has_geo = spatial_hit_rate(assigned)
    logger.info(
        "%s/%s: snapshot_date=%s addresses=%d with_geo=%d matched=%d (%.2f%%)",
        SOURCE_ID, DATASET, target, address_rows, has_geo, matched,
        100.0 * matched / has_geo if has_geo else 0.0,
    )

    counted = (
        assigned.filter(F.col(ZONE_FIELD).isNotNull())
        .groupBy(ZONE_FIELD)
        .agg(F.count(F.lit(1)).alias("address_count"))
        .withColumn("snapshot_date", F.lit(target).cast("date"))
        .withColumn("source_id", F.lit(SOURCE_ID))
        .withColumn("loaded_at", F.current_timestamp())
    )
    counted = enforce_schema(counted, SNOW_CLEARING_ADDRESS_SILVER_SCHEMA).cache()

    assert_row_count(counted, EXPECTED_ZONE_COUNT, f"{SOURCE_ID}/{DATASET}")
    assert_unique(counted, (ZONE_FIELD, "snapshot_date"), f"{SOURCE_ID}/{DATASET}")

    silver_path = _silver_path(bucket)
    # Flat, unpartitioned, whole-table overwrite — the table holds exactly one
    # snapshot date at a time (25 rows).
    #
    # Not partitioned by snapshot_date, though the grain would suggest it:
    # sql/ddl/silver_snow_clearing_address.sql declares no `partitioned_by`,
    # so a partitionBy() write would emit snapshot_date=.../ directories and
    # drop the column out of the Parquet files, and the Trino external table
    # would read zero rows from a directory that looks full. Making it a
    # partitioned table is a schema change, and the contract is frozen as of
    # 2026-08-13. Accumulating snapshot dates is not needed for H1 anyway:
    # Gold reads MAX(snapshot_date), and Bronze keeps every collection day, so
    # any past date can be rebuilt with --snapshot-date.
    counted.coalesce(1).write.mode("overwrite").parquet(silver_path)

    logger.info(
        "%s/%s: wrote %d Silver rows for snapshot_date=%s to %s",
        SOURCE_ID, DATASET, EXPECTED_ZONE_COUNT, target, silver_path,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bucket", required=True, help="Object-storage bucket name (no s3a:// prefix)"
    )
    parser.add_argument(
        "--snapshot-date",
        type=date.fromisoformat,
        default=None,
        help="Collection date to build (default: the freshest one collected)",
    )
    args = parser.parse_args()

    spark = SparkSession.builder.appName(f"etl_{SILVER_TABLE}").getOrCreate()
    try:
        run(spark, bucket=args.bucket, snapshot_date=args.snapshot_date)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
