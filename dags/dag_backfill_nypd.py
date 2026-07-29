"""
Backfill DAG for SRC-NYPD (NYPD Public Safety — collisions, complaints, shootings).

Partition strategy : monthly (one Socrata query per dataset per month, 4 datasets)
Trigger            : manual only (schedule=None)
Params             : start (inclusive), end (exclusive), bucket

Note: NYPD has 4 datasets sharing one Socrata token → max_workers=2 to avoid rate limits.

Trigger example:
    {"start": "2024-01-01", "end": "2025-01-01", "bucket": "nyc-uoip"}
"""

from __future__ import annotations

import logging
from datetime import datetime

from _dag_common import DEFAULT_ARGS, backfill_params, get_bucket
from airflow import DAG
from airflow.operators.python import PythonOperator

logger = logging.getLogger(__name__)

SOURCE_ID = "SRC-NYPD"


def _check_params(**context) -> None:
    params = context["params"]
    start = datetime.strptime(params["start"], "%Y-%m-%d").date()
    end = datetime.strptime(params["end"], "%Y-%m-%d").date()
    if end <= start:
        raise ValueError(f"end ({end}) must be after start ({start})")
    get_bucket(params)
    logger.info("%s backfill: [%s, %s)", SOURCE_ID, start, end)


def _run_backfill(**context) -> None:
    from scripts.backfill.bulk import backfill_monthly_window

    params = context["params"]
    start = datetime.strptime(params["start"], "%Y-%m-%d").date()
    end = datetime.strptime(params["end"], "%Y-%m-%d").date()
    bucket = get_bucket(params)

    results = backfill_monthly_window(SOURCE_ID, start=start, end=end, bucket=bucket)

    failed = [r for r in results if r.status == "failed"]
    total_records = sum(r.manifest_count for r in results if r.status == "ok")
    logger.info(
        "%s: %d months processed, %d dataset shards written, %d failures",
        SOURCE_ID, len(results), total_records, len(failed),
    )
    if failed:
        for r in failed:
            logger.error("  FAILED month=%s: %s", r.document, r.error)
        raise RuntimeError(
            f"{len(failed)} month(s) failed for {SOURCE_ID}. "
            "Check logs above. Airflow will retry the whole task."
        )


with DAG(
    dag_id="dag_backfill_nypd",
    description="One-time backfill: NYPD Public Safety → GCS Bronze (monthly partition)",
    default_args=DEFAULT_ARGS,
    schedule=None,
    catchup=False,
    params=backfill_params,
    tags=["backfill", "nypd", "bronze", "socrata"],
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
