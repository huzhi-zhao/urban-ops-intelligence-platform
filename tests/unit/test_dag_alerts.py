"""
Unit tests for dags/_alerts.py — Airflow failure alerting.

These do not need Airflow: the module takes a context dict and posts HTTP, so
the tests build the context by hand. That matters, because the failure this
module exists to prevent (12 days of silent failures) was itself caused by a
convention nobody could test.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests

DAGS_DIR = Path(__file__).parent.parent.parent / "dags"
if str(DAGS_DIR) not in sys.path:
    sys.path.insert(0, str(DAGS_DIR))

import _alerts  # noqa: E402
from _alerts import ALERT_URL_VARS, WATCHDOG_URL_VAR, alert_on_failure, ping_watchdog  # noqa: E402

WEBHOOK = "https://discord.example/api/webhooks/123/s3cr3t-token"


def _ctx(exception: Exception | None = None, **overrides) -> dict:
    ti = SimpleNamespace(
        dag_id="dag_ingest_weather_archive",
        task_id="run_ingest",
        run_id="scheduled__2026-08-15T06:00:00+00:00",
        try_number=3,
        log_url="http://airflow.example/log?dag_id=x",
        **overrides,
    )
    return {"task_instance": ti, "exception": exception}


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for var in (*ALERT_URL_VARS, WATCHDOG_URL_VAR):
        monkeypatch.delenv(var, raising=False)


# ── alert_on_failure ──────────────────────────────────────────────────────────

def test_unset_webhook_warns_and_does_not_raise(caplog):
    with caplog.at_level(logging.WARNING):
        assert alert_on_failure(_ctx()) is False
    assert "no alert was sent" in caplog.text


def test_payload_is_exactly_what_discord_accepts(monkeypatch):
    """Discord 400s on a malformed body, and a 400 here is invisible — this
    callback only runs when something has already failed."""
    monkeypatch.setenv("BACKFILL_ALERT_WEBHOOK_URL", WEBHOOK)
    with patch.object(_alerts.requests, "post", return_value=MagicMock()) as post:
        assert alert_on_failure(_ctx(RuntimeError("boom"))) is True

    url, kwargs = post.call_args[0][0], post.call_args[1]
    assert url == WEBHOOK
    payload = kwargs["json"]
    assert set(payload) == {"content", "allowed_mentions"}
    assert 0 < len(payload["content"]) <= _alerts.CONTENT_LIMIT
    # An @everyone inside an exception string must not ping the channel.
    assert payload["allowed_mentions"] == {"parse": []}
    for expected in ("dag_ingest_weather_archive", "run_ingest", "scheduled__2026-08-15", "boom"):
        assert expected in payload["content"]


def test_content_is_never_empty(monkeypatch):
    """A body carrying no content/embeds/file is rejected by Discord with 400."""
    monkeypatch.setenv("BACKFILL_ALERT_WEBHOOK_URL", WEBHOOK)
    with patch.object(_alerts, "_describe", return_value=""):
        with patch.object(_alerts.requests, "post", return_value=MagicMock()) as post:
            assert alert_on_failure({}) is True
    assert post.call_args[1]["json"]["content"].strip()


def test_falls_back_to_snapshot_webhook(monkeypatch):
    monkeypatch.setenv("SNAPSHOT_ALERT_WEBHOOK_URL", WEBHOOK)
    with patch.object(_alerts.requests, "post", return_value=MagicMock()) as post:
        assert alert_on_failure(_ctx()) is True
    assert post.call_args[0][0] == WEBHOOK


def test_backfill_webhook_wins_over_snapshot(monkeypatch):
    monkeypatch.setenv("BACKFILL_ALERT_WEBHOOK_URL", WEBHOOK)
    monkeypatch.setenv("SNAPSHOT_ALERT_WEBHOOK_URL", "https://discord.example/other")
    with patch.object(_alerts.requests, "post", return_value=MagicMock()) as post:
        alert_on_failure(_ctx())
    assert post.call_args[0][0] == WEBHOOK


def test_truncation_keeps_the_identifying_head(monkeypatch):
    monkeypatch.setenv("BACKFILL_ALERT_WEBHOOK_URL", WEBHOOK)
    with patch.object(_alerts.requests, "post", return_value=MagicMock()) as post:
        alert_on_failure(_ctx(RuntimeError("x" * 5000)))

    content = post.call_args[1]["json"]["content"]
    assert len(content) <= _alerts.CONTENT_LIMIT
    # The tail is traceback detail; the head is which task failed. Losing the
    # head would make the alert unactionable.
    assert "dag_ingest_weather_archive.run_ingest" in content
    assert "scheduled__2026-08-15T06:00:00+00:00" in content


def test_transport_failure_is_swallowed_and_url_not_logged(monkeypatch, caplog):
    monkeypatch.setenv("BACKFILL_ALERT_WEBHOOK_URL", WEBHOOK)
    err = requests.RequestException()
    err.response = SimpleNamespace(status_code=404)
    with patch.object(_alerts.requests, "post", side_effect=err):
        with caplog.at_level(logging.ERROR):
            assert alert_on_failure(_ctx()) is False
    assert "HTTP 404" in caplog.text
    # The webhook URL is a bearer credential; it must never reach the log.
    assert "s3cr3t-token" not in caplog.text


def test_missing_task_instance_still_reports(monkeypatch):
    monkeypatch.setenv("BACKFILL_ALERT_WEBHOOK_URL", WEBHOOK)
    with patch.object(_alerts.requests, "post", return_value=MagicMock()) as post:
        assert alert_on_failure({"dag_id": "dag_x", "task_id": "t"}) is True
    assert "dag_x.t" in post.call_args[1]["json"]["content"]


# ── ping_watchdog ─────────────────────────────────────────────────────────────

def test_watchdog_unset_is_silent(caplog):
    """No watchdog is registered yet. A warning on every successful run of every
    ingest DAG is how real warnings get tuned out."""
    with caplog.at_level(logging.WARNING):
        assert ping_watchdog("dag_x") is False
    assert caplog.text == ""


def test_watchdog_does_not_fall_back_to_snapshot(monkeypatch):
    """Pinging the snapshot check on Airflow's behalf would silence BO-7."""
    monkeypatch.setenv("SNAPSHOT_WATCHDOG_URL", "https://hc.example/snapshot")
    monkeypatch.setenv("BACKFILL_WATCHDOG_URL", "https://hc.example/backfill")
    with patch.object(_alerts.requests, "get") as get:
        assert ping_watchdog("dag_x") is False
    get.assert_not_called()


def test_watchdog_pings_on_success(monkeypatch):
    monkeypatch.setenv(WATCHDOG_URL_VAR, "https://hc.example/airflow")
    with patch.object(_alerts.requests, "get", return_value=MagicMock()) as get:
        assert ping_watchdog("dag_x") is True
    assert get.call_args[0][0] == "https://hc.example/airflow"


def test_watchdog_failure_is_swallowed(monkeypatch, caplog):
    monkeypatch.setenv(WATCHDOG_URL_VAR, "https://hc.example/airflow-s3cr3t")
    err = requests.RequestException()
    err.response = SimpleNamespace(status_code=500)
    with patch.object(_alerts.requests, "get", side_effect=err):
        with caplog.at_level(logging.ERROR):
            assert ping_watchdog("dag_x") is False
    assert "HTTP 500" in caplog.text
    assert "s3cr3t" not in caplog.text
