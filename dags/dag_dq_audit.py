"""Out-of-pipeline data-quality audit — daily, and deliberately not blocking.

Everything else in this platform checks data *before* it is written: the Spark
jobs assert, the Gold build gates. This DAG looks at what is already on disk
and asks whether it still holds today — which is the only way to see chronic
drift, a silent stall, or a cross-batch inconsistency (design §2).

🔴 **A finding does not fail this task.** Bronze is immutable and Gold is
rebuilt by hand, so exiting non-zero would not repair anything; it would leave
the DAG red until a human did, and a permanently red DAG is a muted DAG. The
same reasoning `dag_audit_bronze` already runs under (ADR 0012 §1 rule 2).
What *does* raise is a check that could not be executed at all — that is the
audit being broken, not the data. Findings reach people through Discord, which
`scripts.dq.run_audit` posts directly.
"""

from __future__ import annotations

# Bare, not `from dags._dag_common` — Airflow puts the dags folder itself on
# sys.path, so its modules are top-level and there is no `dags` package.
from _dag_common import DEFAULT_ARGS, get_bucket
from airflow import DAG
from airflow.models.param import Param
from airflow.operators.python import PythonOperator

# Exit codes from scripts.dq.run_audit. 0 and 1 are outcomes about the data;
# 2 and 3 are outcomes about the audit.
EXIT_CHECK_COULD_NOT_RUN = 2
EXIT_CONFIG = 3


def _audit(**context) -> None:
    from scripts.dq.run_audit import main

    params = context["params"]
    argv = ["--cadence", params.get("cadence") or "daily", "--bucket", get_bucket(params)]
    if params.get("window_days"):
        argv += ["--window-days", str(params["window_days"])]
    code = main(argv)
    if code in (EXIT_CHECK_COULD_NOT_RUN, EXIT_CONFIG):
        raise RuntimeError(
            f"the DQ audit itself could not run (exit {code}) — see the task log. "
            "A *finding* would not reach this line: findings are reported, not raised."
        )


with DAG(
    dag_id="dag_dq_audit",
    description="Out-of-pipeline DQ audit — reports and logs, never blocks",
    # No start_date here: DEFAULT_ARGS carries one. `days_ago` was removed in
    # Airflow 3 (this image is 3.2.2) and importing it breaks the DAG at parse.
    default_args=DEFAULT_ARGS,
    # After the Gold build's usual window and after dag_audit_bronze (0 8), so a
    # morning run reads the day's data rather than yesterday's.
    schedule="30 8 * * *",
    # catchup=False: the audit asks "is this true *now*". Re-running it for a
    # past date would re-measure today's tables and file the answer under the
    # wrong day, which is worse than the gap it would fill.
    catchup=False,
    max_active_runs=1,
    tags=["dq", "audit"],
    params={
        "bucket": Param(
            "", type="string", description="Object storage bucket; blank uses the env default"
        ),
        "cadence": Param(
            "daily",
            type="string",
            enum=["daily", "weekly", "manual"],
            description="daily runs the daily rules; manual is the full sweep",
        ),
        "window_days": Param(
            14,
            type="integer",
            description="Rolling window for the Silver checks. Never a whole-table scan (O13).",
        ),
    },
) as dag:
    # No on_failure_callback here on purpose: DEFAULT_ARGS already carries
    # alert_on_failure, and setting it locally would override it.
    PythonOperator(task_id="run_dq_audit", python_callable=_audit)
