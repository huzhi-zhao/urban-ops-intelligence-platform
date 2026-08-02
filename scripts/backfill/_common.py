"""
Shared CLI helpers for per-source backfill scripts.

Per-source scripts accept a ``[start, end)`` window and delegate to
``scripts.backfill.bulk``. The bulk layer splits the window into
day-sized or month-sized chunks and calls the atomic ``BackfillFacade``
methods.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime


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
        "--dataset", default=None,
        help="Specific dataset name to backfill (default: all datasets in the source)",
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
