"""
Failure alerting for Airflow DAGs.

CLAUDE.md has required every DAG to carry an ``on_failure_callback`` since the
Airflow conventions were written, but no such utility ever existed — so the
convention was inert, and `dag_backfill_silver_weather_archive` failed for 12
consecutive days without notifying anyone. This module is that utility. See
``docs/dev/design/20260816-failure-alerting-and-followups.md``.

Two channels, because there are two distinct failures and only one of them can
be reported from inside the process:

**The task ran and failed.** A live process exists, so it can report itself:
:func:`alert_on_failure` is wired into ``DEFAULT_ARGS`` in ``_dag_common`` and
therefore covers every DAG at once, including ones not written yet.

**The task never ran.** Scheduler down, DAG unparseable, container gone — *no
process exists to run any callback*, which is the other half of the same silence.
Only an external watchdog sees this: :func:`ping_watchdog` checks in after a
successful run and the watchdog (healthchecks.io or equivalent) alerts when a
check-in fails to arrive.

    BACKFILL_ALERT_WEBHOOK_URL   POST target, falls back to SNAPSHOT_ALERT_WEBHOOK_URL
    AIRFLOW_WATCHDOG_URL         dead-man check-in, **no fallback** (see below)

The watchdog side is **opt-in**: no watchdog is registered yet, so an unset
``AIRFLOW_WATCHDOG_URL`` disables that path silently rather than warning. It
would otherwise log once per successful run — a warning that fires every day and
means nothing is how real warnings get ignored. The alert side still warns when
its webhook is missing, because there it is a *failure* going unreported.

The alert channel is shared with the backfill scripts on purpose: a webhook is
only a delivery address, and one Discord channel for all UOIP failures has no
downside worth another environment variable.

The watchdog deliberately does **not** fall back to ``SNAPSHOT_WATCHDOG_URL``,
for the same reason ``BACKFILL_WATCHDOG_URL`` refuses to
(``.claude/rules/backfill.md``): checking in there on a day the snapshot
collection never ran would suppress precisely the alert that check exists to
raise.

Both channels are optional and neither ever raises — an alert that fails must
not replace the original error with its own.
"""

from __future__ import annotations

import logging
import os
import socket

import requests

# Reused rather than re-derived: redaction (the webhook URL *is* the credential,
# and requests' exception strings embed it), Discord's 2000-char `content`
# ceiling, and the short timeout that keeps alerting from becoming the thing
# that hangs. Identical requirements, so identical code.
from ingestion.snapshot.notify import CONTENT_LIMIT, TIMEOUT_SECS, _redact

logger = logging.getLogger(__name__)

# Checked in order; the first one set wins.
ALERT_URL_VARS = ("BACKFILL_ALERT_WEBHOOK_URL", "SNAPSHOT_ALERT_WEBHOOK_URL")
WATCHDOG_URL_VAR = "AIRFLOW_WATCHDOG_URL"


def _resolve_alert_url() -> str:
    for var in ALERT_URL_VARS:
        url = (os.environ.get(var) or "").strip()
        if url:
            return url
    return ""


def _describe(context: dict) -> str:
    """Render a failed task instance as one line of identifying text.

    Identity leads the string so that truncating the tail at Discord's ceiling
    drops exception detail rather than the answer to "which task, which run".
    """
    ti = context.get("task_instance")
    dag_id = getattr(ti, "dag_id", None) or context.get("dag_id") or "<unknown dag>"
    task_id = getattr(ti, "task_id", None) or context.get("task_id") or "<unknown task>"
    run_id = getattr(ti, "run_id", None) or context.get("run_id") or "<unknown run>"
    try_number = getattr(ti, "try_number", None)
    log_url = getattr(ti, "log_url", None)

    head = f"❌ [airflow] {dag_id}.{task_id} failed (run_id={run_id}"
    if try_number is not None:
        head += f", try={try_number}"
    head += f") on {socket.gethostname()}"
    if log_url:
        head += f"\nlog: {log_url}"

    exception = context.get("exception")
    if exception is not None:
        head += f"\n{type(exception).__name__}: {exception}"
    return head


def alert_on_failure(context: dict) -> bool:
    """Airflow ``on_failure_callback``: POST a failure notice to the webhook.

    Returns:
        True if the notice was delivered. Never raises — an exception here is
        swallowed by Airflow into the scheduler log, which is exactly the place
        nobody is watching.
    """
    url = _resolve_alert_url()
    text = _describe(context)
    if not url:
        logger.warning(
            "Task failed and none of %s is set — no alert was sent: %s",
            "/".join(ALERT_URL_VARS), text,
        )
        return False

    # Discord validates the webhook body and answers a bad one with 400 — and a
    # 400 here is invisible, because this code path only runs when something has
    # *already* failed. So the body carries exactly the keys Discord documents
    # and nothing else:
    #
    #   - `content` must be present and non-empty (a body with none of
    #     content/embeds/file is rejected) and at most 2000 characters;
    #   - `allowed_mentions: {"parse": []}` keeps an @everyone that happens to
    #     appear inside an exception string from actually pinging the channel.
    #
    # Slack's `text` key is deliberately NOT sent alongside: extra top-level keys
    # are what make these payloads fragile, and the destination here is Discord.
    # Repointing this at Slack means changing this dict, not just the URL.
    payload = {
        "content": (text[:CONTENT_LIMIT] or "❌ [airflow] a task failed (no detail available)"),
        "allowed_mentions": {"parse": []},
    }
    try:
        response = requests.post(url, json=payload, timeout=TIMEOUT_SECS)
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error("Could not deliver failure alert: %s", _redact(e))
        return False
    return True


def ping_watchdog(label: str) -> bool:
    """Check in with the dead-man watchdog after a successful DAG run.

    Args:
        label: what checked in, for the log line only — the watchdog identity is
            the URL itself.

    Returns:
        True if the check-in was delivered. Never raises: the run has already
        succeeded by this point, and a missed check-in produces a false alarm,
        which is the cheap direction to fail in.
    """
    url = (os.environ.get(WATCHDOG_URL_VAR) or "").strip()
    if not url:
        # Not configured = not wanted. Deliberately debug, not warning: this
        # would fire on every successful run of every ingest DAG.
        logger.debug("%s is not set — skipping watchdog check-in for %s",
                     WATCHDOG_URL_VAR, label)
        return False

    try:
        response = requests.get(url, timeout=TIMEOUT_SECS)
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error("Could not check in with the watchdog for %s: %s", label, _redact(e))
        return False
    logger.info("%s: watchdog check-in delivered", label)
    return True
