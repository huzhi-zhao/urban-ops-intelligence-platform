"""Socrata fetcher — paginated fetch, windowed by timestamp or whole-table."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from datetime import date, datetime
from typing import Any

from ingestion.backfill.fetchers.base import Fetcher
from ingestion.clients.socrata_client import SocrataClient, SocrataFetchError
from ingestion.config import DatasetConfig

logger = logging.getLogger(__name__)


class SocrataFetcher(Fetcher):
    """Fetch Socrata dataset records, either windowed or as a whole table.

    Which of the two applies is decided by the caller (``build_fetcher``) from
    the dataset's effective partition strategy, not inferred here from whether
    ``timestamp_field`` happens to be set. The distinction matters: a
    ``monthly`` dataset that forgot its ``timestamp_field`` must fail loudly,
    not quietly turn into a full-table pull repeated once per month.
    """

    def __init__(
        self,
        ds: DatasetConfig,
        start: date,
        end: date,
        *,
        full_table: bool = False,
    ) -> None:
        if not ds.resource_id or not ds.domain:
            raise ValueError(
                f"Socrata dataset {ds.name!r} missing resource_id/domain",
            )
        if not full_table and not ds.timestamp_field:
            raise ValueError(
                f"Socrata dataset {ds.name!r} missing timestamp_field",
            )

        app_token = os.environ.get("SOCRATA_APP_TOKEN") or None
        self.client = SocrataClient(
            resource_id=ds.resource_id,
            domain=ds.domain,
            app_token=app_token,
        )
        self.timestamp_field = ds.timestamp_field
        self.start = start
        self.end = end
        self.full_table = full_table
        self.dataset_name = ds.name

    def fetch(self) -> Iterator[dict[str, Any]]:
        if self.full_table:
            yield from self._fetch_full_table()
            return
        yield from self._fetch_window()

    def _fetch_window(self) -> Iterator[dict[str, Any]]:
        """Fetch one ``[start, end)`` window, paginated.

        ``$order=:id`` is as load-bearing here as in ``_fetch_full_table``:
        limit/offset paging needs a stable row order, and a *filtered* result
        set is no more ordered than an unfiltered one. Without it a window
        spanning more than one page can repeat or drop rows at every page
        boundary — 2019-06-07 (3,585 rows, 4 pages) shipped one record twice,
        which surfaced only because the Silver primary-key assertion caught it.
        A dropped row would have surfaced as nothing at all.
        """
        start_dt = datetime.combine(self.start, datetime.min.time())
        end_dt = datetime.combine(self.end, datetime.min.time())
        logger.info(
            "Socrata fetch: dataset=%s field=%s window=[%s, %s)",
            self.dataset_name, self.timestamp_field, self.start, self.end,
        )
        try:
            yield from self.client.fetch_all_paginated(
                timestamp_field=self.timestamp_field,
                start_dt=start_dt,
                end_dt=end_dt,
                order_by=":id",
            )
        except SocrataFetchError as e:
            raise RuntimeError(
                f"Socrata fetch failed for {self.dataset_name!r} "
                f"[{self.start}, {self.end}): {e}",
            ) from e

    def _fetch_full_table(self) -> Iterator[dict[str, Any]]:
        """Walk the whole table, no time filter. Used by ``static`` datasets.

        ``$order=:id`` is not optional here: limit/offset paging over an
        unordered Socrata result set has no stable row order, so rows can be
        repeated or dropped between pages. On a static reference table that
        silently corrupts the one copy we keep.
        """
        logger.info(
            "Socrata fetch: dataset=%s whole table (static, time window ignored)",
            self.dataset_name,
        )
        try:
            yield from self.client.fetch_all_paginated(order_by=":id")
        except SocrataFetchError as e:
            raise RuntimeError(
                f"Socrata full-table fetch failed for {self.dataset_name!r}: {e}",
            ) from e
