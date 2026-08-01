"""
DAG import tests — verify every DAG file in dags/ can be imported without errors.

Airflow's Scheduler silently skips DAG files that fail to import. This test
surfaces those failures in CI before the DAGs reach the Airflow containers.

Run with: make test-unit
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

# Skip the entire module if apache-airflow is not installed.
# Locally: uv run pip install apache-airflow  (or make test-dags)
# In CI / the Airflow containers: airflow is present, tests run normally.
pytest.importorskip("airflow", reason="apache-airflow not installed; skipping DAG import tests")

DAGS_DIR = Path(__file__).parent.parent.parent / "dags"

# Collect all DAG files (dag_*.py) — excludes helpers like _dag_common.py
DAG_FILES = sorted(DAGS_DIR.glob("dag_*.py"))


@pytest.fixture(autouse=True)
def _add_dags_to_path():
    """Put dags/ on sys.path so DAGs can import _dag_common."""
    dags_str = str(DAGS_DIR)
    inserted = dags_str not in sys.path
    if inserted:
        sys.path.insert(0, dags_str)
    yield
    if inserted:
        sys.path.remove(dags_str)


@pytest.mark.parametrize("dag_file", DAG_FILES, ids=lambda p: p.stem)
def test_dag_imports(dag_file: Path, monkeypatch) -> None:
    """Import the DAG module and verify at least one DAG object is created."""
    # Stub out heavy dependencies so the import works without a live GCP connection.
    _stub_airflow_providers(monkeypatch)

    module_name = f"_test_dag_{dag_file.stem}"
    spec = importlib.util.spec_from_file_location(module_name, dag_file)
    assert spec is not None, f"Could not build import spec for {dag_file}"

    module = importlib.util.module_from_spec(spec)
    # Should not raise — syntax errors, bad imports, etc. will surface here.
    spec.loader.exec_module(module)

    # Every DAG file must expose at least one airflow.models.DAG instance.
    from airflow import DAG as AirflowDAG
    dag_objects = [v for v in vars(module).values() if isinstance(v, AirflowDAG)]
    assert dag_objects, (
        f"{dag_file.name} was imported successfully but contains no DAG objects. "
        "Did you forget 'with DAG(...) as dag:'?"
    )


def _stub_airflow_providers(monkeypatch) -> None:
    """Replace heavy runtime imports with lightweight stubs.

    We only need the DAG/Operator classes to be importable — no live connections.
    """
    # scripts.backfill.bulk is imported lazily inside _run_backfill(), so
    # it won't be called at import time. No stub needed.
    pass
