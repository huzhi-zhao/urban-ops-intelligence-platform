"""
Daily snapshot collection entry point — runs on the storage node, not Airflow.

Why not a DAG: this is the one ingestion task that cannot be replayed, and
Airflow runs on the rebuildable compute node. Putting it beside the storage it
writes to means the collection survives anything done to the compute node
(ADR 0006 §2.2). The cost is that it must carry its own alerting, which it does
— see ``ingestion/snapshot/notify.py``.

Usage:
    python -m scripts.collect_snapshot --source SRC-WPG-SNOW
    python -m scripts.collect_snapshot --source SRC-WPG-SNOW --dry-run

Exit codes:
    0  every dataset collected
    1  configuration/usage error (nothing attempted)
    2  at least one dataset failed to collect

Deployment: a systemd timer or cron entry on the storage node. Environment
(S3_*, SOCRATA_APP_TOKEN, SNAPSHOT_*) comes from the unit's EnvironmentFile —
cron runs with an almost empty environment, so relying on an interactive shell's
exports is the standard way for this to silently stop working.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from ingestion.config import ConfigLoadError, load_source_config
from ingestion.snapshot import SnapshotCollectionError, SnapshotCollector
from ingestion.snapshot.collector import DEFAULT_MIN_RECORDS
from ingestion.snapshot.fetch import fetch_snapshot_records
from ingestion.snapshot.notify import notify_failure, ping_watchdog

logger = logging.getLogger("collect_snapshot")

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_COLLECTION_FAILED = 2


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--source", required=True,
        help="Source ID with partition_strategy=snapshot (e.g. SRC-WPG-SNOW)",
    )
    parser.add_argument(
        "--bucket", default=None,
        help="Object-storage bucket. Defaults to the S3_BUCKET_NAME env var.",
    )
    parser.add_argument(
        "--ingest-date", default=None, type=date.fromisoformat,
        help="Partition to write (YYYY-MM-DD, default today). Labels the "
             "collection; it cannot retrieve a past day.",
    )
    parser.add_argument(
        "--min-records", type=int, default=DEFAULT_MIN_RECORDS,
        help=f"Refuse to write a pull smaller than this (default "
             f"{DEFAULT_MIN_RECORDS}); guards against a silent upstream failure "
             f"overwriting the day with an empty partition.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Count the records the upstream would return; write nothing.",
    )
    return parser.parse_args(argv)


def _resolve_bucket(explicit: str | None) -> str:
    import os

    bucket = (explicit or os.environ.get("S3_BUCKET_NAME") or "").strip()
    if not bucket:
        raise SystemExit(
            "Error: bucket is required. Pass --bucket or set S3_BUCKET_NAME.",
        )
    return bucket


def _dry_run(source_id: str) -> int:
    cfg = load_source_config(source_id)
    for ds in cfg.datasets:
        count = sum(1 for _ in fetch_snapshot_records(ds))
        logger.info("DRY-RUN %s/%s: %d records upstream", source_id, ds.name, count)
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    args = parse_args(argv)

    try:
        if args.dry_run:
            return _dry_run(args.source)
        collector = SnapshotCollector.for_source(
            args.source,
            bucket=_resolve_bucket(args.bucket),
            min_records=args.min_records,
        )
    except (ConfigLoadError, ValueError) as e:
        # Nothing was attempted, so this is a usage error rather than a lost
        # day — the operator is watching, and no alert is warranted.
        logger.error("%s", e)
        return EXIT_USAGE

    try:
        results = collector.collect(ingest_date=args.ingest_date)
    except SnapshotCollectionError as e:
        logger.error("%s", e)
        notify_failure(args.source, str(e))
        return EXIT_COLLECTION_FAILED
    except Exception as e:  # noqa: BLE001 — the alert matters more than the traceback
        logger.exception("Unexpected failure collecting %s", args.source)
        notify_failure(args.source, f"{type(e).__name__}: {e}")
        return EXIT_COLLECTION_FAILED

    total = sum(r.record_count for r in results)
    logger.info(
        "%s: %d dataset(s) collected, %d records total",
        args.source, len(results), total,
    )
    # Only after a real success — a check-in on a failed run would tell the
    # watchdog the exact opposite of the truth.
    ping_watchdog(args.source)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
