"""Per-source backfill: Winnipeg Snow Parking Bans (SRC-WPG-PARKING-BAN, static).

49 rows. One pull fetches the whole table; ``--start`` / ``--end`` are ignored.
"""

from __future__ import annotations

import argparse
import logging

from scripts.backfill._common import parse_args, run_standard_backfill
from scripts.backfill._registry import register_backfill

SOURCE_ID = "SRC-WPG-PARKING-BAN"


@register_backfill(SOURCE_ID)
def run(args: argparse.Namespace) -> None:
    run_standard_backfill(SOURCE_ID, args)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    run(parse_args("Winnipeg Snow Parking Bans backfill (static)"))
