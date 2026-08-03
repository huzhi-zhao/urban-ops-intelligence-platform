"""
Daily incremental ingest DAG for SRC-Open-Meteo / weather_archive (daily observations).

Schedule        : 06:00 UTC every day
Window          : yesterday (the most recent complete local day)
Catchup         : enabled — missed days are auto-backfilled on scheduler restart
max_active_runs : 1 — single API endpoint; no benefit from parallel runs
SLA             : 1 hour — Open-Meteo is a single fast API call; should finish quickly

Covers the archive dataset only. Calling the `daily` bulk path on this source
writes the datasets whose effective strategy is `daily`, which is exactly the
archive — the source's `weather_forecast` dataset is `partition_strategy:
snapshot` and is collected on the storage node by ingestion/snapshot/, outside
Airflow (ADR 0006 §2.2, CLAUDE.md). A missed forecast snapshot is unrecoverable
and must not depend on the scheduler.

Open-Meteo revises a day's aggregate for a few days after the fact, and the
fetcher's forecast-API route returns every day back to the window start, so a
run rewrites the trailing Bronze day(s) it already wrote. Silver re-derives those
dates from Bronze on the same 7-day lookback, so the layers stay consistent.

Bronze output: bronze/raw/SRC-Open-Meteo/weather_archive/{YYYY-MM}/data_{YYYY-MM-DD}.ndjson.gz
"""

from __future__ import annotations

import logging
from datetime import timedelta

from _dag_common import DEFAULT_ARGS, get_bucket, get_yesterday
from airflow import DAG
from airflow.operators.python import PythonOperator

logger = logging.getLogger(__name__)

SOURCE_ID = "SRC-Open-Meteo"


def _run_ingest(**context) -> None:
    from scripts.backfill.bulk import backfill_daily_window

    target_date = get_yesterday(context)
    bucket = get_bucket({})

    logger.info("%s ingest: archive day=%s", SOURCE_ID, target_date)
    results = backfill_daily_window(
        SOURCE_ID, start=target_date, end=target_date + timedelta(days=1), bucket=bucket
    )

    failed = [r for r in results if r.status == "failed"]
    total_files = sum(r.manifest_count for r in results if r.status == "ok")
    logger.info("%s: %d files written, %d failures", SOURCE_ID, total_files, len(failed))
    if failed:
        for r in failed:
            logger.error("  FAILED: %s", r.error)
        raise RuntimeError(f"Open-Meteo archive ingest failed: {failed[0].error}")


with DAG(
    dag_id="dag_ingest_weather_archive",
    description="Daily incremental: Open-Meteo daily weather archive → Bronze",
    default_args=DEFAULT_ARGS,
    schedule="0 6 * * *",
    catchup=True,
    max_active_runs=1,
    tags=["ingest", "open-meteo", "bronze", "weather", "daily"],
) as dag:

    run_ingest = PythonOperator(
        task_id="run_ingest",
        python_callable=_run_ingest,
    )
