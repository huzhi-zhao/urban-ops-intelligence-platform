"""
Load ``.env`` for the command-line entry points.

The library code (``ingestion/``) reads its configuration from
``os.environ`` and never loads a file — that is correct: Airflow injects the
environment into its workers, and the storage node's systemd unit uses an
``EnvironmentFile``. But a human running a CLI has neither, and until this
existed the only thing in the repo that called ``load_dotenv()`` was
``tests/integration/conftest.py``. The visible symptom was the integration
tests reaching MinIO while the backfill CLI, two commands later in the same
shell, failed with "Missing required object-storage environment variable(s)".

``override=False`` (python-dotenv's default) is load-bearing: a value already
in the environment — from systemd, from Docker, from an explicit ``export``
before a one-off run — wins over the file. Loading ``.env`` must never
silently redirect a deployed job at a different bucket.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_cli_env() -> None:
    """Load ``<repo root>/.env`` into the environment if the file exists.

    Missing file is not an error: the deployed callers supply the environment
    themselves, and ``.env`` is deliberately untracked.
    """
    load_dotenv(REPO_ROOT / ".env", override=False)
