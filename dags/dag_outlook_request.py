"""Daily M1 outlook from the morning's forecast vintage (H2-R12 batch C).

Schedule : 13:30 UTC — after the storage node collects the forecast
           (06:45 America/Winnipeg, i.e. 11:45 or 12:45 UTC) and after
           dag_silver_weather_archive (07:00 UTC) has synced the observed days
           the chain splices in front of the forecast.
Engine   : plain Python in the Airflow worker. The chain needs pandas and
           numpy, not Spark or statsmodels.
Output   : gold/_outlook_runs/issue_date=<local date>/<serving version>/,
           append-only. A rerun of a recorded day is a no-op success.

A missing vintage fails the task after retries, which raises the usual Discord
alert — so this DAG also notices a forecast collection that never happened,
independently of the storage node's own watchdog.

No on_failure_callback here: DEFAULT_ARGS carries it for every DAG, and setting
it locally would override that. Everything below the schedule lives in
scripts/models/outlook_m1.py.

Design: docs/dev/design/20260927-forecast-chain-rehearsal.md §4 batch C.
"""

from __future__ import annotations

from datetime import datetime

from _dag_common import DEFAULT_ARGS, get_bucket
from airflow import DAG
from airflow.operators.python import PythonOperator

# 🔴 Airflow 3's cron schedule gives each run a zero-length data interval at its
# own trigger time (start == end == logical date), not Airflow 2's "the day
# before". So the run *at* 2026-09-27 13:30 scores issue 2026-09-27, the first
# vintage ever collected. The first deploy used 09-26 13:30 on the Airflow 2
# reading and its first run looked for a 09-26 forecast that never existed.
# An earlier start catches up over days with no forecast at all — each one a
# guaranteed failure and a Discord message.
FIRST_RUN = datetime(2026, 9, 27, 13, 30)


def _score(**context) -> None:
    import tempfile

    from scripts.models import outlook_m1

    config = outlook_m1.load_config()
    issue = outlook_m1.issue_date_for(context["data_interval_end"], config)
    # A fresh directory per attempt: the local copy is also append-only, so a
    # retry after a failed upload would otherwise refuse its own first try.
    with tempfile.TemporaryDirectory() as out_dir:
        code = outlook_m1.main(
            outlook_m1.scheduled_argv(issue, config, get_bucket({}), out_dir)
        )
    if code != 0:
        raise RuntimeError(f"outlook for issue {issue} failed with exit code {code}")


with DAG(
    dag_id="dag_outlook_request",
    description="Daily M1 outlook from the morning's forecast vintage",
    default_args={**DEFAULT_ARGS, "start_date": FIRST_RUN},
    schedule="30 13 * * *",
    catchup=True,
    max_active_runs=1,
    tags=["gold", "outlook", "m1", "daily"],
) as dag:
    PythonOperator(task_id="score_outlook", python_callable=_score)
