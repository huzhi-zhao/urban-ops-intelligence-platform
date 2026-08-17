"""
Daily Bronze -> Silver Spark job for SRC-WPG-311 / service_requests.

Schedule        : 07:00 UTC every day — two hours after dag_ingest_service_requests
                  (05:00 UTC), so the previous day's Bronze files are confirmed
                  before Silver reads them.
Engine          : standalone Spark cluster (spark-master:7077) on this host,
                  deploy-mode client — driver runs inside the Airflow container via
                  SparkSubmitOperator, using the spark_default connection.
Storage         : MinIO via s3a:// — Bronze and Silver in the same bucket (ADR 0006).
Catchup         : enabled — missed days are auto-backfilled on scheduler restart.
max_active_runs : 1 — the Spark cluster has 7 GB and this is the platform's
                  largest table; two concurrent runs is how it OOMs.

The historical load is *not* this DAG's job. Catchup starts at INGEST_START_DATE
(2026-08-02); the ten years before that run once from
scripts/backfill/plan_silver_service_request.sh, which slices and checkpoints.

The 7-day lookback mirrors the Bronze ingest DAG's: 311 rows arrive late and get
revised. Re-processing a day is idempotent because the write overwrites whole
date partitions (C6) — it does not de-duplicate, and must not: a broken
(case_id, interaction_id) is an upstream signal the job raises on.

This job carries a Python UDF (point-in-polygon zone attribution), so it needs
the three extra --conf entries in _spark_common.SPARK_CONF. They are already
there; jobs without a UDF simply ignore them.

on_failure_callback is deliberately absent: DEFAULT_ARGS carries it for every
DAG from one place, and setting it here would override that (see dags/_alerts.py).
"""

from __future__ import annotations

from datetime import timedelta

from _dag_common import DEFAULT_ARGS, get_bucket
from _spark_common import S3A_JARS, SPARK_CONF
from airflow import DAG
from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator

# Matches the Bronze ingest DAG's lookback window, so a late-arriving Bronze row
# is picked up by Silver in the same pass rather than one window later.
LOOKBACK_DAYS = 7

try:
    _DEFAULT_BUCKET = get_bucket({})
except ValueError:
    # S3_BUCKET_NAME not set in this environment yet — let the DAG parse anyway;
    # triggering a run with an empty bucket fails visibly inside the Spark job.
    _DEFAULT_BUCKET = ""

with DAG(
    dag_id="dag_silver_service_request",
    description=(
        "Daily Bronze -> Silver: Winnipeg 311 service requests, "
        "via spark-submit on the local Spark cluster"
    ),
    default_args=DEFAULT_ARGS,
    schedule="0 7 * * *",
    catchup=True,
    max_active_runs=1,
    tags=["silver", "service-request", "spark", "daily"],
) as dag:

    run_silver_etl = SparkSubmitOperator(
        task_id="run_silver_etl",
        application="/opt/airflow/plugins/spark/jobs/etl_service_request.py",
        conn_id="spark_default",
        jars=S3A_JARS,
        conf=SPARK_CONF,
        application_args=[
            "--bucket",
            _DEFAULT_BUCKET,
            # data_interval_start is this run's data date ("yesterday" relative
            # to the trigger day). Window is [start, end) = the last
            # LOOKBACK_DAYS days ending at and including that date.
            "--start",
            "{{ (data_interval_start.date() - macros.timedelta(days="
            + str(LOOKBACK_DAYS - 1)
            + ")).isoformat() }}",
            "--end",
            "{{ (data_interval_start.date() + macros.timedelta(days=1)).isoformat() }}",
        ],
        verbose=True,
        execution_timeout=timedelta(hours=2),
    )
