"""
Fetcher Protocol and factory.

A :class:`Fetcher` pulls raw records for one dataset from its upstream API,
abstracting the differences between Socrata, Open-Meteo, static GeoJSON, etc.
The :func:`build_fetcher` factory dispatches to the correct concrete class
based on ``DatasetConfig.api_type``.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import Any, Protocol, runtime_checkable

from ingestion.config import ApiType, DatasetConfig, PartitionStrategy


@runtime_checkable
class Fetcher(Protocol):
    """Protocol: any object with a ``fetch()`` method yielding dicts."""

    def fetch(self) -> Iterator[dict[str, Any]]:
        ...


def build_fetcher(
    ds: DatasetConfig,
    start: date,
    end: date,
    *,
    strategy: PartitionStrategy,
) -> Fetcher:
    """Factory: return the correct fetcher for a dataset's api_type.

    Args:
        ds: the dataset to fetch
        start: inclusive start date (best-effort per api_type)
        end: exclusive end date (best-effort per api_type)
        strategy: the dataset's *effective* partition strategy, from
            ``SourceConfig.strategy_for(ds)``. Keyword-only and required:
            api_type alone does not determine how to fetch. A Socrata dataset
            partitioned ``static`` has to be pulled as a whole table with no
            time filter, because ``static`` forbids ``timestamp_field`` — and a
            fetcher that assumed a window there would simply fail. Making the
            caller state the strategy is what keeps the two layers from
            disagreeing silently.

    Raises:
        ValueError: if ``ds.api_type`` is not a known api_type.
    """
    # Local imports to avoid a circular dependency between fetchers.
    if ds.api_type == ApiType.SOCRATA:
        from ingestion.backfill.fetchers.socrata import SocrataFetcher

        return SocrataFetcher(
            ds, start=start, end=end, full_table=(strategy == "static"),
        )

    if ds.api_type == ApiType.SOCRATA_GEOJSON:
        from ingestion.backfill.fetchers.socrata_geojson import SocrataGeoJsonFetcher

        return SocrataGeoJsonFetcher(ds)

    if ds.api_type == ApiType.OPEN_METEO:
        from ingestion.backfill.fetchers.open_meteo import OpenMeteoFetcher

        return OpenMeteoFetcher(ds, start=start, end=end)

    if ds.api_type == ApiType.GENERIC_REST:
        from ingestion.backfill.fetchers.generic_rest import GenericRestFetcher

        return GenericRestFetcher(ds, start=start, end=end)

    raise ValueError(f"Unknown api_type: {ds.api_type!r}")
