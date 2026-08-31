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


class FakeTi:
    """Just enough TaskInstance for the run_id XCom the three tasks share."""

    def __init__(self, pulled: str | None = None) -> None:
        self.pushed: dict = {}
        self._pulled = pulled

    def xcom_push(self, key, value):
        self.pushed[key] = value

    def xcom_pull(self, task_ids, key):
        return self._pulled


def call_audit(module, ti=None, **params):
    base = {"bucket": "uoip-test", "cadence": "daily", "window_days": 14}
    base.update(params)
    return module._audit(params=base, ti=ti or FakeTi())


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
    ti = FakeTi()
    call_audit(dag_module, ti)
    run_id = ti.pushed["dq_run_id"]
    assert captured["argv"] == [
        "--cadence", "daily", "--bucket", "uoip-test", "--run-id", run_id,
        "--window-days", "14",
    ]


def test_the_bucket_falls_back_to_the_env(dag_module, captured, monkeypatch):
    monkeypatch.setenv("S3_BUCKET_NAME", "uoip-from-env")
    ti = FakeTi()
    call_audit(dag_module, ti, bucket="", window_days=0)
    assert captured["argv"] == [
        "--cadence", "daily", "--bucket", "uoip-from-env",
        "--run-id", ti.pushed["dq_run_id"],
    ]


def test_the_run_id_is_generated_before_the_audit_runs(dag_module, captured):
    """🔴 The three tasks share one id by construction. Reading "the newest
    run" back out of the log downstream guesses from a timestamp — and on the
    day the audit writes nothing at all, there is no newest run to find."""
    ti = FakeTi()
    call_audit(dag_module, ti)
    assert ti.pushed["dq_run_id"].startswith("dq-")
    assert ti.pushed["dq_run_id"] in captured["argv"]


def test_the_run_id_is_pushed_even_when_the_audit_then_raises(dag_module, captured):
    """The `unknown` path depends on it: certify runs under all_done and still
    needs to name the run whose checks could not be executed."""
    captured["code"] = 2
    ti = FakeTi()
    with pytest.raises(RuntimeError):
        call_audit(dag_module, ti)
    assert "dq_run_id" in ti.pushed


def test_a_finding_does_not_raise(dag_module, captured):
    """🔴 The claim the whole design rests on: exit 0 covers "checks failed",
    so the task stays green and the DAG does not get muted."""
    captured["code"] = 0
    call_audit(dag_module)


@pytest.mark.parametrize("code", [2, 3])
def test_an_audit_that_could_not_run_does_raise(dag_module, captured, code):
    captured["code"] = code
    with pytest.raises(RuntimeError, match="could not run"):
        call_audit(dag_module)


def test_the_dag_chains_audit_then_scorecard_then_certify(dag_module):
    assert sorted(t.task_id for t in dag_module.dag.tasks) == [
        "certify", "run_dq_audit", "scorecard",
    ]
    tasks = {t.task_id: t for t in dag_module.dag.tasks}
    assert tasks["run_dq_audit"].downstream_task_ids == {"scorecard"}
    assert tasks["scorecard"].downstream_task_ids == {"certify"}


@pytest.mark.parametrize("task_id", ["scorecard", "certify"])
def test_the_downstream_tasks_run_even_when_the_audit_raised(dag_module, task_id):
    """🔴 design §5 — the audit raises exactly when a check could not run, and
    that is the day a `unknown` certification row has to be written. Under
    all_success there would be no row, which reads as "nothing was wrong"."""
    tasks = {t.task_id: t for t in dag_module.dag.tasks}
    assert str(tasks[task_id].trigger_rule).lower().endswith("all_done")


def test_certify_never_raises_on_a_verdict(dag_module, monkeypatch):
    """`suspect` and `unknown` are results this task records, not failures."""
    from scripts.dq import certify

    seen: dict = {}
    monkeypatch.setattr(certify, "main", lambda argv: seen.update(argv=list(argv)) or 0)
    dag_module._certify(params={"bucket": "uoip-test"}, ti=FakeTi("dq-123"))
    assert seen["argv"] == ["--run-id", "dq-123", "--bucket", "uoip-test"]


def test_the_scorecard_task_scores_the_run_the_audit_just_wrote(dag_module, monkeypatch):
    from scripts.dq import scorecard

    seen: dict = {}
    monkeypatch.setattr(scorecard, "main", lambda argv: seen.update(argv=list(argv)) or 0)
    dag_module._scorecard(params={}, ti=FakeTi("dq-123"))
    assert seen["argv"] == ["--notify", "--run-id", "dq-123"]


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
