"""
Daily incremental ingest DAG for SRC-Open-Meteo (hourly weather).

Schedule        : 06:00 UTC every day
Window          : yesterday (confirmed 24h) + 7 days forecast
Catchup         : enabled — missed days are auto-backfilled on scheduler restart
max_active_runs : 1 — single API endpoint; no benefit from parallel runs
SLA             : 1 hour — Open-Meteo is a single fast API call; should finish quickly

Forecast files are intentionally overwritten on each run: Open-Meteo updates its
forecast model multiple times per day, so yesterday's forecast for D+3 is less
accurate than today's. Daily refresh gives Silver layer the freshest forecast data.

Bronze output: bronze/raw/SRC-Open-Meteo/nyc_weather_forecast/{YYYY-MM}/data_{YYYY-MM-DD}.ndjson.gz
"""

from __future__ import annotations

import logging
from datetime import timedelta

from _dag_common import DEFAULT_ARGS, get_bucket, get_yesterday
from airflow import DAG
from airflow.operators.python import PythonOperator

logger = logging.getLogger(__name__)

SOURCE_ID = "SRC-Open-Meteo"
FORECAST_DAYS = 7


def _run_ingest(**context) -> None:
    from scripts.backfill.bulk import backfill_daily_window

    target_date = get_yesterday(context)
    fetch_end = target_date + timedelta(days=1 + FORECAST_DAYS)
    bucket = get_bucket({})

    logger.info("%s ingest: confirmed=%s forecast_until=%s", SOURCE_ID, target_date, fetch_end - timedelta(days=1))
    results = backfill_daily_window(SOURCE_ID, start=target_date, end=fetch_end, bucket=bucket)

    failed = [r for r in results if r.status == "failed"]
    total_files = sum(r.manifest_count for r in results if r.status == "ok")
    logger.info("%s: %d files written, %d failures", SOURCE_ID, total_files, len(failed))
    if failed:
        for r in failed:
            logger.error("  FAILED: %s", r.error)
        raise RuntimeError(f"Open-Meteo ingest failed: {failed[0].error}")


with DAG(
    dag_id="dag_ingest_open_meteo",
    description="Daily incremental: Open-Meteo weather (yesterday + 7-day forecast) → Bronze",
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
