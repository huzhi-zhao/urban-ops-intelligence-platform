"""Bronze -> Silver transforms for static polygon-boundary sources.

Role-generic: works for any source whose Bronze payload is one row per
polygon with a GeoJSON geometry column plus a handful of scalar attribute
columns. Which columns those are (id field, grouping field, city-specific
literals) is the calling job's concern, not this module's — see
spark/jobs/etl_plow_zone_boundary.py for the Winnipeg wiring.

Pipeline order:
  1. add_geometry_wkt  — GeoJSON struct -> WKT, repairing OGC-invalid
                          geometry with shapely.validation.make_valid rather
                          than rejecting it (ADR 0010 D6)
  2. split_by_validity — reject rows with no usable geometry or a null
                          required id field
  3. enforce_schema    — align columns to the caller's target StructType
"""

from __future__ import annotations

import json

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql.types import BooleanType, DoubleType, StringType, StructField, StructType

_GEOMETRY_REPAIR_SCHEMA = StructType(
    [
        StructField("wkt", StringType()),
        StructField("repaired", BooleanType()),
        StructField("area_delta_pct", DoubleType()),
    ]
)


def _geojson_struct_to_valid_wkt(geom_json: str | None) -> tuple[str, bool, float | None] | None:
    """Convert a GeoJSON string to WKT, repairing invalid geometry in place.

    Returns (wkt, repaired, area_delta_pct) or None if there is no geometry
    to convert. area_delta_pct is None (not 0.0) when the geometry was
    already valid, so "not repaired" and "repaired with ~0% area change"
    stay distinguishable downstream.
    """
    if geom_json is None:
        return None
    from shapely.geometry import shape  # deferred: not available on driver at import time
    from shapely.validation import make_valid

    geometry = shape(json.loads(geom_json))
    if geometry.is_valid:
        return (geometry.wkt, False, None)

    original_area = geometry.area
    fixed = make_valid(geometry)
    area_delta_pct = (
        (fixed.area - original_area) / original_area * 100 if original_area else None
    )
    return (fixed.wkt, True, area_delta_pct)


_geometry_repair_udf = F.udf(_geojson_struct_to_valid_wkt, _GEOMETRY_REPAIR_SCHEMA)


def add_geometry_wkt(df: DataFrame, geometry_field: str = "the_geom") -> DataFrame:
    """Serialise the nested geometry struct to WKT, repairing it if invalid.

    Adds geometry_wkt, geometry_repaired, area_delta_pct. Invalid geometry is
    fixed rather than dropped: BO-4's spatial hit rate is measured on these
    same repaired geometries, so a silent reject would shrink the boundary
    coverage without anyone knowing an edge moved (ADR 0010 D6).
    """
    repair = _geometry_repair_udf(F.to_json(F.col(geometry_field)))
    return (
        df.withColumn("geometry_wkt", repair["wkt"])
        .withColumn("geometry_repaired", F.coalesce(repair["repaired"], F.lit(False)))
        .withColumn("area_delta_pct", repair["area_delta_pct"])
    )


def split_by_validity(df: DataFrame, required_id_fields: tuple[str, ...]) -> tuple[DataFrame, DataFrame]:
    """Split into (valid, rejected): reject null geometry or any missing id field."""
    reject_reason = F.when(F.col("geometry_wkt").isNull(), F.lit("null_geometry"))
    for field in required_id_fields:
        reject_reason = reject_reason.when(F.col(field).isNull(), F.lit(f"missing_{field}"))

    df = df.withColumn("_reject_reason", reject_reason)
    valid = df.filter(F.col("_reject_reason").isNull()).drop("_reject_reason")
    rejected = df.filter(F.col("_reject_reason").isNotNull())
    return valid, rejected


def enforce_schema(df: DataFrame, schema: StructType) -> DataFrame:
    """Project df to exactly the columns declared in the target StructType.

    Extra columns from the raw pipeline are silently dropped by the select.
    Only missing columns cause a hard failure — that means a required
    transform step was skipped.
    """
    expected = {f.name for f in schema.fields}
    missing = expected - set(df.columns)
    if missing:
        raise ValueError(
            f"Silver output columns don't match target schema: missing={sorted(missing)}"
        )
    return df.select([F.col(f.name).cast(f.dataType) for f in schema.fields])
