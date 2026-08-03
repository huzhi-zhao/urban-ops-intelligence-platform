"""Per-source backfill: Winnipeg Snow Clearing Shifts (SRC-WPG-PLOW-SHIFT, static).

418 rows for the whole ten-year history — one pull fetches all of it, and
``--start`` / ``--end`` are ignored. Why a small history table is `static`
rather than `monthly` is argued in config/sources/winnipeg_plow_shifts.yaml.
"""

from __future__ import annotations

import argparse
import logging

from scripts.backfill._common import parse_args, run_standard_backfill
from scripts.backfill._registry import register_backfill

SOURCE_ID = "SRC-WPG-PLOW-SHIFT"


@register_backfill(SOURCE_ID)
def run(args: argparse.Namespace) -> None:
    run_standard_backfill(SOURCE_ID, args)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    run(parse_args("Winnipeg Snow Clearing Shifts backfill (static)"))
