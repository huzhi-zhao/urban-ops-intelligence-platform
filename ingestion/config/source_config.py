"""
Pydantic models for source registry YAML files.

Each YAML file under ``config/sources/`` describes one upstream data source,
optionally with multiple datasets. The models in this module are the schema
contract — validation runs on every load and rejects unknown fields,
malformed IDs, and cross-field inconsistencies.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SourceType(StrEnum):
    """High-level source type — drives the client/loader dispatch in step #3."""

    REST_API_SOCRATA = "rest_api_socrata"
    REST_API = "rest_api"
    GEOJSON_STATIC = "geojson_static"


class ApiType(StrEnum):
    """Dataset-level API protocol — drives dataset client construction."""

    SOCRATA = "socrata"
    SOCRATA_GEOJSON = "socrata_geojson"
    OPEN_METEO = "open_meteo"
    GENERIC_REST = "generic_rest"


# Stable set of allowed values for source.status.
SourceStatus = Literal["production", "staging", "deprecated"]

# Bronze layer partitioning strategy — drives the object-storage path layout.
# daily:    data is split by record date into per-day files inside a month folder.
#           Used for high-volume event streams (service requests, weather).
#           Path: bronze/raw/{sid}/{ds}/{YYYY-MM}/data_{YYYY-MM-DD}.ndjson.gz
#                 + manifest_{YYYY-MM-DD}.json
# monthly:  data is written as a single file per month.
#           Used for lower-volume event streams.
#           Path: bronze/raw/{sid}/{ds}/data_{YYYY-MM}.ndjson.gz + manifest_{YYYY-MM}.json
# static:   data is written to a fixed-name shard; time is irrelevant.
#           Used for reference data (administrative boundaries).
#           Path: bronze/raw/{sid}/{ds}/data_static.ndjson.gz + manifest_static.json
# snapshot: data is partitioned by *collection* date rather than record date.
#           For upstreams that overwrite in place and keep no history, where each
#           day's pull is the only copy that will ever exist. `static` would
#           overwrite yesterday's file; `daily` needs a timestamp_field the data
#           does not have. This is the only strategy allowing timestamp_field=null.
#           Path: bronze/raw/{sid}/{ds}/ingest_date={YYYY-MM-DD}/data.ndjson.gz
#                 + manifest.json
PartitionStrategy = Literal["daily", "monthly", "static", "snapshot"]


class SourceMetadata(BaseModel):
    """Top-level metadata for a single data source."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        pattern=r"^SRC-[A-Za-z0-9-]+$",
        description=(
            "Stable source identifier. The canonical IDs in this project "
            "are defined in config/sources/*.yaml — load them via "
            "ingestion.config.load_all_sources() rather than hardcoding."
        ),
    )
    name: str = Field(min_length=1)
    type: SourceType
    owner: str = Field(min_length=1, description="Owning team slug, e.g. city_operations")
    priority: str = Field(pattern=r"^P[0-3]$")
    status: SourceStatus
    description: str | None = None
    partition_strategy: PartitionStrategy = Field(
        default="monthly",
        description=(
            "Bronze partitioning strategy. 'daily' splits records by their "
            "timestamp_field into per-day files inside a month folder (used for "
            "high-volume event streams). 'monthly' writes a single file per month. "
            "'static' writes a fixed-name shard (used for reference data with no "
            "time dimension). 'snapshot' partitions by collection date, for "
            "upstreams that overwrite in place and keep no history. Requires "
            "timestamp_field on every dataset when set to 'daily'; forbids it "
            "when 'static'; allows either when 'snapshot'."
        ),
    )


class DatasetConfig(BaseModel):
    """A single dataset within a source. Validated against its api_type."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z0-9_]+$")
    description: str | None = None
    api_type: ApiType

    # Common
    timestamp_field: str | None = Field(
        default=None,
        description="Field used for incremental windowing. null for static datasets.",
    )
    partition_strategy: PartitionStrategy | None = Field(
        default=None,
        description=(
            "Optional per-dataset override of the source's partition_strategy. "
            "Leave unset (the normal case) to inherit from source. Set it only "
            "when one source genuinely serves datasets with different time "
            "semantics — e.g. a weather source whose archive endpoint is "
            "keyed by record date (daily) while its forecast endpoint returns "
            "a rolling forward window that overlaps the previous pull and so "
            "must be partitioned by collection date (snapshot). Writing the "
            "forecast as 'daily' would rewrite Bronze files that already "
            "exist, which the Bronze contract forbids."
        ),
    )

    # Socrata / Socrata-GeoJSON
    resource_id: str | None = None
    domain: str | None = None

    # Socrata-GeoJSON only
    format: str | None = None

    # Open-Meteo / generic REST
    endpoint: str | None = None
    query_params: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _check_field_combinations(self) -> DatasetConfig:
        """Enforce required/forbidden field combinations per api_type."""
        api = self.api_type

        if api == ApiType.SOCRATA:
            self._require("resource_id", "domain")
            self._forbid("endpoint", "format")

        elif api == ApiType.SOCRATA_GEOJSON:
            self._require("resource_id", "domain")
            if self.format != "geojson":
                raise ValueError(
                    "api_type=socrata_geojson requires format='geojson'",
                )
            self._forbid("endpoint")

        elif api == ApiType.OPEN_METEO:
            self._require("endpoint")
            self._forbid("resource_id", "domain", "format")

        elif api == ApiType.GENERIC_REST:
            self._require("endpoint")
            self._forbid("resource_id", "domain", "format")

        return self

    def _require(self, *field_names: str) -> None:
        missing = [f for f in field_names if not getattr(self, f)]
        if missing:
            raise ValueError(
                f"api_type={self.api_type.value} requires: {', '.join(missing)}",
            )

    def _forbid(self, *field_names: str) -> None:
        present = [f for f in field_names if getattr(self, f) is not None]
        if present:
            raise ValueError(
                f"api_type={self.api_type.value} forbids: {', '.join(present)}",
            )


