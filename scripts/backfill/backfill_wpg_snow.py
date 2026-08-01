"""Per-source backfill: Winnipeg Snow Clearing Status (SRC-WPG-SNOW, snapshot).

There is nothing to back-fill in the usual sense. The upstream is an
overwrite-in-place snapshot with no time field, so the only day this can ever
collect is today — ``--start`` / ``--end`` are accepted for CLI uniformity and
ignored, exactly as they are for the static source.

The scheduled collection does **not** run through this script; it runs as a
standalone timer on the storage node (ADR 0006 §2.2). This entry point exists
for manual re-runs and dry-run inspection.
"""

from __future__ import annotations

import argparse
import logging

from ingestion.config import load_source_config
from scripts.backfill._common import (
    FETCH_DISPATCH,
    UPLOAD_DISPATCH,
    parse_args,
    require_bucket,
)
from scripts.backfill._registry import register_backfill

logger = logging.getLogger(__name__)
SOURCE_ID = "SRC-WPG-SNOW"


@register_backfill(SOURCE_ID)
def run(args: argparse.Namespace) -> None:
    cfg = load_source_config(SOURCE_ID)
    strategy = cfg.source.partition_strategy
    logger.info(
        "%s is an overwrite-in-place snapshot; --start=%s --end=%s are accepted "
        "but ignored — only the upstream's current state can be collected",
        SOURCE_ID, args.start, args.end,
    )

    if args.dry_run or args.action == "fetch":
        results = FETCH_DISPATCH[strategy](SOURCE_ID)
        _log_results(results, dry_run=True)
        return

    bucket = require_bucket(args)
    results = UPLOAD_DISPATCH[strategy](SOURCE_ID, bucket=bucket)
    _log_results(results, dry_run=False)
    failures = [r for r in results if r.status == "failed"]
    if failures:
        logger.error("%s: %d/%d chunks failed", SOURCE_ID, len(failures), len(results))
        raise SystemExit(2)


def _log_results(results, *, dry_run: bool) -> None:
    tag = "DRY-RUN" if dry_run else "WROTE"
    for r in results:
        if r.status == "ok":
            logger.info("  %s snapshot %s: %d records ok", tag, r.document, r.manifest_count)
        else:
            logger.error("  %s snapshot %s FAILED: %s", tag, r.document, r.error)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    run(parse_args("Winnipeg Snow Clearing Status collection (snapshot)"))
