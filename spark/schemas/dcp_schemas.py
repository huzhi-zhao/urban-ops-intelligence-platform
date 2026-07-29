"""StructType definitions for SRC-DCP / borough_boundaries.

Static reference table: 5 rows, one per NYC borough.

NOTE — this source is the documented exception to the "always pass schema="
rule in AGENTS.md: etl_dcp.py deliberately calls spark.read.json() *without*
a schema, because the_geom is a MultiPolygon whose declared StructType would
be hundreds of nested ArrayType levels deep. The nested geometry is serialised
straight back to JSON and handed to Shapely, so Spark's inference of it never
reaches the Silver output. Only the scalar fields are re-typed, in
spark/transforms/dcp.py::cast_scalars.

DCP_RAW_SCHEMA below is therefore currently UNUSED — kept as the documented
expected raw shape. Either wire it into etl_dcp.py (as a schema for the scalar
fields only) or delete it; do not assume it is enforced today.

TODO: contracts/api-contracts/dcp-borough-boundaries.yaml does not exist yet —
this schema is frozen against observed Bronze data, not against a contract.
"""

from __future__ import annotations

from pyspark.sql.types import (
    DoubleType,
    IntegerType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

# Bronze NDJSON shape — all numeric fields arrive as strings from the API.
# the_geom is a nested struct: {type: STRING, coordinates: ARRAY<...>}
# We read it as a raw JSON string via spark.read.json; Spark infers the_geom
# as a StructType automatically, so we only declare the scalar fields here
# and handle the_geom via to_json() in the transform.
DCP_RAW_SCHEMA = StructType(
    [
        StructField("borocode", StringType(), nullable=False),
        StructField("boroname", StringType(), nullable=False),
        StructField("shape_area", StringType(), nullable=True),
        StructField("shape_leng", StringType(), nullable=True),
        # the_geom intentionally omitted: Spark infers the nested MultiPolygon
        # struct automatically; the transform serialises it back to JSON then
        # converts to WKT via Shapely.
    ]
)

# Silver grain: one row per borough, geometry stored as WKT string.
# BigQuery consumes geometry_wkt via ST_GEOGFROMTEXT(geometry_wkt).
DCP_SILVER_SCHEMA = StructType(
    [
        StructField("borough_id",      IntegerType(), nullable=False),   # borocode cast to int
        StructField("borough_name",    StringType(),  nullable=False),
        StructField("shape_area_sqft", DoubleType(),  nullable=True),
        StructField("shape_leng_ft",   DoubleType(),  nullable=True),
        StructField("geometry_wkt",    StringType(),  nullable=False),   # MultiPolygon WKT, WGS84
        StructField("source_id",       StringType(),  nullable=False),
        StructField("loaded_at",       TimestampType(), nullable=False),
    ]
)
