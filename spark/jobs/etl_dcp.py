"""Bronze → Silver ETL for SRC-DCP / borough_boundaries.

Reads the single static Bronze NDJSON file, converts MultiPolygon geometry
to WKT via Shapely, and writes a 5-row Silver Parquet (one per NYC borough).

Static source: no date partitioning, no incremental window.
Re-running always overwrites the entire Silver table (mode=overwrite).
Run manually whenever Bronze is refreshed (boundary updates are rare).

Preferred entry point is the DAG (`dags/dag_backfill_silver_dcp.py`), which
already wires up S3A_JARS + SPARK_CONF from dags/_spark_common.py.

Docker Spark (see docs/dev/adr/0005-silver-execution-architecture.md §4):
    docker exec airflow-scheduler spark-submit \\
        --master spark://spark-master:7077 --deploy-mode client \\
        --jars https://repo1.maven.org/maven2/org/apache/hadoop/hadoop-aws/3.3.4/hadoop-aws-3.3.4.jar,https://repo1.maven.org/maven2/com/amazonaws/aws-java-sdk-bundle/1.12.262/aws-java-sdk-bundle-1.12.262.jar \\
        --conf spark.hadoop.fs.s3a.endpoint=$S3_ENDPOINT_URL \\
        --conf spark.hadoop.fs.s3a.path.style.access=true \\
        --conf spark.pyspark.python=/usr/local/bin/python3.11 \\
        --conf spark.pyspark.driver.python=python3 \\
        --conf spark.executorEnv.PYTHONPATH=/opt/airflow/plugins \\
        /opt/airflow/plugins/spark/jobs/etl_dcp.py --bucket uoip

    Credentials come from AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY in the
    container environment — never from --conf, which would print them in the
    Spark UI, the process list and the task log.

Note: the last three --conf flags are mandatory for this job specifically —
it is the only job with a Python UDF. See dags/_spark_common.py for why.
"""

from __future__ import annotations

import argparse
import logging

from pyspark.sql import SparkSession

from spark.schemas.dcp_schemas import DCP_SILVER_SCHEMA
from spark.transforms.dcp import (
    add_geometry_wkt,
    cast_scalars,
    enforce_schema,
    split_by_validity,
)

logger = logging.getLogger(__name__)

SOURCE_ID = "SRC-DCP"
DATASET   = "borough_boundaries"
EXPECTED_BOROUGH_COUNT = 5


def _bronze_path(bucket: str) -> str:
    return f"s3a://{bucket}/bronze/raw/{SOURCE_ID}/{DATASET}/data_static.ndjson"


def run(spark: SparkSession, bucket: str) -> None:
    bronze_path = _bronze_path(bucket)

    # Schema inference is intentional here: the_geom is a deeply nested
    # MultiPolygon struct — declaring it in StructType would be hundreds of
    # nested ArrayType levels. We let Spark infer it, then immediately
    # serialise it back to JSON inside add_geometry_wkt().
    raw = spark.read.json(bronze_path)

    enriched        = add_geometry_wkt(raw)
    typed           = cast_scalars(enriched, source_id=SOURCE_ID)
    valid, rejected = split_by_validity(typed)
    valid           = enforce_schema(valid, DCP_SILVER_SCHEMA)

    silver_path  = f"s3a://{bucket}/silver/borough_boundaries"
    rejects_path = f"s3a://{bucket}/silver/_rejects/borough_boundaries"

    (
        valid.coalesce(1)
        .write.mode("overwrite")
        .parquet(silver_path)
    )

    if rejected.take(1):
        rejected.coalesce(1).write.mode("overwrite").parquet(rejects_path)
        logger.warning("%s/%s: %d rejected rows written to %s",
                       SOURCE_ID, DATASET, rejected.count(), rejects_path)

    row_count = valid.count()
    logger.info("%s/%s: wrote %d Silver rows to %s", SOURCE_ID, DATASET, row_count, silver_path)

    if row_count < EXPECTED_BOROUGH_COUNT:
        raise RuntimeError(
            f"{SOURCE_ID}/{DATASET}: only {row_count} valid rows (expected {EXPECTED_BOROUGH_COUNT}). "
            f"Possible Bronze file corruption or upstream schema change. Escalate per CLAUDE.md."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", required=True, help="Object-storage bucket name (no s3a:// prefix)")
    args = parser.parse_args()

    spark = SparkSession.builder.appName(f"etl_dcp_{DATASET}").getOrCreate()
    try:
        run(spark, bucket=args.bucket)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