class SourceConfig(BaseModel):
    """A complete source definition (metadata + one or more datasets)."""

    model_config = ConfigDict(extra="forbid")

    source: SourceMetadata
    datasets: list[DatasetConfig] = Field(min_length=1)

    def strategy_for(self, dataset: DatasetConfig) -> PartitionStrategy:
        """The partition strategy that actually applies to one dataset.

        A dataset's own ``partition_strategy`` wins; otherwise it inherits the
        source's. Every consumer must go through here rather than reading
        ``source.partition_strategy`` directly, or a per-dataset override will
        be silently ignored on the write path.
        """
        return dataset.partition_strategy or self.source.partition_strategy

    def datasets_with_strategy(self, strategy: str) -> list[DatasetConfig]:
        """Datasets whose effective strategy is ``strategy`` (declaration order)."""
        return [d for d in self.datasets if self.strategy_for(d) == strategy]

    @model_validator(mode="after")
    def _check_partition_strategy_vs_timestamps(self) -> SourceConfig:
        """Cross-check each dataset's *effective* strategy against its timestamp_field.

        - ``daily`` requires one — it splits records by it.
        - ``static`` forbids one — time is ignored entirely.
        - ``snapshot`` allows either, and this is deliberate rather than an
          omission: it partitions by *collection* date, so it works on data with
          no time field at all, but will still use a timestamp_field to fill in
          the manifest's data_date range when the upstream happens to have one.
        - ``monthly`` (the default) is unconstrained.

        Checked per dataset rather than per source, because a dataset may
        override the strategy — the pairing that has to hold is
        (effective strategy, that dataset's timestamp_field).
        """
        missing = [
            d.name for d in self.datasets
            if self.strategy_for(d) == "daily" and not d.timestamp_field
        ]
        if missing:
            raise ValueError(
                f"source {self.source.id!r}: dataset(s) {missing!r} resolve to "
                f"partition_strategy='daily' but declare no timestamp_field; "
                f"daily partitioning splits records by their timestamp_field "
                f"and cannot work without one",
            )

        present = [
            d.name for d in self.datasets
            if self.strategy_for(d) == "static" and d.timestamp_field
        ]
        if present:
            raise ValueError(
                f"source {self.source.id!r}: dataset(s) {present!r} resolve to "
                f"partition_strategy='static' but declare timestamp_field; "
                f"static datasets ignore time and should have timestamp_field=null",
            )
        return self
