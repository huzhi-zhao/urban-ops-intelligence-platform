"""StructType definitions for SRC-WPG-SNOW / snow_clearing_status.

``partition_strategy: snapshot`` — the upstream (g3p4-h83y, ~237,867 rows of
address x street-side) carries no timestamp field and overwrites in place, so
each Bronze ``ingest_date=`` partition is the only copy of that day's state
that will ever exist (config/sources/winnipeg_snow_clearing.yaml).

Silver collapses this to one row per ``plow_zone`` per ``snapshot_date`` — a
point-in-polygon count of address records against silver_plow_zone_boundary,
same join family as silver_service_request.plow_zone (see contracts/
silver-contracts/silver_snow_clearing_address.yaml). This is
``dim_plow_zone.address_count``'s only Silver lineage: a point-in-time
snapshot used to normalize a decade of historical scoring, not a time series
of its own — Gold reads only the latest ``snapshot_date``.
"""

from __future__ import annotations

from pyspark.sql.types import (
    BooleanType,
    DateType,
    IntegerType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

# Bronze NDJSON shape, one record per address x street-side. has_street /
# has_alley / has_walk are the upstream's current-state booleans; only the
# geometry is needed to derive the Silver zone count, but the fields are kept
# here so the raw shape is fully documented per AGENTS.md "verify against
# contracts/api-contracts/" — this table has no api-contract file yet, this
# schema is the closest thing to one.
SNOW_CLEARING_ADDRESS_RAW_SCHEMA = StructType(
    [
        StructField("has_street", BooleanType(), nullable=True),
        StructField("has_alley", BooleanType(), nullable=True),
        StructField("has_walk", BooleanType(), nullable=True),
        # geometry arrives as a GeoJSON Point object; the point-in-polygon
        # join that produces plow_zone / address_count happens in the Silver
        # build job, not in this schema.
    ]
)

# Silver grain: one row per (plow_zone, snapshot_date).
SNOW_CLEARING_ADDRESS_SILVER_SCHEMA = StructType(
    [
        StructField("plow_zone", StringType(), nullable=False),  # 25 distinct values
        StructField("address_count", IntegerType(), nullable=False),
        # The g3p4-h83y ingest_date this count was computed from — a
        # point-in-time count, not a time series (SRC-WPG-SNOW is snapshot).
        StructField("snapshot_date", DateType(), nullable=False),
        StructField("source_id", StringType(), nullable=False),
        StructField("loaded_at", TimestampType(), nullable=False),
    ]
)
