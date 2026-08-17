"""Bronze -> Silver ETL for SRC-WPG-311 / service_requests.

Reads the daily-split Bronze NDJSON for a ``[start, end)`` window and writes:

  s3a://{bucket}/silver/service_request/open_date_local=YYYY-MM-DD/*.parquet
  s3a://{bucket}/silver/_rejects/service_request/window=YYYY-MM-DD_YYYY-MM-DD/*.parquet

This is the platform's largest table (18.4 M rows / ~16 GB of Bronze over
4,876 days) and the start of the critical path: everything M1 trains on comes
through here. It is also the first job that combines a date window, a Python
UDF and that volume — etl_weather_archive.py has the window without the UDF,
etl_snow_clearing_address.py has the UDF without the window — so neither
skeleton transfers wholesale.

Five things are load-bearing rather than stylistic:

* **The declared read schema.** Everything arrives from Socrata as a string,
  including the numeric-looking ``interaction_id``; inference would type it
  per-month and collide on union (AGENTS.md prohibits schema-less read.json).
* **Local calendar date as the partition key.** ``open_date`` is a Socrata
  *floating* timestamp — America/Winnipeg wall clock with no offset. The
  partition column is derived from that wall clock *before* the UTC
  conversion. Partitioning on the UTC date would push every request opened
  after 18:00 local into the next day and skew every daily aggregate.
* **Three-value geo attribution.** ``geo_match_status`` is never a bare NULL:
  "no coordinates upstream" (79% of rows) and "coordinates outside every
  polygon" have different owners, and merging them makes the alert denominator
  wrong (see spark/transforms/zone_assignment.py).
* **PK assertion, not de-duplication.** The grain is an interaction, so
  ``case_id`` alone is 1.87% non-unique and de-duplicating on it deletes real
  rows. A broken ``(case_id, interaction_id)`` is an upstream change and must
  raise, not be repaired in place.
* **``partitionOverwriteMode=dynamic``.** ``mode("overwrite")`` without it
  deletes the whole table before writing the window — ten years of data
  erased by one incremental run, silently.

Business semantics stay out of here: ``type``, ``channel_raw``, ``ward_raw``
and ``neighbourhood_raw`` are carried verbatim, and winter classification,
channel normalization and label casefolding all resolve in Gold.

Note: this job carries a Python UDF, so it needs the three extra --conf flags
documented in spark/jobs/etl_plow_zone_boundary.py and dags/_spark_common.py.

Usage:
    spark-submit spark/jobs/etl_service_request.py \\
        --bucket uoip --start 2024-11-01 --end 2025-04-01
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    ArrayType,
    DoubleType,
    StringType,
    StructField,
    StructType,
)

from spark.schemas.service_request_schemas import (
    SERVICE_REQUEST_RAW_SCHEMA,
    SERVICE_REQUEST_SILVER_SCHEMA,
)
from spark.transforms.reference_table import assert_unique, enforce_schema
from spark.transforms.timestamp_normalizer import localize_naive_to_utc
from spark.transforms.zone_assignment import (
    MATCH_STATUS_NO_GEO,
    add_point_coordinates,
    assign_zone,
    build_zone_polygons,
    spatial_hit_rate,
)

logger = logging.getLogger(__name__)

SOURCE_ID = "SRC-WPG-311"
DATASET = "service_requests"
SILVER_TABLE = "service_request"

# Socrata floating timestamps: local wall clock, no offset, no 'Z'. Mirrors
# source_timezone in contracts/api-contracts/winnipeg-311.yaml.
SOURCE_TZ = "America/Winnipeg"

# Winnipeg-specific field names. They stay in the job, not in the transforms —
# the transforms are role-generic (CLAUDE.md §城市无关护栏).
GEOMETRY_FIELD = "geometry"
ZONE_FIELD = "plow_zone"

# C7 (launch 20260814 §7.4): one file per day partition. The write is
# repartitioned by the partition column with this many output partitions, so
# each date lands in exactly one file. Capped because the cap is also the
# shuffle width, and a decade-wide window would otherwise ask for thousands of
# tasks from a 7 GB node.
MAX_WRITE_PARTITIONS = 64

# The measured baseline is 134,123 / 134,258 = 99.9% (metric-feasibility-audit
# task 3). CLAUDE.md escalates below 90% *of the geo-bearing subset* — never of
# the full table, 79% of which carries no coordinates upstream by design.
MIN_SPATIAL_HIT_RATE = 0.9
# Below this many geo-bearing rows the ratio is noise, not a signal: a
# three-day window in the sparse 2008-2016 era can hold a handful of points.
MIN_HIT_RATE_SAMPLE = 1_000

# The GeoJSON Point object on u7f6-5326. Declared here rather than in
# SERVICE_REQUEST_RAW_SCHEMA because the point-in-polygon join is this job's
# concern, and the schema module says so.
_GEOMETRY_STRUCT = StructType(
    [
        StructField("type", StringType(), nullable=True),
        StructField("coordinates", ArrayType(DoubleType()), nullable=True),
    ]
)

# A row missing any of these cannot be keyed, dated or classified downstream.
# `type` and `channel_type` are `nullable: false` in the contract, so a NULL in
# either is an upstream change; the row is rejected rather than raising (the
# window still lands), and run() logs the count so it is not silent (design
# §6 O1).
_REQUIRED_FIELDS = ("case_id", "interaction_id", "open_ts_utc", "type", "channel_raw")


def read_schema() -> StructType:
    """The declared Bronze schema plus the GeoJSON geometry column."""
    return StructType([*SERVICE_REQUEST_RAW_SCHEMA.fields, StructField(GEOMETRY_FIELD, _GEOMETRY_STRUCT, nullable=True)])


def bronze_month_prefixes(bucket: str, start: date, end: date) -> list[str]:
    """The Bronze month folders overlapping ``[start, end)``.

    Month folders, not one path per day. Spark calls ``exists()`` on every
    path handed to the reader before reading anything
    (``DataSource.checkAndGlobPathIfNecessary``), so enumerating the 4,876-day
    history would issue that many HEAD requests up front — and s3a treats 403
    as non-retryable, so one transient fault anywhere in the burst aborts the
    job (the 2026-08-16 Cloudflare HEAD-rewrite incident). Worse, a day
    genuinely absent from Bronze makes the whole window unreadable rather than
    merely short, and 2008-2016 holds winters only, so every window spanning
    that era would fail by construction.

    ~220 paths for the same window, both failure modes gone. The window is
    trimmed afterwards in ``run()`` — a month folder holds days on either side
    of ``start``/``end``.

    Deliberately a second copy of ``etl_weather_archive._bronze_month_prefixes``
    for now (design §6 O2): factoring it out would touch a job already running
    in production, and that is not a change to make in the same step as the
    first run of this one.
    """
    prefixes = []
    month = start.replace(day=1)
    while month < end:
        prefixes.append(f"s3a://{bucket}/bronze/raw/{SOURCE_ID}/{DATASET}/{month:%Y-%m}/")
        # Day 28 + 4 days lands in the next month for every month length.
        month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    return prefixes


def write_partition_count(start: date, end: date) -> int:
    """``clamp(window days, 1, MAX_WRITE_PARTITIONS)`` — the C7 write width."""
    return max(1, min((end - start).days, MAX_WRITE_PARTITIONS))


def _silver_path(bucket: str) -> str:
    return f"s3a://{bucket}/silver/{SILVER_TABLE}"


def _rejects_path(bucket: str, start: date, end: date) -> str:
    """Reject rows are scoped to the window that produced them.

    Not partitioned by ``open_date_local`` like the valid rows: the commonest
    reject reason *is* an unusable date, and those rows have no partition to
    land in. A window-scoped path keeps a re-run of the same window
    overwriting exactly its own rejects, and keeps the whole prefix countable
    for the acceptance gate ("Bronze rows minus Silver rows must equal the
    rejects").
    """
    return f"s3a://{bucket}/silver/_rejects/{SILVER_TABLE}/window={start}_{end}"


def _boundary_path(bucket: str) -> str:
    return f"s3a://{bucket}/silver/plow_zone_boundary"


def normalize(df: DataFrame) -> DataFrame:
    """Rename to the Silver vocabulary and derive the two time columns.

    ``open_date_local`` is taken from the *naive* wall-clock string, before
    the UTC conversion — see the module docstring.
    """
    dated = df.withColumn("open_date_local", F.to_date(F.col("open_date")))
    renamed = (
        dated.withColumnRenamed("open_date", "open_ts_utc")
        .withColumnRenamed("closed_date", "closed_ts_utc")
        .withColumnRenamed("channel_type", "channel_raw")
        .withColumnRenamed("ward", "ward_raw")
        .withColumnRenamed("neighbourhood", "neighbourhood_raw")
    )
    localized = localize_naive_to_utc(renamed, "open_ts_utc", SOURCE_TZ)
    localized = localize_naive_to_utc(localized, "closed_ts_utc", SOURCE_TZ)
    return localized.withColumn("source_id", F.lit(SOURCE_ID)).withColumn(
        "loaded_at", F.current_timestamp()
    )


def split_by_validity(df: DataFrame) -> tuple[DataFrame, DataFrame]:
    """Split into ``(valid, rejected)``; rejects carry ``_reject_reason``.

    ``closed_ts_utc`` is *not* required — it is legitimately absent on 3.5% of
    rows (open cases), and requiring it would reject a fifth of a season.
    """
    reason = F.when(
        F.col("open_date_local").isNull(), F.lit("unparseable_open_date")
    ).otherwise(F.lit(None).cast("string"))
    for field in _REQUIRED_FIELDS:
        reason = F.when(reason.isNotNull(), reason).otherwise(
            F.when(F.col(field).isNull(), F.lit(f"missing_{field}"))
        )

    tagged = df.withColumn("_reject_reason", reason)
    return (
        tagged.filter(F.col("_reject_reason").isNull()).drop("_reject_reason"),
        tagged.filter(F.col("_reject_reason").isNotNull()),
    )


def attribute_zones(df: DataFrame, zone_polygons: list[tuple[str, str]]) -> DataFrame:
    """Add ``has_geo`` / ``geo_match_status`` / ``plow_zone``.

    Rows without coordinates are split off *before* the UDF and filled with
    constants instead. ``assign_zone`` handles them correctly on its own — the
    split is purely about not paying JVM<->Python serialization for the 79% of
    rows that can only ever come back ``no_geo``.

    The constants come from the transform's ``MATCH_STATUS_*`` names, never
    from a literal spelled out here: a literal would keep working after the
    transform renamed a status, and this branch would quietly become a fourth
    value nobody is looking for. The transform itself is not changed to skip
    empty points — etl_snow_clearing_address.py goes through the unsplit path,
    and BO-4's hit rate is only comparable if both count the same way.
    """
    located = add_point_coordinates(df, geometry_field=GEOMETRY_FIELD)
    has_point = F.col("longitude").isNotNull() & F.col("latitude").isNotNull()

    with_geo = assign_zone(located.filter(has_point), zone_polygons, zone_col=ZONE_FIELD)
    without_geo = (
        located.filter(~has_point)
        .withColumn("has_geo", F.lit(False))
        .withColumn("geo_match_status", F.lit(MATCH_STATUS_NO_GEO))
        .withColumn(ZONE_FIELD, F.lit(None).cast(StringType()))
    )
    return with_geo.unionByName(without_geo)


def run(spark: SparkSession, bucket: str, start: date, end: date) -> None:
    raw = (
        spark.read.schema(read_schema())
        # pathGlobFilter is load-bearing, not an optimisation: each month folder
        # holds manifest_YYYY-MM-DD.json beside the data shards, and reading the
        # folder whole would parse the manifests as service requests. Against a
        # declared schema they yield all-null rows, so they would land in
        # _rejects silently rather than raising.
        .option("pathGlobFilter", "data_*.ndjson.gz")
        .json(bronze_month_prefixes(bucket, start, end))
    )

    normalized = normalize(raw)
    # Month folders overshoot the window on both ends. Null dates survive the
    # filter so unparseable rows still reach _rejects rather than vanishing;
    # split_by_validity, not this filter, decides validity.
    windowed = normalized.filter(
        F.col("open_date_local").isNull()
        | (
            (F.col("open_date_local") >= F.lit(start))
            & (F.col("open_date_local") < F.lit(end))
        )
    )

    boundary = spark.read.parquet(_boundary_path(bucket))
    zone_polygons = build_zone_polygons(boundary, zone_field=ZONE_FIELD)

    valid, rejected = split_by_validity(windowed)
    valid = enforce_schema(attribute_zones(valid, zone_polygons), SERVICE_REQUEST_SILVER_SCHEMA)
    valid = valid.repartition(write_partition_count(start, end), "open_date_local").cache()

    assert_unique(valid, ("case_id", "interaction_id"), f"{SOURCE_ID}/{DATASET}")

    (
        valid.write.partitionBy("open_date_local")
        # 🔴 Without dynamic mode, mode("overwrite") deletes every existing
        # partition before writing this window's — a 7-day incremental run
        # would erase the whole ten-year table, and report success.
        .option("partitionOverwriteMode", "dynamic")
        .mode("overwrite")
        .parquet(_silver_path(bucket))
    )

    row_count = valid.count()
    rejected_count = rejected.count()
    if rejected_count:
        rejects_path = _rejects_path(bucket, start, end)
        rejected.write.mode("overwrite").parquet(rejects_path)
        # Logged at warning even though rejecting is the designed behaviour:
        # the contract declares `type` and `channel_type` non-nullable, so a
        # non-zero count here means the upstream changed (design §6 O1).
        logger.warning(
            "%s/%s: %d rejected rows for window=[%s, %s) written to %s",
            SOURCE_ID, DATASET, rejected_count, start, end, rejects_path,
        )

    matched, has_geo = spatial_hit_rate(valid)
    logger.info(
        "%s/%s: window=[%s, %s) rows=%d rejected=%d with_geo=%d matched=%d (%.3f%%)",
        SOURCE_ID, DATASET, start, end, row_count, rejected_count, has_geo, matched,
        100.0 * matched / has_geo if has_geo else 0.0,
    )
    if row_count == 0:
        # Not an error: Bronze holds winters only before 2016-08, so a summer
        # window in that era is legitimately empty.
        logger.warning(
            "%s/%s: window=[%s, %s) produced 0 Silver rows. Expected only for "
            "pre-2016 non-winter windows — otherwise Bronze is missing days.",
            SOURCE_ID, DATASET, start, end,
        )

    if has_geo >= MIN_HIT_RATE_SAMPLE and matched < MIN_SPATIAL_HIT_RATE * has_geo:
        raise RuntimeError(
            f"{SOURCE_ID}/{DATASET}: spatial hit rate {matched}/{has_geo} is "
            f"below {MIN_SPATIAL_HIT_RATE:.0%} of the geo-bearing subset "
            f"(measured baseline 99.9%). The boundary table or the upstream "
            f"coordinate field changed. Escalate per CLAUDE.md."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bucket", required=True, help="Object-storage bucket name (no s3a:// prefix)"
    )
    parser.add_argument("--start", required=True, type=date.fromisoformat, help="Inclusive")
    parser.add_argument("--end", required=True, type=date.fromisoformat, help="Exclusive")
    args = parser.parse_args()
    if args.start >= args.end:
        parser.error("--start must be before --end")

    spark = (
        SparkSession.builder.appName(f"etl_{SILVER_TABLE}_{args.start}_{args.end}")
        .getOrCreate()
    )
    try:
        run(spark, bucket=args.bucket, start=args.start, end=args.end)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
