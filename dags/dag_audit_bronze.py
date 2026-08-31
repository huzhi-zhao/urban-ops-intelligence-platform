"""
Daily Bronze audit DAG — scans Bronze manifests and auto-fills any gaps.

Schedule        : 08:00 UTC every day (2 hours after ingest DAGs finish)
Catchup         : disabled — audit always looks at a fixed rolling window, not history
max_active_runs : 1

What it does
────────────
1. For each source with partition_strategy=daily: check every date in the last
   AUDIT_WINDOW_DAYS days has a manifest object in Bronze.
2. For each source with partition_strategy=monthly: check every month in the
   last AUDIT_WINDOW_MONTHS months has a manifest file.
3. Any gaps found → call the corresponding bulk function directly to fill them.
   Snapshot sources (BO-7) are the exception: they are checked and reported but
   never filled, because their upstream keeps no history — re-running would file
   today's data under a past date and fabricate history rather than recover it.
4. Logs a structured audit report at the end. If any gap could not be filled,
   the task raises so Airflow marks the Run as failed (and retries).
5. A second task then audits Bronze *content* over the same window — checks B
   (PK unique inside each shard) and C (row count vs the upstream's own count).
   Existence checking cannot see either failure: the pagination incident's 34
   corrupted shards all read healthy under step 1. It reports and never repairs
   — Bronze is immutable, so the output is a re-pull list for the CLI.

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
from airflow.models.param import Param
from airflow.operators.python import PythonOperator

logger = logging.getLogger(__name__)

# How far back the audit looks
AUDIT_WINDOW_DAYS = 14       # for daily sources: check last 14 days
AUDIT_WINDOW_MONTHS = 3      # for monthly sources: check last 3 months

# Mirrors scripts.profiling.bronze_integrity_audit.DEFAULT_LATE_ARRIVAL_DAYS,
# which is the authority. It is duplicated rather than imported because a Param
# default is needed at DAG *parse* time, while this file imports everything else
# at task run time on purpose: a parse-time import means one broken module stops
# the scheduler parsing this DAG at all, instead of failing one task with a
# readable message. `test_param_default_mirrors_the_audit_module` fails if the
# two numbers ever drift.
DEFAULT_LATE_ARRIVAL_DAYS = 7

# Which sources to audit is derived from the registry, not listed here.
#
# The previous version hardcoded three (source_id, dataset) tables. That put
# instance-specific literals in a generic scheduling file (CLAUDE.md guardrail
# §1) and, more practically, meant every new source had to be remembered in two
# places — this DAG and the profiler — with the failure mode being a source that
# is ingested but silently never audited.
#
# `static` sources are excluded: they have no time dimension, so there is no
# such thing as a missing day or month for them.


def _audit_targets(strategy: str) -> list[tuple[str, str]]:
    """The (source_id, dataset_name) pairs to audit for one strategy.

    The derivation itself lives in ``ingestion.config`` so it is unit-testable
    without apache-airflow installed; this is a thin call so the DAG file keeps
    to scheduling only. Imported at task run time rather than DAG parse time so
    a config error fails one task with a readable message instead of breaking
    DAG parsing for the whole scheduler.
    """
    from ingestion.config import load_datasets_by_strategy

    return load_datasets_by_strategy(strategy)


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
    for source_id, dataset in _audit_targets("daily"):
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
    for source_id, dataset in _audit_targets("monthly"):
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
    for source_id, dataset in _audit_targets("snapshot"):
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


# ── Content integrity audit (checks B + C) ─────────────────────────────────────

def _audit_integrity(**context) -> None:
    """Report-only content audit. Never writes, never repairs, never raises on
    a finding.

    🔴 **A dirty shard does not fail this task.** Bronze is immutable, so there
    is no action Airflow could take; the actionable output is the re-pull list,
    and re-pulling goes through the CLI under a human. Making the task fail
    would produce a red DAG every day until someone did that by hand, and a
    permanently red DAG is a muted DAG.

    What *does* raise is a check that could not run at all (object storage
    unreachable, upstream refusing every request) — that is the audit itself
    being broken, which is worth a retry and an alert.
    """
    from scripts.profiling.bronze_integrity_audit import (
        DEFAULT_LATE_ARRIVAL_DAYS,
        audit_full,
        audit_window,
        format_report,
    )

    params = context.get("params") or {}
    bucket_name = get_bucket(params)
    today = get_yesterday(context) + timedelta(days=1)
    late_arrival_days = int(params.get("late_arrival_days") or DEFAULT_LATE_ARRIVAL_DAYS)

    if params.get("full_sweep"):
        logger.warning(
            "INTEGRITY | FULL SWEEP requested — minutes of scanning and "
            "thousands of upstream requests. This is the after-a-backfill entry "
            "point, not a daily one."
        )
        results = audit_full(bucket_name, late_arrival_days=late_arrival_days)
    else:
        results = audit_window(
            bucket_name, today=today,
            window_days=AUDIT_WINDOW_DAYS,
            late_arrival_days=late_arrival_days,
        )

    logger.info("INTEGRITY REPORT | %d dataset(s)\n%s", len(results), format_report(results))

    broken = [r for r in results if r.errors]
    if broken:
        detail = "; ".join(f"{r.source_id}/{r.dataset}: {r.errors}" for r in broken)
        raise RuntimeError(
            f"Bronze integrity audit could not complete for {len(broken)} "
            f"dataset(s): {detail}"
        )

    unclean = [r for r in results if not r.is_clean]
    if not unclean:
        logger.info("INTEGRITY REPORT | all datasets clean")
        return

    # Loud, but not a failure. See the docstring.
    for r in unclean:
        logger.error(
            "INTEGRITY | %s/%s needs a re-pull of %d day(s): %s",
            r.source_id, r.dataset, len(r.refetch_days), " ".join(r.refetch_days),
        )
    logger.error(
        "INTEGRITY | re-pull with: python -m scripts.backfill.main "
        "--source <id> --start <day> --end <day+1> --bucket %s  "
        "(Bronze is immutable — this task does not repair)",
        bucket_name,
    )


with DAG(
    dag_id="dag_audit_bronze",
    description="Daily Bronze audit: scan Bronze manifests for gaps and auto-fill missing partitions",
    default_args=DEFAULT_ARGS,
    schedule="0 8 * * *",
    catchup=False,
    max_active_runs=1,
    tags=["audit", "bronze", "data-quality", "daily"],
    params={
        "bucket": Param(
            "", type="string",
            description="Object-storage bucket. Blank falls back to S3_BUCKET_NAME.",
        ),
        "full_sweep": Param(
            False, type="boolean",
            description=(
                "Audit ALL of Bronze instead of the rolling window. Minutes of "
                "scanning plus thousands of upstream requests — trigger it by "
                "hand after a backfill and before Silver, never on a schedule. "
                "Scheduled runs always leave this false."
            ),
        ),
        "late_arrival_days": Param(
            DEFAULT_LATE_ARRIVAL_DAYS, type="integer", minimum=0,
            description=(
                "Days back from today exempt from the upstream row-count check. "
                "Recent days legitimately grow after collection (late-arriving "
                "facts); counting that as a dropped row builds an alert that "
                "fires every day and then gets muted."
            ),
        ),
    },
) as dag:

    audit_and_fill = PythonOperator(
        task_id="audit_and_fill",
        python_callable=_audit_and_fill,
    )

    audit_integrity = PythonOperator(
        task_id="audit_integrity",
        python_callable=_audit_integrity,
    )

    # Existence first: a day filled by the task above should be checked for
    # content in the same run, not tomorrow.
    audit_and_fill >> audit_integrity
