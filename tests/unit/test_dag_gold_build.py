"""The gold build DAG's callable, exercised rather than merely imported.

Stage E2 found four defects in this DAG, and only two of them were import
errors. `get_bucket()` called without its required `params` is a TypeError
raised the moment the task body runs — an import test walks straight past it,
which is why one exists here that actually calls `_build`.

Skipped unless apache-airflow is installed (`uv sync --extra airflow`); the
`dags` CI job installs it so these run for real. See O15 in
docs/dev/launch/20260819-gold-dimensional-build-launch.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("airflow", reason="apache-airflow not installed")

DAGS_DIR = Path(__file__).resolve().parents[2] / "dags"


@pytest.fixture()
def dag_module(monkeypatch):
    """Import dags/dag_gold_build.py the way Airflow does: dags/ on sys.path."""
    monkeypatch.syspath_prepend(str(DAGS_DIR))
    import dag_gold_build

    return dag_gold_build


@pytest.fixture()
def captured_argv(monkeypatch):
    """Stand in for Builder so nothing reaches Trino, and record the argv."""
    from scripts.gold import build_gold

    seen: dict[str, list[str]] = {}
    real_parser = build_gold.build_parser

    def fake_parser():
        parser = real_parser()
        real_parse = parser.parse_args

        def parse(argv):
            seen["argv"] = list(argv)
            return real_parse(argv)

        parser.parse_args = parse
        return parser

    class FakeBuilder:
        def __init__(self, args):
            self.args = args

        def build(self) -> int:
            return 0

    monkeypatch.setattr(build_gold, "build_parser", fake_parser)
    monkeypatch.setattr(build_gold, "Builder", FakeBuilder)
    return seen


def test_build_resolves_the_bucket_from_the_param(dag_module, captured_argv):
    dag_module._build(params={"bucket": "uoip-test", "only": ""})
    assert captured_argv["argv"] == ["--bucket", "uoip-test"]


def test_build_falls_back_to_the_env_bucket_when_the_param_is_blank(
    dag_module, captured_argv, monkeypatch
):
    monkeypatch.setenv("S3_BUCKET_NAME", "uoip-from-env")
    dag_module._build(params={"bucket": "", "only": ""})
    assert captured_argv["argv"] == ["--bucket", "uoip-from-env"]


def test_build_forwards_only_when_set(dag_module, captured_argv):
    dag_module._build(params={"bucket": "uoip-test", "only": "seeds"})
    assert captured_argv["argv"] == ["--bucket", "uoip-test", "--only", "seeds"]


def test_build_raises_when_the_builder_reports_a_failing_gate(
    dag_module, captured_argv, monkeypatch
):
    from scripts.gold import build_gold

    class FailingBuilder:
        def __init__(self, args):
            pass

        def build(self) -> int:
            return 2

    monkeypatch.setattr(build_gold, "Builder", FailingBuilder)
    with pytest.raises(RuntimeError, match="exit code 2"):
        dag_module._build(params={"bucket": "uoip-test", "only": ""})


def test_the_dag_exposes_exactly_the_one_task(dag_module):
    assert sorted(t.task_id for t in dag_module.dag.tasks) == ["build_gold"]


def test_retries_is_zero_so_a_failing_gate_is_not_retried_for_an_hour(dag_module):
    # DEFAULT_ARGS carries retries=3; a 35-minute deterministic build must not
    # inherit it. Measured 2026-08-20: a full build is 2,127s.
    assert dag_module.dag.default_args["retries"] == 0


def test_module_is_not_left_in_sys_modules_between_tests():
    # Guard against a stale import masking a later parse error.
    sys.modules.pop("dag_gold_build", None)
