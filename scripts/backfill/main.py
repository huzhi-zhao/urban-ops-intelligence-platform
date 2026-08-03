"""
Main entry for backfill operations.

Discovers every per-source script (``backfill_*.py``) in this package,
imports them so their ``@register_backfill`` decorators run, then dispatches
to the registered handler for the requested ``--source``.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import pkgutil
import sys

import scripts.backfill as _pkg
from scripts._env import load_cli_env
from scripts.backfill._common import parse_args
from scripts.backfill._registry import BACKFILL_REGISTRY

logger = logging.getLogger(__name__)


def _discover_backfills() -> None:
    """Import every ``backfill_*.py`` module in this package.

    Importing triggers the ``@register_backfill`` decorator in each module,
    which populates :data:`BACKFILL_REGISTRY`. New sources can be added by
    dropping a new ``backfill_<slug>.py`` file — no edits to ``main.py``.
    """
    for info in pkgutil.iter_modules(_pkg.__path__):
        if info.ispkg or not info.name.startswith("backfill_"):
            continue
        importlib.import_module(f"{_pkg.__name__}.{info.name}")


def main(argv: list[str] | None = None) -> int:
    # Before anything reads S3_* / SOCRATA_APP_TOKEN. An already-exported
    # value still wins; see scripts/_env.py.
    load_cli_env()

    # Discover first so --help can list available sources.
    _discover_backfills()

    parser = argparse.ArgumentParser(
        prog="python -m scripts.backfill.main",
        description=(
            "UOIP backfill main entry. Dispatches by --source to the "
            "matching per-source backfill script. Use --help to see this "
            "message; per-source scripts have their own --help with extra detail."
        ),
    )
    parser.add_argument(
        "--source", required=True,
        help="Source ID to backfill (e.g. SRC-Open-Meteo, SRC-WPG-SNOW)",
    )
    args, remaining = parser.parse_known_args(argv)

    if args.source not in BACKFILL_REGISTRY:
        available = sorted(BACKFILL_REGISTRY)
        print(
            f"Unknown source: {args.source}. Available: {available}",
            file=sys.stderr,
        )
        return 1

    # Re-inject remaining args as the per-source script's argv so it can
    # parse --start / --end / --bucket etc. with its own parser.
    sys.argv = [sys.argv[0]] + remaining
    handler = BACKFILL_REGISTRY[args.source]
    handler(parse_args(handler.__doc__ or "backfill"))
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    sys.exit(main())
