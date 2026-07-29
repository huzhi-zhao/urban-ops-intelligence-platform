"""
Backfill DAG for SRC-NYC-311 (311 Service Requests).

Partition strategy : daily (one Socrata query per day, parallel up to 4 workers)
Trigger            : manual only (schedule=None)
Params             : start (inclusive), end (exclusive), bucket

Trigger example:
    Airflow UI → DAGs → dag_backfill_nyc_311 → Trigger w/ Config:
    {"start": "2024-01-01", "end": "2025-01-01", "bucket": "nyc-uoip"}
"""

from __future__ import annotations

import logging
from datetime import datetime

from _dag_common import DEFAULT_ARGS, backfill_params, get_bucket
from airflow import DAG
from airflow.operators.python import PythonOperator

logger = logging.getLogger(__name__)

SOURCE_ID = "SRC-NYC-311"


def _check_params(**context) -> None:
    params = context["params"]
    start = datetime.strptime(params["start"], "%Y-%m-%d").date()
    end = datetime.strptime(params["end"], "%Y-%m-%d").date()
    if end <= start:
        raise ValueError(f"end ({end}) must be after start ({start})")
    get_bucket(params)  # validates bucket is resolvable
    logger.info("%s backfill: [%s, %s)", SOURCE_ID, start, end)


def _run_backfill(**context) -> None:
    from scripts.backfill.bulk import backfill_daily_window

    params = context["params"]
    start = datetime.strptime(params["start"], "%Y-%m-%d").date()
    end = datetime.strptime(params["end"], "%Y-%m-%d").date()
    bucket = get_bucket(params)

    results = backfill_daily_window(SOURCE_ID, start=start, end=end, bucket=bucket)

    failed = [r for r in results if r.status == "failed"]
    total_records = sum(r.manifest_count for r in results if r.status == "ok")
    logger.info(
        "%s: %d days processed, %d records written, %d failures",
        SOURCE_ID, len(results), total_records, len(failed),
    )
    if failed:
        for r in failed:
            logger.error("  FAILED day=%s: %s", r.document, r.error)
        raise RuntimeError(
            f"{len(failed)} day(s) failed for {SOURCE_ID}. "
            "Check logs above. Airflow will retry the whole task."
        )


with DAG(
    dag_id="dag_backfill_nyc_311",
    description="One-time backfill: NYC 311 Service Requests → GCS Bronze (daily partition)",
    default_args=DEFAULT_ARGS,
    schedule=None,
    catchup=False,
    params=backfill_params,
    tags=["backfill", "nyc-311", "bronze", "socrata"],
) as dag:

    check_params = PythonOperator(
        task_id="check_params",
        python_callable=_check_params,
    )

    run_backfill = PythonOperator(
        task_id="run_backfill",
        python_callable=_run_backfill,
        execution_timeout=None,  # 311 full-year backfill can take hours
    )

    check_params >> run_backfill
