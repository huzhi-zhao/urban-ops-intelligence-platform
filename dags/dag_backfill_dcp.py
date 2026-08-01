"""
Backfill DAG for SRC-DCP (NYC Borough Boundaries GeoJSON).

Partition strategy : static (one snapshot, no date dimension)
Trigger            : manual only (schedule=None)
Params             : bucket only (start/end are ignored for static sources)

Re-trigger this DAG only if the borough boundary data is updated upstream.

Trigger example:
    {"start": "2024-01-01", "end": "2024-01-01", "bucket": "nyc-uoip"}
"""

from __future__ import annotations

import logging

from _dag_common import DEFAULT_ARGS, backfill_params, get_bucket
from airflow import DAG
from airflow.operators.python import PythonOperator

logger = logging.getLogger(__name__)

SOURCE_ID = "SRC-DCP"


def _run_backfill(**context) -> None:
    from scripts.backfill.bulk import backfill_static

    params = context["params"]
    bucket = get_bucket(params)

    results = backfill_static(SOURCE_ID, bucket=bucket)

    failed = [r for r in results if r.status == "failed"]
    if failed:
        raise RuntimeError(f"DCP static fetch failed: {failed[0].error}")

    total_files = sum(r.manifest_count for r in results)
    logger.info("%s: %d static file(s) written to Bronze", SOURCE_ID, total_files)


with DAG(
    dag_id="dag_backfill_dcp",
    description="One-time upload: NYC Borough Boundaries GeoJSON → Bronze (static snapshot)",
    default_args=DEFAULT_ARGS,
    schedule=None,
    catchup=False,
    params=backfill_params,
    tags=["backfill", "dcp", "bronze", "geojson", "static"],
) as dag:

    run_backfill = PythonOperator(
        task_id="run_backfill",
        python_callable=_run_backfill,
    )
