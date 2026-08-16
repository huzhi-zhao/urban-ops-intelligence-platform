"""
Manual smoke test for the failure-alert channel (dags/_alerts.py).

Triggering this DAG fails one task on purpose, which is the only end-to-end way
to confirm that a real Discord message arrives: the callback runs inside the
worker process, reads the environment that container actually has, and posts
over that host's network. Unit tests cover the payload; none of them can tell
you the webhook in `.env` is still valid or that the container even sees it.

This exists because the alerting convention was written down, believed to be in
effect, and silently absent for months — "looks configured" is exactly the
evidence that failed last time.

    schedule=None      never runs on its own
    retries=0          one failure, one alert (DEFAULT_ARGS' 3 retries would
                       otherwise mean a 15-minute wait and 1 alert at the end)
    writes nothing     no bucket, no Spark, no upstream call

Run it from the UI (Trigger DAG) or:

    docker compose exec airflow-scheduler airflow dags trigger dag_smoke_alert

Expected: the task fails, and within seconds the Discord channel shows a message
naming `dag_smoke_alert.fail_on_purpose`, its run_id and a log link. If nothing
arrives, the task log carries the reason — an unset webhook logs a warning
naming the variables, a rejected POST logs the HTTP status (never the URL).
"""

from __future__ import annotations

from _dag_common import DEFAULT_ARGS
from airflow import DAG
from airflow.operators.python import PythonOperator


def _fail(**context) -> None:
    raise RuntimeError(
        "Deliberate failure from dag_smoke_alert — if you are reading this in "
        "Discord, the alert channel works."
    )


with DAG(
    dag_id="dag_smoke_alert",
    description="Manual: fail one task on purpose to verify the Discord alert channel",
    default_args={**DEFAULT_ARGS, "retries": 0},
    schedule=None,
    catchup=False,
    max_active_runs=1,
    tags=["smoke", "alerting", "manual"],
) as dag:

    fail_on_purpose = PythonOperator(
        task_id="fail_on_purpose",
        python_callable=_fail,
    )
