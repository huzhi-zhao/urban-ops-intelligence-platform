"""Socrata-GeoJSON fetcher — static dataset, time window ignored."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from typing import Any

from ingestion.backfill.fetchers.base import Fetcher
from ingestion.clients.socrata_client import SocrataClient
from ingestion.config import DatasetConfig

logger = logging.getLogger(__name__)


class SocrataGeoJsonFetcher(Fetcher):
    """Fetch a static Socrata GeoJSON dataset.

    Time window is ignored — the dataset is fetched in one shot. Used for
    reference data like administrative boundaries that change rarely.
    """

    def __init__(self, ds: DatasetConfig) -> None:
        if not ds.resource_id or not ds.domain:
            raise ValueError(
                f"Socrata-GeoJSON dataset {ds.name!r} missing resource_id/domain",
            )
        app_token = os.environ.get("SOCRATA_APP_TOKEN") or None
        self.client = SocrataClient(
            resource_id=ds.resource_id,
            domain=ds.domain,
            app_token=app_token,
        )
        self.dataset_name = ds.name

    def fetch(self) -> Iterator[dict[str, Any]]:
        """Walk the whole table. Paginated, ordered by ``:id``.

        This used to be a single ``fetch_page(limit=1000)``. That works only
        while the boundary set stays under one Socrata page, and fails by
        *silently truncating* rather than raising once it does not — a
        geometry table that quietly loses rows produces a spatial join that
        drops records with no error anywhere. ``$order=:id`` is required for
        the same reason it is on the plain full-table walk: unordered
        limit/offset paging can repeat or skip rows between pages.
        """
        logger.info(
            "Socrata-GeoJSON fetch: dataset=%s (static, time window ignored)",
            self.dataset_name,
        )
        yield from self.client.fetch_all_paginated(order_by=":id")
