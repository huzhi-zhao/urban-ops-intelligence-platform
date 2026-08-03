"""
Backfill registry — per-source scripts self-register via ``@register_backfill``.

The main entry (``scripts/backfill/main.py``) auto-discovers every
``backfill_*.py`` module in this package and imports them, which triggers
each module's ``@register_backfill`` decorator. The main entry then looks
up the source_id in :data:`BACKFILL_REGISTRY` to find the run function.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable

BACKFILL_REGISTRY: dict[str, Callable[[argparse.Namespace], None]] = {}


def register_backfill(source_id: str) -> Callable:
    """Decorator: register ``func(args)`` as the handler for ``source_id``.

    Usage::

        @register_backfill("SRC-Open-Meteo")
        def run(args: argparse.Namespace) -> None: ...
    """

    def decorator(func: Callable[[argparse.Namespace], None]) -> Callable:
        if source_id in BACKFILL_REGISTRY:
            raise RuntimeError(
                f"Source {source_id!r} already registered with "
                f"{BACKFILL_REGISTRY[source_id].__name__}; "
                f"cannot also register {func.__name__}",
            )
        BACKFILL_REGISTRY[source_id] = func
        return func

    return decorator
