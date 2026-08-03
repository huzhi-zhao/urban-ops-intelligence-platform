"""Per-source backfill: Winnipeg Plow Zone Boundaries (SRC-WPG-PLOW-ZONE, static).

82 MultiPolygons, fetched as Socrata GeoJSON in one shot. The geometry feeds
BO-4's spatial attribution; the Bronze → WKT path is generalised in batch 4.
"""

from __future__ import annotations

import argparse
import logging

from scripts.backfill._common import parse_args, run_standard_backfill
from scripts.backfill._registry import register_backfill

SOURCE_ID = "SRC-WPG-PLOW-ZONE"


@register_backfill(SOURCE_ID)
def run(args: argparse.Namespace) -> None:
    run_standard_backfill(SOURCE_ID, args)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    run(parse_args("Winnipeg Plow Zone Boundaries backfill (static, GeoJSON)"))
