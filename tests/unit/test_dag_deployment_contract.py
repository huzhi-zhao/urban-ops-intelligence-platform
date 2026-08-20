"""What a DAG needs from the deployment, checked without Airflow installed.

Both assertions here come from stage E2 of the L2 launch: `dag_gold_build`
passed `make lint`, `py_compile` and every existing unit test, and would still
have failed in the Airflow container — once at parse time and once at run time.
`test_dag_imports.py` cannot catch either, because it skips locally (no
airflow) and because an import test never touches the container's filesystem.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DAGS_DIR = REPO_ROOT / "dags"
COMPOSE = REPO_ROOT / "infra" / "docker" / "docker-compose.yml"

# Removed outright in Airflow 3 (the deployed image is 3.2.2), so importing one
# of these takes the whole DAG file out of the scheduler with a parse error.
REMOVED_IN_AIRFLOW_3 = ("airflow.utils.dates",)


@pytest.mark.parametrize("dag_file", sorted(DAGS_DIR.glob("dag_*.py")), ids=lambda p: p.name)
def test_no_dag_imports_a_module_removed_in_airflow_3(dag_file: Path) -> None:
    text = dag_file.read_text()
    for module in REMOVED_IN_AIRFLOW_3:
        assert f"import {module}" not in text and f"from {module}" not in text, (
            f"{dag_file.name} imports {module}, removed in Airflow 3 — "
            "the scheduler will skip this DAG with a parse error"
        )


@pytest.mark.parametrize("dag_file", sorted(DAGS_DIR.glob("dag_*.py")), ids=lambda p: p.name)
def test_dag_files_import_their_siblings_as_top_level_modules(dag_file: Path) -> None:
    """`from dags._dag_common import ...` is a ModuleNotFoundError in Airflow.

    Airflow puts the *dags folder itself* on sys.path, so `_dag_common` and its
    siblings are top-level modules and there is no `dags` package to reach them
    through. The repo-wide "absolute imports only" convention does not reach
    inside dags/ for that reason, and dag_gold_build was the one file that
    followed it there — it was the third of three parse-time failures found in
    stage E2.
    """
    # Line-anchored so a comment *about* the wrong form (dag_gold_build carries
    # one) is not itself a failure.
    offenders = re.findall(r"^\s*(?:from dags\.|import dags\b).*", dag_file.read_text(), re.M)
    assert not offenders, (
        f"{dag_file.name} imports a sibling as `dags.<module>`; inside dags/ the "
        f"siblings are top-level modules (`from _dag_common import ...`): {offenders}"
    )


def _plugin_mounts() -> set[str]:
    """Host directories mounted under /opt/airflow/plugins, by host basename."""
    pattern = re.compile(r"^\s*-\s*\.\./\.\./([\w.-]+):/opt/airflow/plugins/([\w.-]+)", re.M)
    return {host for host, container in pattern.findall(COMPOSE.read_text()) if host == container}


@pytest.mark.parametrize("directory", ["sql", "config", "scripts", "ingestion", "spark"])
def test_repo_root_directories_the_dags_read_are_mounted(directory: str) -> None:
    """scripts/ resolves DDL_DIR, DML_DIR and SEED_DIR from its own parents[2].

    Inside the container that is /opt/airflow/plugins, so every such directory
    has to be mounted there under its own name or the path silently does not
    exist. `sql/` was the one missing when dag_gold_build was written: it parses
    fine and dies at run time on a missing sql/ddl/<table>.sql.
    """
    assert directory in _plugin_mounts(), (
        f"{directory}/ is not mounted at /opt/airflow/plugins/{directory} in "
        "infra/docker/docker-compose.yml"
    )
