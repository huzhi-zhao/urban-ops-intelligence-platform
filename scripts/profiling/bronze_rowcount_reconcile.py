"""Reconcile Bronze shard row counts against the upstream's own count.

The companion to ``bronze_duplicate_scan.py``, and the only one of the two that
can see **dropped** rows. Unordered limit/offset paging (fixed 2026-08-18) could
repeat or drop rows at a page boundary; a repeat leaves a duplicate key behind,
a drop leaves nothing at all — no count mismatch inside the shard, no checksum
anomaly, since the manifest describes the payload we actually wrote. Asking the
upstream how many rows that day should have is the only way to find them.

    Bronze − upstream  > 0   rows were fetched twice
                       = 0   the day is clean
                       < 0   rows were dropped   ← invisible to any local check

One ``$select=count(*)`` request per day: no records are transferred.

Usage:
    python -m scripts.profiling.bronze_rowcount_reconcile \\
        --bucket uoip --source SRC-WPG-311 --dataset service_requests \\
        [--late-arrival-days 7] > var/bronze-reconcile.json

Exit codes: 0 = every day reconciles, 2 = mismatches found, 1 = the run failed.
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any

from ingestion.clients.socrata_client import SocrataClient
from ingestion.config.loader import load_source_config
from ingestion.loaders.s3_client import build_s3_client

logger = logging.getLogger(__name__)

SHARD_RE = re.compile(r"/data_(\d{4}-\d{2}-\d{2})\.ndjson(?:\.gz)?$")

# Recent days legitimately grow after collection: 311 carries late-arriving
# facts, which is why the ingest DAG runs a 7-day lookback at all. Counting that
# growth as a dropped row would produce an alert that fires every single day and
# then gets muted — the failure mode CLAUDE.md's "denominator matters" note
# warns about. The tolerance applies ONLY to recent days; a 2019 shard that is
# short is short.
DEFAULT_LATE_ARRIVAL_DAYS = 7


@dataclass
class DayReconciliation:
    """One day's Bronze count vs the upstream's."""

    day: str
    bronze_rows: int
    upstream_rows: int
    delta: int
    within_late_arrival_window: bool

    @property
    def verdict(self) -> str:
        if self.delta == 0:
            return "clean"
        if self.within_late_arrival_window:
            return "late_arrival_tolerated"
        return "excess" if self.delta > 0 else "MISSING_ROWS"


def bronze_row_counts(client: Any, bucket: str, source_id: str, dataset: str) -> dict[str, int]:
    """Row count per day, read from the Bronze shards themselves."""
    counts: dict[str, int] = {}
    paginator = client.get_paginator("list_objects_v2")
    prefix = f"bronze/raw/{source_id}/{dataset}/"
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            m = SHARD_RE.search(obj["Key"])
            if not m:
                continue
            raw = client.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read()
            text = gzip.decompress(raw) if obj["Key"].endswith(".gz") else raw
            counts[m.group(1)] = len(text.decode().splitlines())
    return counts


def upstream_count(client: SocrataClient, timestamp_field: str, day: date) -> int:
    """Ask the upstream how many rows fall in ``[day, day+1)``.

    Goes through SocrataClient rather than a fresh HTTP call so the retry and
    rate-limit backoff behaviour is the one the ingestion path already uses.
    """
    start = datetime.combine(day, datetime.min.time())
    end = start + timedelta(days=1)
    params = client._build_incremental_params(timestamp_field, start, end)
    params["$select"] = "count(*)"
    rows = client.fetch_page(limit=1, offset=0, extra_params=params)
    if not rows:
        return 0
    # Socrata names the column "count" on some domains and "count_*" on others.
    return int(next(iter(rows[0].values())))


def run_reconcile(
    bucket: str, source_id: str, dataset: str, late_arrival_days: int
) -> list[DayReconciliation]:
    cfg = load_source_config(source_id)
    ds = next((d for d in cfg.datasets if d.name == dataset), None)
    if ds is None:
        raise SystemExit(f"dataset {dataset!r} not found in {source_id}")
    if not ds.timestamp_field:
        raise SystemExit(
            f"dataset {dataset!r} has no timestamp_field; a day-by-day "
            "reconciliation has no window to ask the upstream about."
        )

    socrata = SocrataClient(
        resource_id=ds.resource_id,
        domain=ds.domain,
        app_token=os.environ.get("SOCRATA_APP_TOKEN") or None,
    )
    cutoff = date.today() - timedelta(days=late_arrival_days)

    results: list[DayReconciliation] = []
    for day_str, bronze_rows in sorted(bronze_row_counts(
        build_s3_client(), bucket, source_id, dataset
    ).items()):
        day = date.fromisoformat(day_str)
        upstream = upstream_count(socrata, ds.timestamp_field, day)
        rec = DayReconciliation(
            day=day_str,
            bronze_rows=bronze_rows,
            upstream_rows=upstream,
            delta=bronze_rows - upstream,
            within_late_arrival_window=day >= cutoff,
        )
        results.append(rec)
        if rec.verdict == "MISSING_ROWS":
            logger.warning("%s: bronze=%d upstream=%d delta=%d  ← ROWS MISSING",
                           rec.day, rec.bronze_rows, rec.upstream_rows, rec.delta)
        elif rec.verdict == "excess":
            logger.warning("%s: bronze=%d upstream=%d delta=+%d (duplicates)",
                           rec.day, rec.bronze_rows, rec.upstream_rows, rec.delta)
        else:
            logger.info("%s: %d rows, %s", rec.day, rec.bronze_rows, rec.verdict)
    return results


def summarize(results: list[DayReconciliation]) -> dict[str, Any]:
    missing = [r for r in results if r.verdict == "MISSING_ROWS"]
    excess = [r for r in results if r.verdict == "excess"]
    return {
        "days_checked": len(results),
        "days_clean": sum(1 for r in results if r.verdict == "clean"),
        "days_with_missing_rows": len(missing),
        "days_with_excess_rows": len(excess),
        "rows_missing_total": -sum(r.delta for r in missing),
        "rows_excess_total": sum(r.delta for r in excess),
        "days_tolerated_as_late_arrival": sum(
            1 for r in results if r.verdict == "late_arrival_tolerated"
        ),
        # This list IS the re-pull list — both directions, nothing else.
        "refetch_days": sorted(r.day for r in missing + excess),
        "mismatches": [asdict(r) for r in missing + excess],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--source", required=True, help="e.g. SRC-WPG-311")
    parser.add_argument("--dataset", required=True, help="e.g. service_requests")
    parser.add_argument(
        "--late-arrival-days", type=int, default=DEFAULT_LATE_ARRIVAL_DAYS,
        help="Days back from today exempt from the check (late-arriving facts).",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    results = run_reconcile(args.bucket, args.source, args.dataset, args.late_arrival_days)
    summary = summarize(results)
    print(json.dumps(summary, indent=2))
    sys.exit(2 if summary["refetch_days"] else 0)


if __name__ == "__main__":
    main()
