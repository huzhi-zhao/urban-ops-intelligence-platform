"""
Daily incremental ingest DAG for SRC-NYC-311 (311 Service Requests).

Schedule        : 06:00 UTC every day
Window          : data_interval_start.date() — yesterday's data + 7-day lookback
Catchup         : enabled — Airflow will auto-backfill any missed DAG Runs
max_active_runs : 1 — prevents parallel runs from racing on the same GCS paths
SLA             : 2 hours — logs a warning if the task hasn't finished by 08:00 UTC

The 7-day lookback window ensures late-arriving 311 records (status updates applied
days after creation) are captured on every run, not just the target date.

GCS output: bronze/raw/SRC-NYC-311/nyc_311/{YYYY-MM}/data_{YYYY-MM-DD}.ndjson
"""

from __future__ import annotations

import logging
from datetime import timedelta

from _dag_common import DEFAULT_ARGS, get_bucket, get_yesterday
from airflow import DAG
from airflow.operators.python import PythonOperator

logger = logging.getLogger(__name__)

SOURCE_ID = "SRC-NYC-311"
LOOKBACK_DAYS = 7


def _run_ingest(**context) -> None:
    from scripts.backfill.bulk import backfill_daily_window

    target_date = get_yesterday(context)
    start = target_date - timedelta(days=LOOKBACK_DAYS - 1)
    end = target_date + timedelta(days=1)
    bucket = get_bucket({})

    logger.info("%s ingest: target=%s window=[%s, %s)", SOURCE_ID, target_date, start, end)
    results = backfill_daily_window(SOURCE_ID, start=start, end=end, bucket=bucket)

    failed = [r for r in results if r.status == "failed"]
    total_records = sum(r.manifest_count for r in results if r.status == "ok")
    logger.info("%s: %d days written, %d records, %d failures", SOURCE_ID, len(results), total_records, len(failed))
    if failed:
        for r in failed:
            logger.error("  FAILED day=%s: %s", r.document, r.error)
        raise RuntimeError(f"{len(failed)} day(s) failed for {SOURCE_ID}.")


with DAG(
    dag_id="dag_ingest_nyc_311",
    description="Daily incremental: NYC 311 Service Requests → GCS Bronze (7-day lookback)",
    default_args=DEFAULT_ARGS,
    schedule="0 6 * * *",
    catchup=True,
    max_active_runs=1,
    tags=["ingest", "nyc-311", "bronze", "socrata", "daily"],
) as dag:

    run_ingest = PythonOperator(
        task_id="run_ingest",
        python_callable=_run_ingest,
    )
