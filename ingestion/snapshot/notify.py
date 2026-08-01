"""
Failure reporting for snapshot collection.

Snapshot collection runs on the storage node under a plain timer, outside
Airflow, so it gets none of Airflow's retry/alert machinery (ADR 0006 §2.2). It
has to report on its own, and it has to cover two distinct failures:

**The run failed.** The process ran and something went wrong — upstream down,
bucket unreachable, a suspiciously small pull. A live process can report this
itself: :func:`notify_failure` posts to a webhook.

**The run never happened.** The machine was down, the timer was removed, the
unit was masked by an upgrade. *No process exists to send anything*, so no
amount of in-process error handling detects it — and for a source that cannot be
re-collected, this is the failure that actually costs history. It needs an
external watchdog with the inverted contract: the run checks in on success, and
the watchdog alerts when a check-in fails to arrive (healthchecks.io and
equivalents). :func:`ping_watchdog` is that check-in.

Both are configured by environment variable and both are **optional**: an unset
URL disables that channel with a warning rather than failing the collection.
Losing an alert is bad; losing a day of irreplaceable history because the
alerting was misconfigured is worse.

    SNAPSHOT_ALERT_WEBHOOK_URL   POST target for failure notifications
    SNAPSHOT_WATCHDOG_URL        dead-man check-in URL, pinged on success
"""

from __future__ import annotations

import logging
import os
import socket

import requests

logger = logging.getLogger(__name__)

ALERT_URL_VAR = "SNAPSHOT_ALERT_WEBHOOK_URL"
WATCHDOG_URL_VAR = "SNAPSHOT_WATCHDOG_URL"

# Short: alerting must never become the thing that hangs the collection.
TIMEOUT_SECS = 10


def notify_failure(source_id: str, message: str) -> bool:
    """POST a failure notice to the alert webhook.

    Returns:
        True if the notice was delivered. Never raises — a failing alert channel
        must not replace the original error with its own.
    """
    url = (os.environ.get(ALERT_URL_VAR) or "").strip()
    if not url:
        logger.warning(
            "%s snapshot failed and %s is not set — no alert was sent",
            source_id, ALERT_URL_VAR,
        )
        return False

    payload = {
        "source_id": source_id,
        "host": socket.gethostname(),
        "status": "failed",
        "text": f"[{source_id}] snapshot collection failed on "
                f"{socket.gethostname()}: {message}",
    }
    try:
        response = requests.post(url, json=payload, timeout=TIMEOUT_SECS)
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error("Could not deliver failure alert for %s: %s", source_id, e)
        return False
    return True


def ping_watchdog(source_id: str) -> bool:
    """Check in with the dead-man watchdog after a successful collection.

    Returns:
        True if the check-in was delivered. Never raises: the data is already
        safely written by this point, and a missed check-in produces a false
        alarm, which is the cheap direction to fail in.
    """
    url = (os.environ.get(WATCHDOG_URL_VAR) or "").strip()
    if not url:
        logger.warning(
            "%s is not set — nothing will notice if this collection stops "
            "running entirely", WATCHDOG_URL_VAR,
        )
        return False

    try:
        response = requests.get(url, timeout=TIMEOUT_SECS)
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error("Could not check in with the watchdog for %s: %s", source_id, e)
        return False
    logger.info("%s: watchdog check-in delivered", source_id)
    return True
