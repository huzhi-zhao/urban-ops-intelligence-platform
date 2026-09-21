"""Bronze content integrity audit — checks B and C, over a rolling window.

``dag_audit_bronze`` today answers *is the partition there*. That is check A,
and it is blind to the failure this module exists for: unordered limit/offset
paging repeated some rows and dropped others, changing neither the partition's
existence nor its manifest checksum (the manifest describes the payload we
actually wrote, short rows and all). All 34 corrupted shards read healthy under
check A. See docs/dev/postmortem/bronze-socrata-pagination-incident.md.

    A  existence      partition / manifest present      (already in the DAG)
    B  uniqueness     PK unique inside each shard       finds repeats
    C  reconciliation Bronze rows vs upstream count(*)  finds DROPS

🔴 **B and C are not alternatives.** A page-boundary slip produces one repeat
*and* one drop, which cancel out in the row count — so C alone can call a
corrupted day clean, and B alone never sees the drop at all.

This module orchestrates; the measuring is done by ``bronze_duplicate_scan``
and ``bronze_rowcount_reconcile``, unchanged, so the DAG and the CLI report the
same numbers by construction rather than by two implementations agreeing.

What it deliberately does NOT do
────────────────────────────────
- **Never writes.** Bronze is immutable (AGENTS.md); the output is a re-pull
  list and re-pulling goes through the CLI.
- **Never de-duplicates.** That would hide the upstream-change signal.
- **Never guesses a primary key.** A dataset without ``primary_key`` in its
  source YAML skips check B rather than assuming one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from ingestion.config import ApiType, load_all_sources

logger = logging.getLogger(__name__)

# Check C costs one HTTP request per day audited; check B decompresses every
# shard in the window. Both are cheap over a fortnight and neither is cheap over
# 4,878 shards, which is why the full sweep is a separate manual entry point
# (see ``audit_full``) rather than a bigger number here.
DEFAULT_WINDOW_DAYS = 14

# Days back from today exempt from check C. 311 carries late-arriving facts, so
# the upstream's count for recent days legitimately grows after collection —
# that is why the ingest DAG runs a 7-day lookback at all. Counting that growth
# as a dropped row builds an alert that fires daily and then gets muted.
# 🔴 The tolerance applies ONLY to recent days: a 2019 shard that is short is short.
DEFAULT_LATE_ARRIVAL_DAYS = 7


@dataclass
class DatasetAudit:
    """One dataset's outcome under both checks."""

    source_id: str
    dataset: str
    checks_run: list[str] = field(default_factory=list)
    checks_skipped: dict[str, str] = field(default_factory=dict)
    dirty_shards: list[str] = field(default_factory=list)
    days_missing_rows: list[str] = field(default_factory=list)
    days_excess_rows: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def refetch_days(self) -> list[str]:
        """Days needing a re-pull — the audit's only actionable output.

        Both directions belong here: an excess row and a missing row are two
        halves of the same page-boundary slip, so a day flagged either way gets
        the same treatment.
        """
        days = set(self.days_missing_rows) | set(self.days_excess_rows)
        days |= {d for d in (_day_of(k) for k in self.dirty_shards) if d}
        return sorted(days)

    @property
    def is_clean(self) -> bool:
        return not self.refetch_days and not self.errors


def _day_of(blob_key: str) -> str | None:
    """The YYYY-MM-DD a shard key encodes, if it encodes one."""
    from scripts.profiling.bronze_duplicate_scan import DAY_RE

    match = DAY_RE.search(blob_key)
    return match.group(1) if match else None


def _window_days(end: date, window_days: int) -> set[str]:
    """The ``window_days`` days ending the day before ``end``.

    Today is excluded: its ingest has not necessarily run yet, and auditing a
    partition that was never meant to exist yet reports a gap every morning.
    """
    return {
        (end - timedelta(days=offset)).isoformat()
        for offset in range(1, window_days + 1)
    }


