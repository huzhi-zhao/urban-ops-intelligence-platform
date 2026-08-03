"""Per-source backfill: Winnipeg Snow Clearing Status (SRC-WPG-SNOW, snapshot).

There is nothing to back-fill in the usual sense. The upstream is an
overwrite-in-place snapshot with no time field, so the only day this can ever
collect is today — ``--start`` / ``--end`` are accepted for CLI uniformity and
ignored, exactly as they are for the static sources.

The scheduled collection does **not** run through this script; it runs as a
standalone timer on the storage node (ADR 0006 §2.2). This entry point exists
for manual re-runs and dry-run inspection.
"""

from __future__ import annotations

import argparse
import logging

from scripts.backfill._common import parse_args, run_standard_backfill
from scripts.backfill._registry import register_backfill

SOURCE_ID = "SRC-WPG-SNOW"


@register_backfill(SOURCE_ID)
def run(args: argparse.Namespace) -> None:
    run_standard_backfill(SOURCE_ID, args)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    run(parse_args("Winnipeg Snow Clearing Status collection (snapshot)"))
