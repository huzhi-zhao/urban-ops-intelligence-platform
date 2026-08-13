"""StructType definitions for SRC-WPG-PLOW-ZONE / plow_zones.

Static reference table: 82 rows, one per polygon (see contracts/api-contracts/
winnipeg-plow-zones.yaml — a zone is made of several non-adjacent polygons,
so this is *not* one row per zone; the 25-zone dim_plow_zone in ADR 0010 D3
groups these 82 rows by plow_zone downstream, in Gold).

geometry_wkt is produced by spark.transforms.geography_boundary, which
repairs OGC-invalid geometry (8/82 polygons, per ADR 0010 D6) instead of
dropping it — hence geometry_repaired / area_delta_pct on every row.
"""

from __future__ import annotations

from pyspark.sql.types import (
    BooleanType,
    DoubleType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

# Silver grain: one row per polygon, geometry stored as WKT string.
# Trino consumes geometry_wkt via ST_GeomFromText(geometry_wkt).
PLOW_ZONE_BOUNDARY_SILVER_SCHEMA = StructType(
    [
        StructField("entity_id", StringType(), nullable=False),  # PK, 82/82 distinct
        StructField("plow_zone", StringType(), nullable=False),  # 25 distinct values
        StructField("city_area", StringType(), nullable=True),  # East / North / South
        StructField("geometry_wkt", StringType(), nullable=False),  # MultiPolygon WKT, WGS84
        StructField("geometry_repaired", BooleanType(), nullable=False),
        StructField("area_delta_pct", DoubleType(), nullable=True),  # null unless repaired
        StructField("source_id", StringType(), nullable=False),
        StructField("loaded_at", TimestampType(), nullable=False),
    ]
)