def integrity_targets() -> list[tuple[str, str]]:
    """(source_id, dataset) pairs eligible for content integrity checks.

    Derived from the registry, like the DAG's existence audit, so a new source
    is covered without editing this file. Only ``daily`` Socrata datasets
    qualify, and both halves of that are load-bearing:

    - **daily**: checks B and C are day-shaped. ``static`` has no time
      dimension; ``monthly`` shards do not map to an upstream day count.
    - **socrata**: check C needs a ``$select=count(*)`` endpoint. Open-Meteo
      has none, so a weather dataset would fail rather than skip.

    🔴 ``snapshot`` is excluded on purpose and not merely by falling through
    the strategy filter: its upstream keeps no history, so there is nothing to
    reconcile against and no possible repair. Existence checking (A) already
    covers it, report-only, and that stays the whole story.
    """
    targets: list[tuple[str, str]] = []
    for cfg in load_all_sources().values():
        for ds in cfg.datasets:
            if cfg.strategy_for(ds) != "daily":
                continue
            if ds.api_type != ApiType.SOCRATA:
                continue
            targets.append((cfg.source.id, ds.name))
    return targets


def audit_dataset(
    bucket: str,
    source_id: str,
    dataset: str,
    only_days: set[str] | None,
    late_arrival_days: int = DEFAULT_LATE_ARRIVAL_DAYS,
) -> DatasetAudit:
    """Run checks B and C on one dataset, restricted to ``only_days``.

    ``only_days=None`` means the whole history — minutes for B, thousands of
    requests for C. Callers on a schedule always pass a window.

    A failure in one check does not skip the other: they find different things,
    and the run that most needs both is the one where something is already
    wrong.
    """
    from scripts.profiling.bronze_duplicate_scan import run_scan
    from scripts.profiling.bronze_duplicate_scan import summarize as summarize_scan
    from scripts.profiling.bronze_rowcount_reconcile import run_reconcile
    from scripts.profiling.bronze_rowcount_reconcile import (
        summarize as summarize_reconcile,
    )

    cfg = _dataset_config(source_id, dataset)
    result = DatasetAudit(source_id=source_id, dataset=dataset)

    # ── Check B: PK uniqueness inside each shard ──────────────────────────────
    if not cfg.primary_key:
        result.checks_skipped["B"] = (
            "no primary_key declared in config/sources — the audit does not "
            "guess one"
        )
        logger.info("AUDIT-B SKIP | %s/%s | no primary_key declared", source_id, dataset)
    else:
        try:
            reports = run_scan(
                bucket=bucket,
                source_id=source_id,
                dataset=dataset,
                key_fields=tuple(cfg.primary_key),
                only_days=only_days,
            )
            summary = summarize_scan(reports)
            result.checks_run.append("B")
            result.dirty_shards = [s["key"] for s in summary["dirty_shards"]]
            if result.dirty_shards:
                logger.error(
                    "AUDIT-B DIRTY | %s/%s | %d shard(s), %d excess row(s)",
                    source_id, dataset,
                    summary["shards_dirty"], summary["excess_rows_total"],
                )
            else:
                logger.info(
                    "AUDIT-B OK | %s/%s | %d shard(s) scanned, %d multi-page",
                    source_id, dataset,
                    summary["shards_scanned"], summary["shards_multipage"],
                )
        except Exception as exc:  # noqa: BLE001 — reported, never fatal
            result.errors.append(f"check B raised: {exc}")
            logger.exception("AUDIT-B ERROR | %s/%s", source_id, dataset)

    # ── Check C: Bronze row count vs the upstream's own count ─────────────────
    try:
        recs = run_reconcile(
            bucket=bucket,
            source_id=source_id,
            dataset=dataset,
            late_arrival_days=late_arrival_days,
            only_days=only_days,
        )
        summary = summarize_reconcile(recs)
        result.checks_run.append("C")
        result.days_missing_rows = [
            r.day for r in recs if r.verdict == "MISSING_ROWS"
        ]
        result.days_excess_rows = [r.day for r in recs if r.verdict == "excess"]
        if result.days_missing_rows or result.days_excess_rows:
            logger.error(
                "AUDIT-C MISMATCH | %s/%s | %d day(s) short (%d rows), "
                "%d day(s) over (%d rows)",
                source_id, dataset,
                summary["days_with_missing_rows"], summary["rows_missing_total"],
                summary["days_with_excess_rows"], summary["rows_excess_total"],
            )
        else:
            logger.info(
                "AUDIT-C OK | %s/%s | %d day(s) reconciled, %d tolerated as late arrival",
                source_id, dataset,
                summary["days_checked"], summary["days_tolerated_as_late_arrival"],
            )
    except Exception as exc:  # noqa: BLE001 — reported, never fatal
        result.errors.append(f"check C raised: {exc}")
        logger.exception("AUDIT-C ERROR | %s/%s", source_id, dataset)

    return result


