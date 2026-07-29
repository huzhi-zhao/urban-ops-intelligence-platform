"""Full-refresh Bronze -> Silver DAG for SRC-DCP / borough_boundaries.

No date window — DCP is a static source (5 borough polygons). Each run
reads the single Bronze NDJSON file, converts MultiPolygon geometry to WKT
via Shapely, and overwrites the entire Silver table.

Re-run whenever the DCP Bronze file is refreshed (boundary updates are rare;
typically once every several years). Manual trigger only, no schedule.

Engine  : Docker Spark standalone (spark-master:7077), deploy-mode client —
          same cluster used by all Silver ETL. See
          docs/01-architecture/decisions/week3-Silver-Execution-Architecture.md §4.
Storage : reads  gs://{bucket}/bronze/raw/SRC-DCP/borough_boundaries/data_static.ndjson
          writes gs://{bucket}/silver/borough_boundaries/

Trigger example (Airflow UI → Trigger DAG w/ Config):
    {"bucket": "nyc-uoip-prod"}
    or leave bucket empty to use GCS_BUCKET_NAME env var.
"""

from __future__ import annotations

import logging

from _dag_common import DEFAULT_ARGS, get_bucket
from _spark_common import GCS_CONNECTOR_JAR, SPARK_CONF
from airflow import DAG
from airflow.models.param import Param
from airflow.operators.python import PythonOperator
from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator

logger = logging.getLogger(__name__)

_PARAMS = {
    "bucket": Param(
        "",
        type=["string", "null"],
        description="GCS bucket name. Empty = use GCS_BUCKET_NAME env var.",
    ),
}


def _check_params(**context) -> str:
    """Resolve bucket and log intent. Returns bucket via XCom."""
    bucket = get_bucket(context["params"])
    logger.info(
        "DCP Silver full-refresh: bucket=%s  "
        "source=gs://%s/bronze/raw/SRC-DCP/borough_boundaries/data_static.ndjson",
        bucket, bucket,
    )
    return bucket


with DAG(
    dag_id="dag_backfill_silver_dcp",
    description="Full-refresh: DCP borough boundaries, GCS Bronze -> GCS Silver Parquet (WKT geometry)",
    default_args=DEFAULT_ARGS,
    schedule=None,
    catchup=False,
    params=_PARAMS,
    tags=["backfill", "silver", "dcp", "spark", "geography"],
) as dag:

    check_params = PythonOperator(
        task_id="check_params",
        python_callable=_check_params,
    )

    run_silver_dcp = SparkSubmitOperator(
        task_id="run_silver_dcp",
        application="/opt/airflow/plugins/spark/jobs/etl_dcp.py",
        conn_id="spark_default",
        jars=GCS_CONNECTOR_JAR,
        conf=SPARK_CONF,
        application_args=[
            "--bucket",
            "{{ ti.xcom_pull(task_ids='check_params') }}",
        ],
        verbose=True,
        execution_timeout=None,
    )

    check_params >> run_silver_dcp
