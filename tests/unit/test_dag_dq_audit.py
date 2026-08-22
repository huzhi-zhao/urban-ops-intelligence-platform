"""The DQ audit DAG's callable, exercised rather than merely imported.

Same reasoning as test_dag_gold_build: the defects that survive `make lint`,
`py_compile` and an import test are the ones in the task *body* — a missing
`params` argument to `get_bucket()` is a TypeError raised the moment the task
runs (L2 launch §4.11 / O15).

Skipped unless apache-airflow is installed; `make test-dags` and CI's `dags`
job install it so these run for real.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("airflow", reason="apache-airflow not installed")

DAGS_DIR = Path(__file__).resolve().parents[2] / "dags"


@pytest.fixture()
def dag_module(monkeypatch):
    monkeypatch.syspath_prepend(str(DAGS_DIR))
    import dag_dq_audit

    return dag_dq_audit


@pytest.fixture()
def captured(monkeypatch):
    """Stand in for run_audit.main so nothing reaches Trino."""
    from scripts.dq import run_audit

    seen: dict = {"code": 0}

    def fake_main(argv):
        seen["argv"] = list(argv)
        return seen["code"]

    monkeypatch.setattr(run_audit, "main", fake_main)
    return seen


def test_the_callable_passes_the_bucket_and_the_cadence(dag_module, captured):
    dag_module._audit(params={"bucket": "uoip-test", "cadence": "daily", "window_days": 14})
    assert captured["argv"] == [
        "--cadence", "daily", "--bucket", "uoip-test", "--window-days", "14",
    ]


def test_the_bucket_falls_back_to_the_env(dag_module, captured, monkeypatch):
    monkeypatch.setenv("S3_BUCKET_NAME", "uoip-from-env")
    dag_module._audit(params={"bucket": "", "cadence": "daily", "window_days": 0})
    assert captured["argv"] == ["--cadence", "daily", "--bucket", "uoip-from-env"]


def test_a_finding_does_not_raise(dag_module, captured):
    """🔴 The claim the whole design rests on: exit 0 covers "checks failed",
    so the task stays green and the DAG does not get muted."""
    captured["code"] = 0
    dag_module._audit(params={"bucket": "uoip-test", "cadence": "daily", "window_days": 14})


@pytest.mark.parametrize("code", [2, 3])
def test_an_audit_that_could_not_run_does_raise(dag_module, captured, code):
    captured["code"] = code
    with pytest.raises(RuntimeError, match="could not run"):
        dag_module._audit(params={"bucket": "uoip-test", "cadence": "daily", "window_days": 14})


def test_the_dag_exposes_exactly_the_one_task(dag_module):
    assert sorted(t.task_id for t in dag_module.dag.tasks) == ["run_dq_audit"]


def test_the_dag_does_not_override_the_shared_failure_callback(dag_module):
    """DEFAULT_ARGS carries alert_on_failure; setting it locally overrides it."""
    text = (DAGS_DIR / "dag_dq_audit.py").read_text()
    assert "on_failure_callback=" not in text


def test_the_dag_is_scheduled_daily_and_does_not_catch_up(dag_module):
    # Re-running the audit for a past date would re-measure today's tables and
    # file the answer under the wrong day.
    # `schedule`, not `schedule_interval`: the latter was removed in Airflow 3.
    assert dag_module.dag.schedule == "30 8 * * *"
    assert dag_module.dag.catchup is False


def test_module_is_not_left_in_sys_modules_between_tests():
    sys.modules.pop("dag_dq_audit", None)
