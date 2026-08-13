"""Unit tests for spark.transforms.geography_boundary (Bronze -> Silver, no object storage/cluster needed).

Uses a local in-process SparkSession (master=local[1]).
Shapely is required; tests are skipped if not installed.

Fixture geometry is synthetic and role-generic (a "zone" made of polygons),
not tied to any one city's dataset — see CLAUDE.md 城市无关护栏.
"""

from __future__ import annotations

import pytest

pyspark = pytest.importorskip("pyspark")
shapely = pytest.importorskip("shapely")

from pyspark.sql import Row, SparkSession  # noqa: E402
from pyspark.sql.types import (  # noqa: E402
    BooleanType,
    DoubleType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

from spark.transforms.geography_boundary import (  # noqa: E402
    add_geometry_wkt,
    enforce_schema,
    split_by_validity,
)


@pytest.fixture(scope="module")
def spark():
    session = (
        SparkSession.builder.master("local[1]")
        .appName("test_geography_boundary_transforms")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )
    yield session
    session.stop()


_VALID_POLYGON_GEOJSON = (
    '{"type":"MultiPolygon",'
    '"coordinates":[[[[-97.15,49.90],[-97.15,49.95],[-97.10,49.95],[-97.10,49.90],[-97.15,49.90]]]]}'
)

# A bowtie polygon: self-intersecting, OGC-invalid.
_INVALID_POLYGON_GEOJSON = (
    '{"type":"Polygon","coordinates":'
    '[[[0,0],[2,2],[2,0],[0,2],[0,0]]]}'
)


# ---------------------------------------------------------------------------
# add_geometry_wkt
# ---------------------------------------------------------------------------

def test_add_geometry_wkt_valid_geometry_not_flagged_repaired(spark):
    raw_json = [f'{{"zone_id":"A","the_geom":{_VALID_POLYGON_GEOJSON}}}']
    rdd = spark.sparkContext.parallelize(raw_json)
    df = spark.read.json(rdd)

    result = add_geometry_wkt(df).select("geometry_wkt", "geometry_repaired", "area_delta_pct").collect()[0]
    assert result["geometry_wkt"].upper().startswith("MULTIPOLYGON")
    assert result["geometry_repaired"] is False
    assert result["area_delta_pct"] is None


def test_add_geometry_wkt_invalid_geometry_is_repaired_not_dropped(spark):
    raw_json = [f'{{"zone_id":"B","the_geom":{_INVALID_POLYGON_GEOJSON}}}']
    rdd = spark.sparkContext.parallelize(raw_json)
    df = spark.read.json(rdd)

    result = add_geometry_wkt(df).select("geometry_wkt", "geometry_repaired").collect()[0]
    assert result["geometry_wkt"] is not None
    assert result["geometry_repaired"] is True


def test_add_geometry_wkt_null_geometry_produces_null_wkt(spark):
    # Mixed with a valid row so the_geom still infers as a struct column
    # (an all-null column would infer as StringType, which to_json rejects).
    raw_json = [
        f'{{"zone_id":"A","the_geom":{_VALID_POLYGON_GEOJSON}}}',
        '{"zone_id":"C","the_geom":null}',
    ]
    rdd = spark.sparkContext.parallelize(raw_json)
    df = spark.read.json(rdd)

    result = (
        add_geometry_wkt(df)
        .filter("zone_id = 'C'")
        .select("geometry_wkt", "geometry_repaired")
        .collect()[0]
    )
    assert result["geometry_wkt"] is None
    assert result["geometry_repaired"] is False


# ---------------------------------------------------------------------------
# split_by_validity
# ---------------------------------------------------------------------------

def _boundary_df(spark, rows):
    schema = StructType([
        StructField("zone_id", StringType(), True),
        StructField("polygon_id", StringType(), True),
        StructField("geometry_wkt", StringType(), True),
    ])
    return spark.createDataFrame(rows, schema)


def test_valid_row_with_all_ids_passes(spark):
    df = _boundary_df(spark, [Row(zone_id="A", polygon_id="p1", geometry_wkt="MULTIPOLYGON (((0 0,1 0,1 1,0 0)))")])
    valid, rejected = split_by_validity(df, required_id_fields=("zone_id", "polygon_id"))
    assert valid.count() == 1
    assert rejected.count() == 0


def test_null_geometry_rejected(spark):
    df = _boundary_df(spark, [Row(zone_id="A", polygon_id="p1", geometry_wkt=None)])
    valid, rejected = split_by_validity(df, required_id_fields=("zone_id", "polygon_id"))
    assert valid.count() == 0
    assert rejected.count() == 1


def test_missing_required_id_field_rejected(spark):
    df = _boundary_df(spark, [Row(zone_id=None, polygon_id="p1", geometry_wkt="MULTIPOLYGON (((0 0,1 0,1 1,0 0)))")])
    valid, rejected = split_by_validity(df, required_id_fields=("zone_id", "polygon_id"))
    assert valid.count() == 0
    assert rejected.count() == 1


# ---------------------------------------------------------------------------
# enforce_schema
# ---------------------------------------------------------------------------

_TARGET_SCHEMA = StructType([
    StructField("zone_id", StringType(), True),
    StructField("geometry_wkt", StringType(), True),
    StructField("geometry_repaired", BooleanType(), True),
    StructField("area_delta_pct", DoubleType(), True),
    StructField("loaded_at", TimestampType(), True),
])


def test_enforce_schema_selects_correct_columns(spark):
    raw_schema = StructType([
        StructField("zone_id", StringType(), True),
        StructField("geometry_wkt", StringType(), True),
        StructField("geometry_repaired", BooleanType(), True),
        StructField("area_delta_pct", DoubleType(), True),
        StructField("loaded_at", TimestampType(), True),
        StructField("extra_col", StringType(), True),
    ])
    rows = [Row(zone_id="A", geometry_wkt="MULTIPOLYGON (((0 0,1 0,1 1,0 0)))",
                geometry_repaired=False, area_delta_pct=None, loaded_at=None, extra_col="drop_me")]
    df = spark.createDataFrame(rows, raw_schema)
    out = enforce_schema(df, _TARGET_SCHEMA)
    assert set(out.columns) == {f.name for f in _TARGET_SCHEMA.fields}


def test_enforce_schema_raises_on_missing_column(spark):
    schema = StructType([StructField("zone_id", StringType(), True)])
    df = spark.createDataFrame([Row(zone_id="A")], schema)
    with pytest.raises(ValueError, match="missing=\\["):
        enforce_schema(df, _TARGET_SCHEMA)
