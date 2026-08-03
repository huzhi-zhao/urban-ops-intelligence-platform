"""
Shared CLI helpers for per-source backfill scripts.

Per-source scripts accept a ``[start, end)`` window and delegate to
``scripts.backfill.bulk``. The bulk layer splits the window into
day-sized or month-sized chunks and calls the atomic ``BackfillFacade``
methods.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime

from ingestion.config import load_source_config

logger = logging.getLogger(__name__)


def parse_date(s: str) -> date:
    """Parse ``YYYY-MM-DD`` into a :class:`date`. Used as argparse ``type=``."""
    return datetime.strptime(s, "%Y-%m-%d").date()


def parse_args(description: str) -> argparse.Namespace:
    """Standard backfill CLI flags shared by every per-source script.

    Every per-source script accepts ``[--start, --end)`` and delegates
    the day/month splitting to ``scripts.backfill.bulk``. Static
    ``static`` sources ignore ``--start`` / ``--end``.
    """
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--start", required=True, type=parse_date,
        help="Start date (inclusive), format YYYY-MM-DD",
    )
    parser.add_argument(
        "--end", required=True, type=parse_date,
        help="End date (exclusive), format YYYY-MM-DD",
    )
    parser.add_argument(
        "--action", choices=["upload", "fetch"], default="upload",
        help="upload (write to object storage) or fetch (return data, do not write)",
    )
    parser.add_argument(
        "--bucket", default=None,
        help="Object-storage bucket name. Defaults to env S3_BUCKET_NAME. "
             "Required for --action upload.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Alias for --action fetch; logs record counts without writing",
    )
    parser.add_argument(
        "--max-workers", type=int, default=None,
        help="Thread-pool size for parallel day/month backfill (default: "
             "4 for daily, 2 for monthly). 1 = serial.",
    )
    return parser.parse_args()


def require_bucket(args: argparse.Namespace) -> str:
    """Resolve the bucket from ``--bucket`` or the ``S3_BUCKET_NAME`` env.

    Exits with code 1 and a clear message if neither is set, matching the
    pre-refactor behavior of the per-source ``load_config`` helpers.
    """
    bucket = args.bucket or _env_bucket()
    if not bucket:
        print(
            "Error: object-storage bucket is required for upload. "
            "Set --bucket or the S3_BUCKET_NAME env var.",
            file=sys.stderr,
        )
        sys.exit(1)
    return bucket


def _env_bucket() -> str:
    import os
    return os.environ.get("S3_BUCKET_NAME", "").strip()


def default_max_workers(partition_strategy: str) -> int:
    """Default thread-pool size per partition strategy.

    Socrata has per-token rate limits, so 4 is a safe default for daily.
    A multi-dataset source shares one token across them → 2 is safer.
    """
    return {"daily": 4, "monthly": 2, "static": 1, "snapshot": 1}.get(
        partition_strategy, 4,
    )


# ── Dispatch tables: strategy → bulk function ────────────────────────────────
#
# Per-source scripts use these to pick the right bulk helper for their
# source's strategy. Adding a strategy = adding one row to each table.


from scripts.backfill.bulk import (  # noqa: E402  — local import to avoid cycles
    backfill_daily_window,
    backfill_monthly_window,
    backfill_snapshot,
    backfill_static,
    fetch_daily_window,
    fetch_monthly_window,
    fetch_snapshot,
    fetch_static,
)

UPLOAD_DISPATCH = {
    "daily": backfill_daily_window,
    "monthly": backfill_monthly_window,
    "static": backfill_static,
    "snapshot": backfill_snapshot,
}

FETCH_DISPATCH = {
    "daily": fetch_daily_window,
    "monthly": fetch_monthly_window,
    "static": fetch_static,
    "snapshot": fetch_snapshot,
}

# Strategies whose bulk helpers take a [start, end) window. The other two
# ('static', 'snapshot') fetch the upstream's current state in one shot and
# accept no time arguments at all — passing them would be a TypeError, not a
# no-op, which is why this is a set rather than a conditional on args.
_WINDOWED_STRATEGIES = frozenset({"daily", "monthly"})


def run_standard_backfill(source_id: str, args: argparse.Namespace) -> None:
    """Run one source's backfill: pick the bulk helper, log, exit non-zero on failure.

    Every per-source ``backfill_*.py`` script delegates its whole body here.
    Those scripts are pure dispatch by design (.claude/rules/backfill.md) — the
    only thing that legitimately differs between them is the source id, so
    duplicating this logic per source only creates places for them to drift.

    Args:
        source_id: Registry key, as declared in ``config/sources/*.yaml``.
        args: Parsed CLI namespace from :func:`parse_args`.

    Raises:
        SystemExit: code 2 if any slice failed, so a shell loop over several
            windows stops instead of reporting success on a partial backfill.
    """
    cfg = load_source_config(source_id)
    strategy = cfg.source.partition_strategy

    kwargs: dict = {}
    if strategy in _WINDOWED_STRATEGIES:
        kwargs = {
            "start": args.start,
            "end": args.end,
            "max_workers": args.max_workers or default_max_workers(strategy),
        }
    else:
        logger.info(
            "%s is a %r source: --start=%s --end=%s are accepted for CLI "
            "uniformity and ignored — only the upstream's current state exists",
            source_id, strategy, args.start, args.end,
        )

    if args.dry_run or args.action == "fetch":
        results = FETCH_DISPATCH[strategy](source_id, **kwargs)
        _log_results(source_id, results, dry_run=True)
        # A dry-run exits non-zero on failure for the same reason an upload
        # does. It is the pre-flight check before a multi-hour backfill — one
        # that prints errors and still exits 0 is worse than none, because the
        # shell loop around it reports a clean pre-flight on a source that
        # cannot be fetched at all.
        _exit_on_failure(source_id, results)
        return

    bucket = require_bucket(args)
    results = UPLOAD_DISPATCH[strategy](source_id, bucket=bucket, **kwargs)
    _log_results(source_id, results, dry_run=False)
    _exit_on_failure(source_id, results)


def _exit_on_failure(source_id: str, results) -> None:
    failures = [r for r in results if r.status == "failed"]
    if failures:
        logger.error("%s: %d/%d slices failed", source_id, len(failures), len(results))
        raise SystemExit(2)


def _log_results(source_id: str, results, *, dry_run: bool) -> None:
    # BulkResult.manifest_count counts records on the fetch path and written
    # manifests on the upload path, so the unit has to follow the mode. A
    # single "%d records" label made a successful day of 2,827 records report
    # "1 records" and read like data loss.
    tag, unit = ("DRY-RUN", "records") if dry_run else ("WROTE", "file(s)")
    for r in results:
        if r.status == "ok":
            logger.info(
                "  %s %s %s: %d %s", tag, source_id, r.document, r.manifest_count, unit,
            )
        else:
            logger.error("  %s %s %s FAILED: %s", tag, source_id, r.document, r.error)
