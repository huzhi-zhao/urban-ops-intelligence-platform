"""
One-time Bronze -> Silver backfill DAG for SRC-Open-Meteo (hourly weather).

Mirrors dag_backfill_open_meteo.py (Bronze backfill): manual trigger only,
Params-driven start/end/bucket, no catchup. Where dag_silver_open_meteo.py
processes a narrow 7-day rolling window every day, this DAG processes one
arbitrary wide [start, end) range in a single Spark job — for backfilling
Silver from all the Bronze history that already accumulated before the
daily pipeline existed.

Engine  : same standalone Spark cluster (spark-master:7077), deploy-mode
          client, via the spark_default connection — see
          docs/dev/adr/0005-silver-execution-architecture.md.
Storage : MinIO via s3a:// — reads gzipped Bronze NDJSON, writes Silver Parquet.

Trigger example:
    {"start": "2024-01-01", "end": "2026-06-29", "bucket": "uoip"}
"""

from __future__ import annotations

import logging

from _dag_common import DEFAULT_ARGS, backfill_params, get_bucket
from _spark_common import S3A_JARS, SPARK_CONF
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator

logger = logging.getLogger(__name__)


def _check_params(**context) -> str:
    """Validate params and resolve the bucket (env-var fallback included).

    Returns the resolved bucket so the next task can pick it up via XCom —
    Jinja templating alone can't reach get_bucket()'s env-var fallback logic.
    """
    from datetime import datetime

    params = context["params"]
    start = datetime.strptime(params["start"], "%Y-%m-%d").date()
    end = datetime.strptime(params["end"], "%Y-%m-%d").date()
    if end <= start:
        raise ValueError(f"end ({end}) must be after start ({start})")
    bucket = get_bucket(params)
    logger.info("Silver backfill window: [%s, %s) — %d days, bucket=%s", start, end, (end - start).days, bucket)
    return bucket


with DAG(
    dag_id="dag_backfill_silver_open_meteo",
    description="One-time backfill: Open-Meteo weather, Bronze -> Silver, via spark-submit",
    default_args=DEFAULT_ARGS,
    schedule=None,
    catchup=False,
    params=backfill_params,
    tags=["backfill", "silver", "open-meteo", "spark", "weather"],
) as dag:

    check_params = PythonOperator(
        task_id="check_params",
        python_callable=_check_params,
    )

    run_silver_backfill = SparkSubmitOperator(
        task_id="run_silver_backfill",
        application="/opt/airflow/plugins/spark/jobs/etl_open_meteo.py",
        conn_id="spark_default",
        jars=S3A_JARS,
        conf=SPARK_CONF,
        application_args=[
            "--bucket",
            "{{ ti.xcom_pull(task_ids='check_params') }}",
            "--start",
            "{{ params.start }}",
            "--end",
            "{{ params.end }}",
        ],
        verbose=True,
        execution_timeout=None,
    )

    check_params >> run_silver_backfill
