"""
One-time Bronze -> Silver backfill DAG for SRC-Open-Meteo / weather_archive.

Mirrors dag_backfill_weather_archive.py (Bronze backfill): manual trigger only,
Params-driven start/end/bucket, no catchup. Where dag_silver_weather_archive.py
processes a narrow 7-day rolling window every day, this DAG processes one
arbitrary wide [start, end) range in a single Spark job — for backfilling
Silver from all the Bronze history that already accumulated before the
daily pipeline existed.

Every run also rebuilds the BO-3 snowfall event table, which the daily DAG
deliberately does not: an event can span a window boundary, so
segmenting per-window would cut a storm running across the boundary into two
events. The event table is only correct when derived from the whole series,
which is exactly what a backfill run has in hand.

Engine  : same standalone Spark cluster (spark-master:7077), deploy-mode
          client, via the spark_default connection — see
          docs/dev/adr/0005-execution-architecture.md.
Storage : MinIO via s3a:// — reads gzipped Bronze NDJSON, writes Silver Parquet.

Trigger example:
    {"start": "2008-01-01", "end": "2026-08-02", "bucket": "uoip",
     "snowfall_threshold_cm": 2.0}
"""

from __future__ import annotations

import logging

from _dag_common import DEFAULT_ARGS, backfill_params, get_bucket
from _spark_common import S3A_JARS, SPARK_CONF
from airflow import DAG
from airflow.models.param import Param
from airflow.operators.python import PythonOperator
from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator

logger = logging.getLogger(__name__)

# The threshold is policy, not physics — it moves to config/semantics/ in batch 5
# of docs/dev/design/20260802-city-instance-switchover.md. Until then it is an
# explicit trigger-time choice rather than a default buried in the job.
_EVENT_PARAMS = {
    "snowfall_threshold_cm": Param(
        2.0,
        type="number",
        description="Daily snowfall (cm) at or above which a day counts toward a BO-3 event.",
    ),
    "snowfall_gap_days": Param(
        0,
        type="integer",
        description="Below-threshold days tolerated inside one event (0 = strictly consecutive).",
    ),
}


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
    if params["snowfall_threshold_cm"] < 0:
        raise ValueError(
            f"snowfall_threshold_cm must be non-negative, got {params['snowfall_threshold_cm']}"
        )
    if params["snowfall_gap_days"] < 0:
        raise ValueError(
            f"snowfall_gap_days must be non-negative, got {params['snowfall_gap_days']}"
        )
    bucket = get_bucket(params)
    logger.info(
        "Silver backfill window: [%s, %s) — %d days, bucket=%s, threshold=%scm",
        start, end, (end - start).days, bucket, params["snowfall_threshold_cm"],
    )
    return bucket


with DAG(
    dag_id="dag_backfill_silver_weather_archive",
    description="One-time backfill: Open-Meteo weather archive, Bronze -> Silver, via spark-submit",
    default_args=DEFAULT_ARGS,
    schedule=None,
    catchup=False,
    params={**backfill_params, **_EVENT_PARAMS},
    tags=["backfill", "silver", "open-meteo", "spark", "weather"],
) as dag:

    check_params = PythonOperator(
        task_id="check_params",
        python_callable=_check_params,
    )

    run_silver_backfill = SparkSubmitOperator(
        task_id="run_silver_backfill",
        application="/opt/airflow/plugins/spark/jobs/etl_weather_archive.py",
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
            "--snowfall-threshold-cm",
            "{{ params.snowfall_threshold_cm }}",
            "--snowfall-gap-days",
            "{{ params.snowfall_gap_days }}",
            # Unconditional, not a Param: a Jinja-conditional flag would have to
            # render to "" when off, and an empty argv entry makes argparse fail
            # with "unrecognized arguments". Rebuilding the events *is* this DAG's
            # job — run the Spark script by hand for a daily-table-only pass.
            "--emit-events",
        ],
        verbose=True,
        execution_timeout=None,
    )

    check_params >> run_silver_backfill
