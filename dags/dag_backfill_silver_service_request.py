"""
One-time Bronze -> Silver backfill DAG for SRC-WPG-311 / service_requests.

Mirrors dag_backfill_silver_weather_archive.py: manual trigger only,
Params-driven start/end/bucket, no catchup. Where dag_silver_service_request.py
processes a 7-day rolling window every day, this DAG processes one arbitrary
[start, end) range in a single Spark job.

⚠️ **One window per run, and the window is the retry unit.** The full history is
4,876 days / 18.4 M rows and does not fit in one run on a 7 GB node. Triggering
this DAG with the whole range is the failure mode the slicing exists to prevent.
For the historical load use scripts/backfill/plan_silver_service_request.sh,
which issues the same windows as the Bronze side and checkpoints each one; this
DAG is for re-running a single window by hand.

Engine  : same standalone Spark cluster (spark-master:7077), deploy-mode client,
          via the spark_default connection — ADR 0005.
Storage : MinIO via s3a:// — reads gzipped Bronze NDJSON, writes Silver Parquet.

Trigger example:
    {"start": "2024-11-01", "end": "2025-04-01", "bucket": "uoip"}

on_failure_callback is deliberately absent — DEFAULT_ARGS carries it.
"""

from __future__ import annotations

import logging

from _dag_common import DEFAULT_ARGS, backfill_params, get_bucket
from _spark_common import S3A_JARS, SPARK_CONF
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator

logger = logging.getLogger(__name__)

# A year is already a long single run at this volume. Wider than this is a
# request to use the plan script instead, so it is refused up front rather than
# after the run has burned an hour and died.
MAX_WINDOW_DAYS = 400


def _check_params(**context) -> str:
    """Validate the window and resolve the bucket (env-var fallback included).

    Returns the bucket so the Spark task can pick it up via XCom — Jinja alone
    cannot reach get_bucket()'s env-var fallback.
    """
    from datetime import datetime

    params = context["params"]
    start = datetime.strptime(params["start"], "%Y-%m-%d").date()
    end = datetime.strptime(params["end"], "%Y-%m-%d").date()
    if end <= start:
        raise ValueError(f"end ({end}) must be after start ({start})")
    days = (end - start).days
    if days > MAX_WINDOW_DAYS:
        raise ValueError(
            f"window is {days} days, over the {MAX_WINDOW_DAYS}-day limit. "
            f"The full history is loaded by "
            f"scripts/backfill/plan_silver_service_request.sh, which slices it "
            f"and records each window so an interrupted run resumes."
        )
    bucket = get_bucket(params)
    logger.info(
        "Silver service-request backfill window: [%s, %s) — %d days, bucket=%s",
        start, end, days, bucket,
    )
    return bucket


with DAG(
    dag_id="dag_backfill_silver_service_request",
    description="One-time backfill: Winnipeg 311 service requests, Bronze -> Silver, via spark-submit",
    default_args=DEFAULT_ARGS,
    schedule=None,
    catchup=False,
    params=backfill_params,
    tags=["backfill", "silver", "service-request", "spark"],
) as dag:

    check_params = PythonOperator(
        task_id="check_params",
        python_callable=_check_params,
    )

    run_silver_backfill = SparkSubmitOperator(
        task_id="run_silver_backfill",
        application="/opt/airflow/plugins/spark/jobs/etl_service_request.py",
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
