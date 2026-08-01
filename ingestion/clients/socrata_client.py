"""
Socrata API Client — reusable client for NYC Open Data (Socrata) APIs.

Supports:
- Pagination (limit/offset)
- Rate-limit retry with exponential backoff
- Optional App Token for higher rate limits
- Incremental fetch by timestamp field

Used by: SRC-NYC-311 (311), SRC-NYPD (NYPD Collisions)
"""

from __future__ import annotations

import time
from collections.abc import Generator
from datetime import datetime
from typing import Any

import requests


class SocrataClient:
    """Reusable Socrata REST API client with pagination and retry logic."""

    DEFAULT_PAGE_SIZE = 1000
    MAX_RETRIES = 5
    INITIAL_BACKOFF_SECS = 1.0
    RATE_LIMIT_STATUS_CODE = 429

    def __init__(
        self,
        resource_id: str,
        domain: str = "data.cityofnewyork.us",
        app_token: str | None = None,
        timeout_secs: int = 60,
    ) -> None:
        """
        Initialize Socrata client.

        Args:
            resource_id: Socrata resource ID (e.g. "erm2-nwe9" for 311).
            domain: Socrata domain. Defaults to NYC Open Data domain.
            app_token: Optional App Token for higher rate limits.
                       Set via SOCRATA_APP_TOKEN env var if not provided.
            timeout_secs: Request timeout in seconds.
        """
        self.resource_id = resource_id
        self.domain = domain
        self.app_token = app_token
        self.timeout_secs = timeout_secs
        self._session = requests.Session()
        if app_token:
            self._session.headers["X-App-Token"] = app_token

    @property
    def base_url(self) -> str:
        return f"https://{self.domain}/resource/{self.resource_id}.json"

    def _build_pagination_params(
        self,
        limit: int | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        """Build pagination query params for Socrata API."""
        params: dict[str, Any] = {}
        if limit is not None:
            params["$limit"] = limit
        if offset is not None:
            params["$offset"] = offset
        return params

    def _build_incremental_params(
        self,
        timestamp_field: str,
        start_dt: datetime,
        end_dt: datetime | None = None,
    ) -> dict[str, Any]:
        """
        Build WHERE clause for incremental fetch by timestamp.

        Args:
            timestamp_field: Field name to filter on (e.g. "created_date").
            start_dt: Start of the window (inclusive).
            end_dt: End of the window (exclusive). If None, fetches up to now.

        Returns:
            SoQL query params dict with "$where" clause.
        """
        # Socrata uses ISO-8601 format with TZ: 2026-01-15T00:00:00.000
        start_iso = start_dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
        where_clause = f"{timestamp_field} >= '{start_iso}'"
        if end_dt:
            end_iso = end_dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
            where_clause += f" and {timestamp_field} < '{end_iso}'"
        return {"$where": where_clause}

    def _request_with_retry(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Execute GET request with exponential backoff on rate-limit."""
        backoff = self.INITIAL_BACKOFF_SECS
        last_exception: Exception | None = None

        for attempt in range(self.MAX_RETRIES):
            try:
                response = self._session.get(
                    self.base_url,
                    params=params,
                    timeout=self.timeout_secs,
                )
                if response.status_code == self.RATE_LIMIT_STATUS_CODE:
                    # Rate limited — retry with backoff
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                response.raise_for_status()
                return response.json()
            except requests.RequestException as e:
                last_exception = e
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                raise SocrataFetchError(
                    f"Failed after {self.MAX_RETRIES} attempts: {e}"
                ) from last_exception

        raise SocrataFetchError(f"Unexpected error after {self.MAX_RETRIES} retries") from last_exception

    def fetch_page(
        self,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
        extra_params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Fetch a single page of results.

        Args:
            limit: Number of records per page (max 1000 for Socrata).
            offset: Pagination offset.
            extra_params: Additional SoQL params (e.g. $select, $order).

        Returns:
            List of record dicts.
        """
        params = self._build_pagination_params(limit=limit, offset=offset)
        if extra_params:
            params.update(extra_params)
        return self._request_with_retry(params)

    def fetch_all_paginated(
        self,
        timestamp_field: str | None = None,
        start_dt: datetime | None = None,
        end_dt: datetime | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
        order_by: str | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        """
        Generator: fetch all records with automatic pagination.

        Handles rate-limit 429 responses with exponential backoff.
        Optionally filters by timestamp window for incremental loads.

        Args:
            timestamp_field: If provided, enables incremental filtering.
            start_dt: Start of timestamp window (inclusive).
            end_dt: End of timestamp window (exclusive).
            page_size: Records per page. Socrata max is 1000.
            order_by: SoQL ``$order`` expression. Pass ``":id"`` when walking a
                whole table: limit/offset paging over an unordered result set
                gives Socrata no stable row order, so rows can be repeated or
                skipped between pages. It matters most for the longest walks,
                which are exactly the ones nobody re-reads to notice.

        Yields:
            Individual record dicts.
        """
        offset = 0
        extra_params = {}
        if timestamp_field and start_dt:
            extra_params.update(
                self._build_incremental_params(timestamp_field, start_dt, end_dt)
            )
        if order_by:
            extra_params["$order"] = order_by

        while True:
            records = self.fetch_page(
                limit=page_size,
                offset=offset,
                extra_params=extra_params if extra_params else None,
            )
            if not records:
                break
            yield from records
            # Socrata returns at most page_size per request; stop when we get fewer
            if len(records) < page_size:
                break
            offset += page_size

    def fetch_all(
        self,
        timestamp_field: str | None = None,
        start_dt: datetime | None = None,
        end_dt: datetime | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> list[dict[str, Any]]:
        """
        Convenience: fetch all records as a single list.

        For large datasets (>100k rows), prefer fetch_all_paginated() generator
        to avoid memory pressure.
        """
        return list(
            self.fetch_all_paginated(
                timestamp_field=timestamp_field,
                start_dt=start_dt,
                end_dt=end_dt,
                page_size=page_size,
            )
        )


class SocrataFetchError(Exception):
    """Raised when Socrata API fetch fails after all retries."""
