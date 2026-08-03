"""
Daily incremental ingest DAG for the service-request source (311) → Bronze.

Schedule        : 05:00 UTC every day — an hour before the weather DAG, so the
                  two do not compete for the same worker slot.
Window          : a LOOKBACK_DAYS-day window ending at the run's data date.
Catchup         : enabled — missed days are auto-backfilled on scheduler restart
max_active_runs : 1 — one Socrata token, no benefit from parallel runs

The lookback exists because 311 rows arrive and change late: a case opened on
D can be entered, re-contacted or closed days afterwards, and its Bronze file
is keyed by `open_date`. A single-day window would freeze D's file before those
rows exist. AGENTS.md makes the 7-day window the convention for Socrata sources.

⚠️ This means each run rewrites the last LOOKBACK_DAYS Bronze files, which sits
awkwardly against "never overwrite a Bronze file" (CLAUDE.md). The reconciliation
is that the rule exists to prevent *data loss*, and a lookback rewrite is a
replay of the same query over a superset of rows — nothing already captured is
dropped, and Silver dedupes on `(case_id, interaction_id)`. The one case that
would lose data is an upstream *deletion* inside the window; a shrinking
`manifest_count` for a day already written is the signal for it.

The source id below is the registry key from config/sources/, not business
logic — the DAG only says *which* source it schedules. Everything about how the
data is fetched, split and written lives in the source YAML and the backfill
layers (CLAUDE.md 城市无关护栏 §1).

Bronze output: bronze/raw/SRC-WPG-311/service_requests/{YYYY-MM}/data_{YYYY-MM-DD}.ndjson.gz
"""

from __future__ import annotations

import logging
from datetime import timedelta

from _dag_common import DEFAULT_ARGS, get_bucket, get_yesterday
from airflow import DAG
from airflow.operators.python import PythonOperator

logger = logging.getLogger(__name__)

SOURCE_ID = "SRC-WPG-311"
LOOKBACK_DAYS = 7


def _run_ingest(**context) -> None:
    from scripts.backfill.bulk import backfill_daily_window

    target_date = get_yesterday(context)
    window_start = target_date - timedelta(days=LOOKBACK_DAYS - 1)
    window_end = target_date + timedelta(days=1)
    bucket = get_bucket({})

    logger.info(
        "%s ingest: window=[%s, %s) (%d-day lookback ending %s)",
        SOURCE_ID, window_start, window_end, LOOKBACK_DAYS, target_date,
    )
    results = backfill_daily_window(
        SOURCE_ID, start=window_start, end=window_end, bucket=bucket
    )

    failed = [r for r in results if r.status == "failed"]
    total_records = sum(r.manifest_count for r in results if r.status == "ok")
    logger.info(
        "%s: %d/%d days written, %d records, %d failures",
        SOURCE_ID, len(results) - len(failed), len(results), total_records, len(failed),
    )
    if failed:
        for r in failed:
            logger.error("  FAILED %s: %s", r.document, r.error)
        raise RuntimeError(f"{SOURCE_ID} ingest failed on {len(failed)} day(s): {failed[0].error}")


with DAG(
    dag_id="dag_ingest_service_requests",
    description="Daily incremental: 311 service requests → Bronze (7-day lookback)",
    default_args=DEFAULT_ARGS,
    schedule="0 5 * * *",
    catchup=True,
    max_active_runs=1,
    tags=["ingest", "bronze", "service-requests", "daily"],
) as dag:

    run_ingest = PythonOperator(
        task_id="run_ingest",
        python_callable=_run_ingest,
    )
