"""
Full-table fetch for snapshot sources.

Deliberately separate from ``ingestion.backfill.fetchers``: that factory builds
fetchers for a ``[start, end)`` window and requires a ``timestamp_field``, and a
snapshot source has neither. There is no window to ask for — the upstream holds
only its current state — so the fetch is "walk the whole table, now".

The one exception is an Open-Meteo forecast, whose "current state" is a single
relative-window call. It borrows the backfill fetcher's forecast path, which
already knows how to issue that call; see ``_fetch_open_meteo``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

from ingestion.clients.socrata_client import SocrataClient, SocrataFetchError
from ingestion.config import ApiType, DatasetConfig

logger = logging.getLogger(__name__)

# Socrata's synthetic row id. Paging with $limit/$offset over an unordered
# result set lets rows shift between requests; ordering by :id pins them.
STABLE_ORDER = ":id"


class SnapshotFetchError(RuntimeError):
    """Raised when a snapshot's upstream walk fails."""


def _fetch_open_meteo(ds: DatasetConfig) -> Iterator[dict[str, Any]]:
    """One forecast call, flattened to one record per hour.

    Reuses the backfill fetcher's forecast path rather than a second HTTP
    client: that path already honours the dataset's configured
    ``past_days`` / ``forecast_days``, which is what a snapshot needs — the
    window passed in is only ``[today, today + 1)``, the collection date, and
    deriving the request from it would shrink the outlook to one day.

    Imported lazily so a Socrata-only collection never loads it.

    Raises:
        SnapshotFetchError: on a transport or HTTP failure, so the collector
            reports it the same way as a failed Socrata walk.
    """
    import requests

    from ingestion.backfill.fetchers.open_meteo import OpenMeteoFetcher

    today = date.today()
    fetcher = OpenMeteoFetcher(ds, today, today + timedelta(days=1))
    logger.info("Snapshot fetch: dataset=%s api=open_meteo", ds.name)
    try:
        yield from fetcher.fetch()
    except requests.RequestException as e:
        raise SnapshotFetchError(
            f"Snapshot fetch failed for {ds.name!r}: {type(e).__name__}: {e}",
        ) from e


def fetch_snapshot_records(ds: DatasetConfig) -> Iterator[dict[str, Any]]:
    """Yield every record of ``ds``, one at a time.

    A generator by contract, not by convenience: the caller streams these
    straight to disk, and materialising them is the failure mode this exists to
    avoid (ADR 0006 §8.3.4).

    Raises:
        ValueError: if the dataset's api_type cannot be walked in full.
        SnapshotFetchError: if the upstream walk fails.
    """
    if ds.api_type == ApiType.OPEN_METEO:
        yield from _fetch_open_meteo(ds)
        return
    if ds.api_type != ApiType.SOCRATA:
        raise ValueError(
            f"Snapshot collection supports api_type socrata and open_meteo; "
            f"dataset {ds.name!r} is {ds.api_type.value!r}",
        )
    if not ds.resource_id or not ds.domain:
        raise ValueError(f"Socrata dataset {ds.name!r} missing resource_id/domain")

    client = SocrataClient(
        resource_id=ds.resource_id,
        domain=ds.domain,
        app_token=os.environ.get("SOCRATA_APP_TOKEN") or None,
    )
    logger.info(
        "Snapshot fetch: dataset=%s resource=%s domain=%s",
        ds.name, ds.resource_id, ds.domain,
    )
    try:
        yield from client.fetch_all_paginated(order_by=STABLE_ORDER)
    except SocrataFetchError as e:
        raise SnapshotFetchError(
            f"Snapshot fetch failed for {ds.name!r}: {e}",
        ) from e
