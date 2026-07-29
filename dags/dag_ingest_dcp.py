"""
Monthly refresh DAG for SRC-DCP (NYC Borough Boundaries GeoJSON).

Schedule        : 06:00 UTC on the 1st of every month
Catchup         : enabled — if scheduler was down, missed refreshes are auto-replayed
max_active_runs : 1 — static source; concurrent runs would just overwrite each other
SLA             : 30 minutes — single GeoJSON file fetch; should be near-instant

Borough boundaries change rarely. Monthly refresh is a conservative safety net
to pick up any upstream geometry corrections without manual intervention.

GCS output: bronze/raw/SRC-DCP/borough_boundaries/data_static.json
"""

from __future__ import annotations

import logging

from _dag_common import DEFAULT_ARGS, get_bucket
from airflow import DAG
from airflow.operators.python import PythonOperator

logger = logging.getLogger(__name__)

SOURCE_ID = "SRC-DCP"


def _run_ingest(**context) -> None:
    from scripts.backfill.bulk import backfill_static

    bucket = get_bucket({})
    logger.info("%s monthly refresh: fetching static borough boundaries", SOURCE_ID)
    results = backfill_static(SOURCE_ID, bucket=bucket)

    failed = [r for r in results if r.status == "failed"]
    if failed:
        raise RuntimeError(f"DCP static fetch failed: {failed[0].error}")

    total_files = sum(r.manifest_count for r in results)
    logger.info("%s: %d static file(s) refreshed in GCS Bronze", SOURCE_ID, total_files)


with DAG(
    dag_id="dag_ingest_dcp",
    description="Monthly refresh: NYC Borough Boundaries GeoJSON → GCS Bronze (static snapshot)",
    default_args=DEFAULT_ARGS,
    schedule=None,
    catchup=False,
    max_active_runs=1,
    tags=["ingest", "dcp", "bronze", "geojson", "static", "monthly"],
) as dag:

    run_ingest = PythonOperator(
        task_id="run_ingest",
        python_callable=_run_ingest,
    )
