"""Unit tests for ``scripts/_env.py`` — the CLI's ``.env`` loading."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts import _env  # noqa: E402


def _write_env(tmp_path: Path, body: str, monkeypatch) -> None:
    (tmp_path / ".env").write_text(body, encoding="utf-8")
    monkeypatch.setattr(_env, "REPO_ROOT", tmp_path)


def test_values_from_the_file_reach_the_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("S3_BUCKET_NAME", raising=False)
    _write_env(tmp_path, "S3_BUCKET_NAME=from-file\n", monkeypatch)

    _env.load_cli_env()

    assert os.environ["S3_BUCKET_NAME"] == "from-file"


def test_an_already_exported_value_wins_over_the_file(tmp_path, monkeypatch):
    """A deployed job's injected environment must not be silently redirected.

    Airflow and the storage node's systemd unit both supply the environment
    themselves; if ``.env`` overrode that, a stale checked-out file would point
    a live job at a different bucket.
    """
    monkeypatch.setenv("S3_BUCKET_NAME", "from-environment")
    _write_env(tmp_path, "S3_BUCKET_NAME=from-file\n", monkeypatch)

    _env.load_cli_env()

    assert os.environ["S3_BUCKET_NAME"] == "from-environment"


def test_a_missing_env_file_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(_env, "REPO_ROOT", tmp_path)  # no .env written

    _env.load_cli_env()
