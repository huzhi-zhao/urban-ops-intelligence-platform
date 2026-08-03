"""Per-source backfill: Winnipeg 311 Service Requests (SRC-WPG-311, daily).

18 years / 18.4M rows, one Socrata query per day. This is the only source in
the project whose backfill is long enough to need planning rather than just
running — see scripts/backfill/plan_wpg_311_backfill.sh for the agreed windows
(full history for the last 10 years, winters only before that) and
docs/dev/launch/city-instance-switchover-launch.md §7.1 for why.
"""

from __future__ import annotations

import argparse
import logging

from scripts.backfill._common import parse_args, run_standard_backfill
from scripts.backfill._registry import register_backfill

SOURCE_ID = "SRC-WPG-311"


@register_backfill(SOURCE_ID)
def run(args: argparse.Namespace) -> None:
    run_standard_backfill(SOURCE_ID, args)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    run(parse_args("Winnipeg 311 Service Requests backfill (daily)"))
