"""
Daily Bronze audit DAG — scans Bronze manifests and auto-fills any gaps.

Schedule        : 08:00 UTC every day (2 hours after ingest DAGs finish)
Catchup         : disabled — audit always looks at a fixed rolling window, not history
max_active_runs : 1

What it does
────────────
1. For each daily source (311, Open-Meteo): check every date in the last
   AUDIT_WINDOW_DAYS days has a manifest object in Bronze.
2. For each monthly source (NYPD): check every month in the last
   AUDIT_WINDOW_MONTHS months has a manifest file.
3. Any gaps found → call the corresponding bulk function directly to fill them.
   Snapshot sources (BO-7) are the exception: they are checked and reported but
   never filled, because their upstream keeps no history — re-running would file
   today's data under a past date and fabricate history rather than recover it.
4. Logs a structured audit report at the end. If any gap could not be filled,
   the task raises so Airflow marks the Run as failed (and retries).

Why scan manifests instead of data files?
  Manifests are small JSON files written atomically alongside every data file.
  A missing manifest = missing data. Checking manifests is cheap (list + head),
  avoids downloading large data files.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from _dag_common import DEFAULT_ARGS, get_bucket, get_yesterday
from airflow import DAG
from airflow.operators.python import PythonOperator

logger = logging.getLogger(__name__)

# How far back the audit looks
AUDIT_WINDOW_DAYS = 14       # for daily sources: check last 14 days
AUDIT_WINDOW_MONTHS = 3      # for monthly sources: check last 3 months

# Source definitions — mirrors SOURCES in bronze_profiler.py but only what audit needs
DAILY_SOURCES = [
    ("SRC-NYC-311",    "nyc_311"),
    ("SRC-Open-Meteo", "nyc_weather_forecast"),
]
MONTHLY_SOURCES = [
    ("SRC-NYPD", "nypd_collisions"),
    ("SRC-NYPD", "nypd_complaint_historic"),
    ("SRC-NYPD", "nypd_complaint_current"),
    ("SRC-NYPD", "nypd_shooting_incident"),
]
# SRC-DCP is static (borough boundaries never change) — excluded from audit

# Snapshot sources are CHECKED but never filled: their upstream keeps no history,
# so a missing day cannot be recovered by re-running anything. Reporting it is
# still worth doing — it is how a silently stopped collection gets noticed.
SNAPSHOT_SOURCES = [
    ("SRC-WPG-SNOW", "snow_clearing_status"),
]


# ── Manifest existence checks ────────────────────────────────────────────────
#
# These check for an exact key rather than listing a prefix, and manifests are
# stored uncompressed, so the gap-detection logic was unaffected by the move to
# gzipped data files — only the client changed.

def _object_exists(client, bucket_name: str, key: str) -> bool:
    from botocore.exceptions import ClientError

    try:
        client.head_object(Bucket=bucket_name, Key=key)
    except ClientError:
        return False
    return True


def _manifest_exists_daily(client, bucket_name: str, source_id: str, dataset: str, day: date) -> bool:
    month = day.strftime("%Y-%m")
    key = f"bronze/raw/{source_id}/{dataset}/{month}/manifest_{day.isoformat()}.json"
    return _object_exists(client, bucket_name, key)


def _manifest_exists_monthly(client, bucket_name: str, source_id: str, dataset: str, month_start: date) -> bool:
    month = month_start.strftime("%Y-%m")
    key = f"bronze/raw/{source_id}/{dataset}/manifest_{month}.json"
    return _object_exists(client, bucket_name, key)


def _manifest_exists_snapshot(client, bucket_name: str, source_id: str, dataset: str, day: date) -> bool:
    key = f"bronze/raw/{source_id}/{dataset}/ingest_date={day.isoformat()}/manifest.json"
    return _object_exists(client, bucket_name, key)


# ── Audit + gap-fill logic ─────────────────────────────────────────────────────

def _audit_and_fill(**context) -> None:
    from ingestion.loaders.s3_client import build_s3_client
    from scripts.backfill.bulk import backfill_daily_window, backfill_monthly_window

    client = build_s3_client()
    bucket_name = get_bucket({})
    today = get_yesterday(context) + timedelta(days=1)   # wall-clock "today" for audit

    gap_report: list[str] = []
    fill_failures: list[str] = []

    # ── Daily sources ──────────────────────────────────────────────────────────
    for source_id, dataset in DAILY_SOURCES:
        missing_days: list[date] = []
        for offset in range(1, AUDIT_WINDOW_DAYS + 1):
            day = today - timedelta(days=offset)
            if not _manifest_exists_daily(client, bucket_name, source_id, dataset, day):
                missing_days.append(day)

        if not missing_days:
            logger.info("AUDIT OK  | %s/%s | all %d days present", source_id, dataset, AUDIT_WINDOW_DAYS)
            continue

        missing_days.sort()
        logger.warning("AUDIT GAP | %s/%s | missing %d day(s): %s", source_id, dataset, len(missing_days), missing_days)
        gap_report.append(f"{source_id}/{dataset}: missing days {missing_days}")

        # Fill gaps — call bulk directly (same code the ingest DAG uses)
        fill_start = missing_days[0]
        fill_end = missing_days[-1] + timedelta(days=1)
        logger.info("AUDIT FILL | %s/%s | filling [%s, %s)", source_id, dataset, fill_start, fill_end)
        try:
            results = backfill_daily_window(source_id, start=fill_start, end=fill_end, bucket=bucket_name)
            still_failed = [r for r in results if r.status == "failed"]
            if still_failed:
                msg = f"{source_id}/{dataset}: {len(still_failed)} day(s) still failed after fill"
                logger.error("AUDIT FILL FAILED | %s", msg)
                fill_failures.append(msg)
            else:
                logger.info("AUDIT FILL OK | %s/%s | %d day(s) recovered", source_id, dataset, len(missing_days))
        except Exception as exc:
            msg = f"{source_id}/{dataset}: fill raised {exc}"
            logger.error("AUDIT FILL ERROR | %s", msg)
            fill_failures.append(msg)

    # ── Monthly sources ────────────────────────────────────────────────────────
    # Collect unique (source_id, month_start) pairs to avoid duplicate fills
    checked_months: set[tuple[str, date]] = set()
    for source_id, dataset in MONTHLY_SOURCES:
        for offset in range(AUDIT_WINDOW_MONTHS):
            # Walk back month by month
            ref = date(today.year, today.month, 1) - timedelta(days=1)  # last day of prev month
            for _ in range(offset):
                ref = date(ref.year, ref.month, 1) - timedelta(days=1)
            month_start = date(ref.year, ref.month, 1)

            if not _manifest_exists_monthly(client, bucket_name, source_id, dataset, month_start):
                logger.warning("AUDIT GAP | %s/%s | missing month %s", source_id, dataset, month_start.strftime("%Y-%m"))
                gap_report.append(f"{source_id}/{dataset}: missing month {month_start.strftime('%Y-%m')}")

                key = (source_id, month_start)
                if key not in checked_months:
                    checked_months.add(key)
                    month_end = date(
                        month_start.year + (1 if month_start.month == 12 else 0),
                        month_start.month % 12 + 1,
                        1,
                    )
                    logger.info("AUDIT FILL | %s | filling month [%s, %s)", source_id, month_start, month_end)
                    try:
                        results = backfill_monthly_window(source_id, start=month_start, end=month_end, bucket=bucket_name)
                        still_failed = [r for r in results if r.status == "failed"]
                        if still_failed:
                            msg = f"{source_id} month {month_start.strftime('%Y-%m')}: fill failed"
                            logger.error("AUDIT FILL FAILED | %s", msg)
                            fill_failures.append(msg)
                        else:
                            logger.info("AUDIT FILL OK | %s | month %s recovered", source_id, month_start.strftime("%Y-%m"))
                    except Exception as exc:
                        msg = f"{source_id} month {month_start.strftime('%Y-%m')}: fill raised {exc}"
                        logger.error("AUDIT FILL ERROR | %s", msg)
                        fill_failures.append(msg)
            else:
                logger.info("AUDIT OK  | %s/%s | month %s present", source_id, dataset, month_start.strftime("%Y-%m"))

    # ── Snapshot sources: report only, never fill ──────────────────────────────
    #
    # Everything above this point re-runs the ingest to close a gap. That is
    # exactly what must NOT happen here: the upstream holds only its current
    # state, so "filling" a past day would write today's data under yesterday's
    # partition and quietly fabricate history. A missing day is permanent; the
    # audit's job is to make sure it is at least known.
    for source_id, dataset in SNAPSHOT_SOURCES:
        missing_snapshots: list[date] = []
        for offset in range(1, AUDIT_WINDOW_DAYS + 1):
            day = today - timedelta(days=offset)
            if not _manifest_exists_snapshot(client, bucket_name, source_id, dataset, day):
                missing_snapshots.append(day)
        if not missing_snapshots:
            logger.info(
                "AUDIT OK  | %s/%s | all %d snapshot day(s) present",
                source_id, dataset, AUDIT_WINDOW_DAYS,
            )
            continue

        missing_snapshots.sort()
        logger.error(
            "AUDIT GAP | %s/%s | %d snapshot day(s) missing and UNRECOVERABLE: %s "
            "— check the collection timer on the storage node "
            "(docs/guide/snapshot-collection.md)",
            source_id, dataset, len(missing_snapshots), missing_snapshots,
        )
        gap_report.append(
            f"{source_id}/{dataset}: {len(missing_snapshots)} snapshot day(s) "
            f"missing, unrecoverable: {missing_snapshots}",
        )

    # ── Final report ───────────────────────────────────────────────────────────
    if not gap_report:
        logger.info("AUDIT REPORT | all sources clean — no gaps found in audit window")
        return

    logger.warning("AUDIT REPORT | %d gap(s) found, %d fill failure(s)", len(gap_report), len(fill_failures))
    for line in gap_report:
        logger.warning("  GAP: %s", line)

    if fill_failures:
        for line in fill_failures:
            logger.error("  FILL FAILED: %s", line)
        raise RuntimeError(
            f"Bronze audit: {len(fill_failures)} gap(s) could not be filled. "
            "Check logs above. Airflow will retry."
        )


with DAG(
    dag_id="dag_audit_bronze",
    description="Daily Bronze audit: scan Bronze manifests for gaps and auto-fill missing partitions",
    default_args=DEFAULT_ARGS,
    schedule="0 8 * * *",
    catchup=False,
    max_active_runs=1,
    tags=["audit", "bronze", "data-quality", "daily"],
) as dag:

    audit_and_fill = PythonOperator(
        task_id="audit_and_fill",
        python_callable=_audit_and_fill,
    )
