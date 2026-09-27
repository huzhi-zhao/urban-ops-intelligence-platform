"""dag_outlook_request's callable, exercised rather than merely imported.

Same reasoning as test_dag_gold_build.py: an import test walks past a task
body that breaks the moment it runs. Skipped unless apache-airflow is
installed; run with `make test-dags`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

pytest.importorskip("airflow", reason="apache-airflow not installed")

DAGS_DIR = Path(__file__).resolve().parents[2] / "dags"


@pytest.fixture()
def dag_module(monkeypatch):
    monkeypatch.syspath_prepend(str(DAGS_DIR))
    import dag_outlook_request

    return dag_outlook_request


@pytest.fixture()
def calls(monkeypatch):
    from scripts.models import outlook_m1

    seen: dict = {"argv": None, "code": 0}

    def fake_main(argv):
        seen["argv"] = list(argv)
        return seen["code"]

    monkeypatch.setattr(outlook_m1, "main", fake_main)
    monkeypatch.setenv("S3_BUCKET_NAME", "uoip-test")
    return seen


def test_the_run_scores_its_local_issue_date_with_the_serving_version(dag_module, calls):
    # 13:30 UTC on 2026-11-15 is 07:30 in Winnipeg, the same local date.
    dag_module._score(data_interval_end=datetime(2026, 11, 15, 13, 30, tzinfo=UTC))

    argv = calls["argv"]
    assert argv[argv.index("--issue-date") + 1] == "2026-11-15"
    assert argv[argv.index("--model-version") + 1] == "m1-poisson-20260822-df31d954"
    assert argv[argv.index("--bucket") + 1] == "uoip-test"
    assert "--upload" in argv and "--skip-existing" in argv


def test_a_non_zero_exit_fails_the_task(dag_module, calls):
    calls["code"] = 1

    with pytest.raises(RuntimeError, match="2026-11-15"):
        dag_module._score(data_interval_end=datetime(2026, 11, 15, 13, 30, tzinfo=UTC))


def test_the_dag_starts_at_the_first_collected_vintage(dag_module):
    """An earlier start would catch up over days with no forecast at all."""
    # Airflow normalises a naive start_date to UTC.
    assert dag_module.dag.default_args["start_date"] == datetime(2026, 9, 26, 13, 30, tzinfo=UTC)
    assert dag_module.dag.catchup is True
