"""
Full-table fetch for snapshot sources.

Deliberately separate from ``ingestion.backfill.fetchers``: that factory builds
fetchers for a ``[start, end)`` window and requires a ``timestamp_field``, and a
snapshot source has neither. There is no window to ask for — the upstream holds
only its current state — so the fetch is "walk the whole table, now".
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from typing import Any

from ingestion.clients.socrata_client import SocrataClient, SocrataFetchError
from ingestion.config import ApiType, DatasetConfig

logger = logging.getLogger(__name__)

# Socrata's synthetic row id. Paging with $limit/$offset over an unordered
# result set lets rows shift between requests; ordering by :id pins them.
STABLE_ORDER = ":id"


class SnapshotFetchError(RuntimeError):
    """Raised when a snapshot's upstream walk fails."""


def fetch_snapshot_records(ds: DatasetConfig) -> Iterator[dict[str, Any]]:
    """Yield every record of ``ds``, one at a time.

    A generator by contract, not by convenience: the caller streams these
    straight to disk, and materialising them is the failure mode this exists to
    avoid (ADR 0006 §8.3.4).

    Raises:
        ValueError: if the dataset's api_type cannot be walked in full.
        SnapshotFetchError: if the upstream walk fails.
    """
    if ds.api_type != ApiType.SOCRATA:
        raise ValueError(
            f"Snapshot collection supports api_type=socrata only; dataset "
            f"{ds.name!r} is {ds.api_type.value!r}",
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
