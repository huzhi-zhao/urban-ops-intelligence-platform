"""Per-source backfill: Open-Meteo Weather (SRC-Open-Meteo, daily archive).

Covers the ``weather_archive`` dataset. The source's ``weather_forecast``
dataset overrides the strategy to ``snapshot`` and cannot be backfilled at
all — the upstream keeps no history, so a past date has nothing to fetch.
"""

from __future__ import annotations

import argparse
import logging

from scripts.backfill._common import parse_args, run_standard_backfill
from scripts.backfill._registry import register_backfill

SOURCE_ID = "SRC-Open-Meteo"


@register_backfill(SOURCE_ID)
def run(args: argparse.Namespace) -> None:
    run_standard_backfill(SOURCE_ID, args)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    run(parse_args("Open-Meteo Weather backfill (daily archive)"))
