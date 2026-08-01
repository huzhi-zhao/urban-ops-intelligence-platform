"""Unit tests for spark.transforms.dcp (Bronze -> Silver, no object storage/cluster needed).

Uses a local in-process SparkSession (master=local[1]).
Shapely is required; tests are skipped if not installed.
"""

from __future__ import annotations

import pytest

pyspark  = pytest.importorskip("pyspark")
shapely  = pytest.importorskip("shapely")

from pyspark.sql import Row, SparkSession  # noqa: E402

from spark.schemas.dcp_schemas import DCP_SILVER_SCHEMA  # noqa: E402
from spark.transforms.dcp import (  # noqa: E402
    add_geometry_wkt,
    cast_scalars,
    enforce_schema,
    split_by_validity,
)


@pytest.fixture(scope="module")
def spark():
    session = (
        SparkSession.builder.master("local[1]")
        .appName("test_dcp_transforms")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )
    yield session
    session.stop()


# Minimal GeoJSON for a tiny polygon — used in place of the real MultiPolygon.
_TINY_POLYGON_GEOJSON = (
    '{"type":"MultiPolygon",'
    '"coordinates":[[[[-74.0,40.5],[-74.0,40.6],[-73.9,40.6],[-73.9,40.5],[-74.0,40.5]]]]}'
)


def _make_raw(spark, rows):
    """Build a DataFrame that mimics what add_geometry_wkt outputs."""
    schema_fields = [
        ("borocode", "string"),
        ("boroname", "string"),
        ("shape_area", "string"),
        ("shape_leng", "string"),
        ("geometry_wkt", "string"),
    ]
    from pyspark.sql.types import StringType, StructField, StructType
    schema = StructType([StructField(n, StringType(), True) for n, _ in schema_fields])
    return spark.createDataFrame(rows, schema)


# ---------------------------------------------------------------------------
# add_geometry_wkt
# ---------------------------------------------------------------------------

def test_add_geometry_wkt_produces_wkt_string(spark):
    """add_geometry_wkt should convert a GeoJSON struct to a WKT string."""
    # Build a DataFrame with a the_geom column as an inferred struct
    raw_json = [
        '{"borocode":"1","boroname":"Manhattan","shape_area":"100","shape_leng":"50",'
        f'"the_geom":{_TINY_POLYGON_GEOJSON}}}'
    ]
    rdd = spark.sparkContext.parallelize(raw_json)
    df  = spark.read.json(rdd)

    result = add_geometry_wkt(df).select("geometry_wkt").collect()
    assert len(result) == 1
    wkt = result[0]["geometry_wkt"]
    assert wkt is not None
    assert wkt.upper().startswith("MULTIPOLYGON")


# ---------------------------------------------------------------------------
# cast_scalars
# ---------------------------------------------------------------------------

def test_cast_scalars_types_and_source_id(spark):
    rows = [Row(borocode="1", boroname="Manhattan", shape_area="636631537.285",
                shape_leng="359537.9", geometry_wkt="MULTIPOLYGON (((0 0, 1 0, 1 1, 0 0)))")]
    from pyspark.sql.types import StringType, StructField, StructType
    schema = StructType([
        StructField("borocode",     StringType(), True),
        StructField("boroname",     StringType(), True),
        StructField("shape_area",   StringType(), True),
        StructField("shape_leng",   StringType(), True),
        StructField("geometry_wkt", StringType(), True),
    ])
    df     = spark.createDataFrame(rows, schema)
    result = cast_scalars(df, source_id="SRC-DCP").collect()[0]

    assert result["borough_id"]      == 1
    assert result["borough_name"]    == "Manhattan"
    assert abs(result["shape_area_sqft"] - 636631537.285) < 1
    assert result["source_id"]       == "SRC-DCP"
    assert result["loaded_at"] is not None


# ---------------------------------------------------------------------------
# split_by_validity
# ---------------------------------------------------------------------------

def _typed_df(spark, rows):
    from pyspark.sql.types import (
        DoubleType,
        IntegerType,
        StringType,
        StructField,
        StructType,
    )
    schema = StructType([
        StructField("borough_id",      IntegerType(), True),
        StructField("borough_name",    StringType(),  True),
        StructField("shape_area_sqft", DoubleType(),  True),
        StructField("shape_leng_ft",   DoubleType(),  True),
        StructField("geometry_wkt",    StringType(),  True),
        StructField("source_id",       StringType(),  True),
    ])
    return spark.createDataFrame(rows, schema)


def test_valid_rows_pass(spark):
    rows = [Row(borough_id=1, borough_name="Manhattan", shape_area_sqft=1.0,
                shape_leng_ft=1.0, geometry_wkt="MULTIPOLYGON (((0 0,1 0,1 1,0 0)))",
                source_id="SRC-DCP")]
    df    = _typed_df(spark, rows)
    valid, rejected = split_by_validity(df)
    assert valid.count()    == 1
    assert rejected.count() == 0


def test_invalid_borough_id_rejected(spark):
    rows = [Row(borough_id=99, borough_name="Unknown", shape_area_sqft=1.0,
                shape_leng_ft=1.0, geometry_wkt="MULTIPOLYGON (((0 0,1 0,1 1,0 0)))",
                source_id="SRC-DCP")]
    df    = _typed_df(spark, rows)
    valid, rejected = split_by_validity(df)
    assert valid.count()    == 0
    assert rejected.count() == 1


def test_null_geometry_rejected(spark):
    rows = [Row(borough_id=2, borough_name="Bronx", shape_area_sqft=1.0,
                shape_leng_ft=1.0, geometry_wkt=None, source_id="SRC-DCP")]
    df    = _typed_df(spark, rows)
    valid, rejected = split_by_validity(df)
    assert valid.count()    == 0
    assert rejected.count() == 1


# ---------------------------------------------------------------------------
# enforce_schema
# ---------------------------------------------------------------------------

def test_enforce_schema_selects_correct_columns(spark):
    from pyspark.sql.types import (
        DoubleType,
        IntegerType,
        StringType,
        StructField,
        StructType,
        TimestampType,
    )

    rows = [Row(borough_id=3, borough_name="Brooklyn", shape_area_sqft=1.0,
                shape_leng_ft=1.0, geometry_wkt="MULTIPOLYGON (((0 0,1 0,1 1,0 0)))",
                source_id="SRC-DCP", loaded_at=None, extra_col="drop_me")]

    # Deliberately includes extra_col, which enforce_schema must drop. All
    # fields nullable so createDataFrame accepts loaded_at=None.
    raw_schema = StructType([
        StructField("borough_id",      IntegerType(), True),
        StructField("borough_name",    StringType(),  True),
        StructField("shape_area_sqft", DoubleType(),  True),
        StructField("shape_leng_ft",   DoubleType(),  True),
        StructField("geometry_wkt",    StringType(),  True),
        StructField("source_id",       StringType(),  True),
        StructField("loaded_at",       TimestampType(), True),
        StructField("extra_col",       StringType(),  True),
    ])
    df = spark.createDataFrame(rows, raw_schema)
    out = enforce_schema(df, DCP_SILVER_SCHEMA)
    assert set(out.columns) == {f.name for f in DCP_SILVER_SCHEMA.fields}


def test_enforce_schema_raises_on_missing_column(spark):
    from pyspark.sql.types import StringType, StructField, StructType
    schema = StructType([StructField("borough_id", StringType(), True)])
    df = spark.createDataFrame([Row(borough_id="1")], schema)
    with pytest.raises(ValueError, match="missing=\\["):
        enforce_schema(df, DCP_SILVER_SCHEMA)