def _dataset_config(source_id: str, dataset: str):
    from ingestion.config import load_source_config

    cfg = load_source_config(source_id)
    ds = next((d for d in cfg.datasets if d.name == dataset), None)
    if ds is None:
        raise ValueError(f"dataset {dataset!r} not found in {source_id}")
    return ds


def audit_window(
    bucket: str,
    today: date,
    window_days: int = DEFAULT_WINDOW_DAYS,
    late_arrival_days: int = DEFAULT_LATE_ARRIVAL_DAYS,
) -> list[DatasetAudit]:
    """Audit every eligible dataset over the trailing ``window_days`` days."""
    only_days = _window_days(today, window_days)
    return [
        audit_dataset(bucket, sid, ds, only_days, late_arrival_days)
        for sid, ds in integrity_targets()
    ]


def audit_full(
    bucket: str,
    late_arrival_days: int = DEFAULT_LATE_ARRIVAL_DAYS,
) -> list[DatasetAudit]:
    """Audit the whole of Bronze. Manual entry point only.

    Minutes for check B and thousands of upstream requests for check C. Run it
    after a backfill and before Silver, not on a schedule.
    """
    return [
        audit_dataset(bucket, sid, ds, None, late_arrival_days)
        for sid, ds in integrity_targets()
    ]


def format_report(results: list[DatasetAudit]) -> str:
    """A human-readable summary — the re-pull list is the point of it."""
    if not results:
        return "no datasets eligible for content integrity checks"

    lines: list[str] = []
    for r in results:
        skipped = "".join(f" [B skipped: {v}]" for v in r.checks_skipped.values())
        if r.is_clean:
            lines.append(
                f"  OK   {r.source_id}/{r.dataset}: "
                f"checks {'+'.join(r.checks_run) or 'none'} clean{skipped}"
            )
            continue
        lines.append(
            f"  FAIL {r.source_id}/{r.dataset}: "
            f"{len(r.dirty_shards)} dirty shard(s), "
            f"{len(r.days_missing_rows)} day(s) short, "
            f"{len(r.days_excess_rows)} day(s) over{skipped}"
        )
        for err in r.errors:
            lines.append(f"       ERROR: {err}")
        if r.refetch_days:
            lines.append(f"       re-pull: {' '.join(r.refetch_days)}")
    return "\n".join(lines)


def main() -> None:
    """CLI mirror of the DAG task, for the manual full sweep and for debugging.

    Exit codes match the two probes: 0 clean, 2 findings, 1 the audit itself
    failed. A finding is exit 2 rather than 1 because it is a result, not an
    error — the caller decides what to do with the re-pull list.
    """
    import argparse
    import os

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bucket", default=os.environ.get("S3_BUCKET_NAME", ""),
        help="Defaults to $S3_BUCKET_NAME.",
    )
    parser.add_argument(
        "--window-days", type=int, default=DEFAULT_WINDOW_DAYS,
        help="Trailing days to audit. Ignored with --full.",
    )
    parser.add_argument(
        "--full", action="store_true",
        help="Audit all of Bronze. Minutes of scanning, thousands of requests.",
    )
    parser.add_argument(
        "--late-arrival-days", type=int, default=DEFAULT_LATE_ARRIVAL_DAYS,
        help="Recent days exempt from the upstream row-count check.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not args.bucket:
        raise SystemExit("--bucket is required (or set S3_BUCKET_NAME)")

    if args.full:
        results = audit_full(args.bucket, late_arrival_days=args.late_arrival_days)
    else:
        results = audit_window(
            args.bucket, today=date.today(),
            window_days=args.window_days,
            late_arrival_days=args.late_arrival_days,
        )

    print(format_report(results))

    if any(r.errors for r in results):
        raise SystemExit(1)
    raise SystemExit(2 if any(not r.is_clean for r in results) else 0)


if __name__ == "__main__":
    main()
