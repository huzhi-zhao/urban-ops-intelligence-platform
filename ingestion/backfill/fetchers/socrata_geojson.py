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
        logger.info(
            "Socrata-GeoJSON fetch: dataset=%s (static, time window ignored)",
            self.dataset_name,
        )
        yield from self.client.fetch_page(limit=1000)
