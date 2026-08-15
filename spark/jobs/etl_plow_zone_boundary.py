"""Bronze -> Silver ETL for SRC-WPG-PLOW-ZONE / plow_zones.

Reads the single static Bronze NDJSON file, converts each polygon's the_geom
to WKT via spark.transforms.geography_boundary (repairing OGC-invalid
geometry rather than dropping it — ADR 0010 D6), and writes an 82-row Silver
Parquet (one row per polygon; a plow_zone is made of several polygons, see
contracts/api-contracts/winnipeg-plow-zones.yaml).

Static source: no date partitioning, no incremental window. Re-running
always overwrites the entire Silver table (mode=overwrite). Run manually
whenever Bronze is refreshed (boundary updates are rare).

This is the batch-4 generalisation of the GeoJSON -> WKT capability that used
to live in spark/jobs/etl_dcp.py (SRC-DCP / NYC borough boundaries, retired).
The generic repair/validity/schema-enforcement logic now lives in
spark/transforms/geography_boundary.py; this file supplies only the
Winnipeg-specific field names and output path (per the city-agnostic
guardrail in CLAUDE.md §"城市无关护栏").

Docker Spark (see docs/dev/adr/0005-silver-execution-architecture.md §4):
    docker exec airflow-scheduler spark-submit \\
        --master spark://spark-master:7077 --deploy-mode client \\
        --jars https://repo1.maven.org/maven2/org/apache/hadoop/hadoop-aws/3.3.4/hadoop-aws-3.3.4.jar,https://repo1.maven.org/maven2/com/amazonaws/aws-java-sdk-bundle/1.12.262/aws-java-sdk-bundle-1.12.262.jar \\
        --conf spark.hadoop.fs.s3a.endpoint=$S3_ENDPOINT_URL \\
        --conf spark.hadoop.fs.s3a.path.style.access=true \\
        --conf spark.pyspark.python=/usr/local/bin/python3.11 \\
        --conf spark.pyspark.driver.python=python3 \\
        --conf spark.executorEnv.PYTHONPATH=/opt/airflow/plugins \\
        /opt/airflow/plugins/spark/jobs/etl_plow_zone_boundary.py --bucket uoip

    Credentials come from AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY in the
    container environment — never from --conf, which would print them in the
    Spark UI, the process list and the task log.

Note: the last three --conf flags are mandatory for this job specifically —
it is the only job (besides the retired etl_dcp.py) with a Python UDF.
See dags/_spark_common.py for why.
"""

from __future__ import annotations

import argparse
import logging

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from spark.schemas.plow_zone_boundary_schemas import PLOW_ZONE_BOUNDARY_SILVER_SCHEMA
from spark.transforms.geography_boundary import add_geometry_wkt, enforce_schema, split_by_validity

logger = logging.getLogger(__name__)

SOURCE_ID = "SRC-WPG-PLOW-ZONE"
DATASET = "plow_zones"
EXPECTED_POLYGON_COUNT = 82  # per contracts/api-contracts/winnipeg-plow-zones.yaml


def _bronze_path(bucket: str) -> str:
    return f"s3a://{bucket}/bronze/raw/{SOURCE_ID}/{DATASET}/data_static.ndjson.gz"


def run(spark: SparkSession, bucket: str) -> None:
    bronze_path = _bronze_path(bucket)

    # Schema inference is intentional here: the_geom is a deeply nested
    # MultiPolygon struct — declaring it in StructType would be hundreds of
    # nested ArrayType levels. We let Spark infer it, then immediately
    # serialise it back to JSON inside add_geometry_wkt().
    raw = spark.read.json(bronze_path)

    enriched = add_geometry_wkt(raw, geometry_field="the_geom")

    # Scalar columns already arrive named entity_id / plow_zone / city_area —
    # no renaming needed; just add the two audit columns.
    typed = enriched.withColumn("source_id", F.lit(SOURCE_ID)).withColumn(
        "loaded_at", F.current_timestamp()
    )

    valid, rejected = split_by_validity(typed, required_id_fields=("entity_id", "plow_zone"))
    valid = enforce_schema(valid, PLOW_ZONE_BOUNDARY_SILVER_SCHEMA)

    silver_path = f"s3a://{bucket}/silver/plow_zone_boundary"
    rejects_path = f"s3a://{bucket}/silver/_rejects/plow_zone_boundary"

    valid.coalesce(1).write.mode("overwrite").parquet(silver_path)

    if rejected.take(1):
        rejected.coalesce(1).write.mode("overwrite").parquet(rejects_path)
        logger.warning(
            "%s/%s: %d rejected rows written to %s",
            SOURCE_ID, DATASET, rejected.count(), rejects_path,
        )

    row_count = valid.count()
    repaired_count = valid.filter("geometry_repaired = true").count()
    logger.info(
        "%s/%s: wrote %d Silver rows to %s (%d geometry-repaired)",
        SOURCE_ID, DATASET, row_count, silver_path, repaired_count,
    )

    if row_count < EXPECTED_POLYGON_COUNT:
        raise RuntimeError(
            f"{SOURCE_ID}/{DATASET}: only {row_count} valid rows "
            f"(expected {EXPECTED_POLYGON_COUNT}). Possible Bronze file "
            f"corruption or upstream schema change. Escalate per CLAUDE.md."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", required=True, help="Object-storage bucket name (no s3a:// prefix)")
    args = parser.parse_args()

    spark = SparkSession.builder.appName(f"etl_{DATASET}").getOrCreate()
    try:
        run(spark, bucket=args.bucket)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
