"""
Backfill DAG for SRC-Open-Meteo (hourly weather history).

Partition strategy : daily, wide-fetch (1 Open-Meteo API call covers the entire window;
                     the response is split into per-day files by the facade)
Trigger            : manual only (schedule=None)
Params             : start (inclusive), end (exclusive), bucket

Routing:
- Windows starting within the last 92 days → forecast API (past_days / forecast_days).
- Older windows → archive API (start_date / end_date, no day-count limit).
  Historical chunks are sized at 365 days to reduce API call count.
  Recent chunks are sized at 90 days (safely under the 92-day forecast limit).

Trigger example:
    {"start": "2020-01-01", "end": "2026-06-01", "bucket": "nyc-uoip-prod"}
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from _dag_common import DEFAULT_ARGS, backfill_params, get_bucket
from airflow import DAG
from airflow.operators.python import PythonOperator

logger = logging.getLogger(__name__)

SOURCE_ID = "SRC-Open-Meteo"
_ARCHIVE_CHUNK_DAYS = 365   # archive API: no day-count limit, use large chunks
_FORECAST_CHUNK_DAYS = 90   # forecast API: must stay under 92-day past_days limit
_ARCHIVE_CUTOFF_DAYS = 92   # windows starting > 92 days ago route to archive API


def _check_params(**context) -> None:
    from datetime import date as _date

    params = context["params"]
    start = datetime.strptime(params["start"], "%Y-%m-%d").date()
    end = datetime.strptime(params["end"], "%Y-%m-%d").date()
    if end <= start:
        raise ValueError(f"end ({end}) must be after start ({start})")
    get_bucket(params)

    today = _date.today()
    archive_boundary = today - timedelta(days=_ARCHIVE_CUTOFF_DAYS)
    archive_end = min(end, archive_boundary)
    forecast_start = max(start, archive_boundary)

    archive_chunks = 0
    if start < archive_boundary:
        archive_days = (archive_end - start).days
        archive_chunks = (archive_days + _ARCHIVE_CHUNK_DAYS - 1) // _ARCHIVE_CHUNK_DAYS

    forecast_chunks = 0
    if end > archive_boundary:
        forecast_days = (end - forecast_start).days
        forecast_chunks = (forecast_days + _FORECAST_CHUNK_DAYS - 1) // _FORECAST_CHUNK_DAYS

    logger.info(
        "%s backfill: [%s, %s), archive_boundary=%s, "
        "archive_chunks=%d (≤%d days each), forecast_chunks=%d (≤%d days each)",
        SOURCE_ID, start, end, archive_boundary,
        archive_chunks, _ARCHIVE_CHUNK_DAYS,
        forecast_chunks, _FORECAST_CHUNK_DAYS,
    )


def _run_backfill(**context) -> None:
    from datetime import date as _date

    from scripts.backfill.bulk import backfill_daily_window

    params = context["params"]
    start = datetime.strptime(params["start"], "%Y-%m-%d").date()
    end = datetime.strptime(params["end"], "%Y-%m-%d").date()
    bucket = get_bucket(params)

    today = _date.today()
    # Boundary: windows starting before this date route to the archive API
    # (handled automatically by OpenMeteoFetcher); use larger chunks there.
    archive_boundary = today - timedelta(days=_ARCHIVE_CUTOFF_DAYS)

    cursor = start
    all_results = []
    while cursor < end:
        # Choose chunk size based on which API the fetcher will use.
        chunk_days = _ARCHIVE_CHUNK_DAYS if cursor < archive_boundary else _FORECAST_CHUNK_DAYS
        chunk_end = min(cursor + timedelta(days=chunk_days), end)
        logger.info("Fetching chunk [%s, %s) (chunk_days=%d)", cursor, chunk_end, chunk_days)
        results = backfill_daily_window(SOURCE_ID, start=cursor, end=chunk_end, bucket=bucket)
        all_results.extend(results)
        cursor = chunk_end

    failed = [r for r in all_results if r.status == "failed"]
    if failed:
        for r in failed:
            logger.error("  FAILED: %s", r.error)
        raise RuntimeError(f"Open-Meteo fetch failed: {failed[0].error}")

    total_files = sum(r.manifest_count for r in all_results)
    logger.info("%s: %d daily files written to Bronze", SOURCE_ID, total_files)


with DAG(
    dag_id="dag_backfill_open_meteo",
    description="One-time backfill: Open-Meteo weather history → Bronze (daily partition, wide-fetch)",
    default_args=DEFAULT_ARGS,
    schedule=None,
    catchup=False,
    params=backfill_params,
    tags=["backfill", "open-meteo", "bronze", "weather"],
) as dag:

    check_params = PythonOperator(
        task_id="check_params",
        python_callable=_check_params,
    )

    run_backfill = PythonOperator(
        task_id="run_backfill",
        python_callable=_run_backfill,
        execution_timeout=None,
    )

    check_params >> run_backfill
