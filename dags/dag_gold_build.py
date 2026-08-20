"""Rebuild the Gold dimension and fact tables.

Manual trigger only. The upstreams are not daily: four of the nine dimensions
come from seed CSVs that change when someone edits them, three come from
whole-table reference pulls, and only the snowfall-event chain follows Silver.
A daily schedule would rebuild an unchanged table 364 times a year.

The build is a Python call rather than a Trino operator: it is the same shape
as sync_partition_metadata, and it keeps the dependency order, the seed
loading and the gates in one place that the CLI also uses.
"""

from __future__ import annotations

from airflow import DAG
from airflow.models.param import Param
from airflow.operators.python import PythonOperator

from dags._dag_common import DEFAULT_ARGS, get_bucket


def _build(**context) -> None:
    from scripts.gold.build_gold import Builder, build_parser

    params = context["params"]
    argv = ["--bucket", params.get("bucket") or get_bucket()]
    if params.get("only"):
        argv += ["--only", params["only"]]
    args = build_parser().parse_args(argv)
    code = Builder(args).build()
    if code != 0:
        raise RuntimeError(
            f"gold build failed with exit code {code} — see the task log for the failing gate"
        )


with DAG(
    dag_id="dag_gold_build",
    description="Rebuild the Gold dimensions and facts (manual)",
    # retries=0 overrides DEFAULT_ARGS' 3: the build is manually triggered and
    # takes ~15 minutes, and its usual failure is a row-count gate — which is
    # deterministic, so three more attempts cost an hour and three more Discord
    # notices to reach the same verdict. Same override shape as dag_smoke_alert.
    # No start_date here: DEFAULT_ARGS carries one. `days_ago` was removed in
    # Airflow 3 (this image is 3.2.2) and importing it broke the DAG at parse.
    default_args={**DEFAULT_ARGS, "retries": 0},
    schedule=None,
    catchup=False,
    max_active_runs=1,
    tags=["gold", "manual"],
    params={
        "bucket": Param(
            "", type="string", description="Object storage bucket; blank uses the env default"
        ),
        "only": Param(
            "",
            type="string",
            description="seeds | dims | facts | a single table name; blank builds all",
        ),
    },
) as dag:
    # No on_failure_callback here on purpose: DEFAULT_ARGS already carries
    # alert_on_failure, and setting it locally would override it.
    PythonOperator(task_id="build_gold", python_callable=_build)
