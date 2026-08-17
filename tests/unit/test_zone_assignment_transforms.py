"""Unit tests for spark.transforms.zone_assignment (no object storage/cluster needed).

Uses a local in-process SparkSession (master=local[1]).
Shapely is required; tests are skipped if not installed.

Fixture geometry is synthetic and role-generic — two adjacent square "zones",
one of them made of two disjoint pieces, plus a self-intersecting bowtie to
exercise the make_valid path. Nothing here is tied to a deployed city
(CLAUDE.md 城市无关护栏).
"""

from __future__ import annotations

import pytest

pyspark = pytest.importorskip("pyspark")
shapely = pytest.importorskip("shapely")

from pyspark.sql import Row, SparkSession  # noqa: E402

from spark.transforms.zone_assignment import (  # noqa: E402
    MATCH_STATUS_MATCHED,
    MATCH_STATUS_NO_GEO,
    MATCH_STATUS_UNMATCHED,
    add_point_coordinates,
    assign_zone,
    build_zone_polygons,
    spatial_hit_rate,
)


@pytest.fixture(scope="module")
def spark():
    session = (
        SparkSession.builder.master("local[1]")
        .appName("test_zone_assignment_transforms")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )
    yield session
    session.stop()


# Zone A occupies x in [0, 1]; zone B is two disjoint pieces, x in [2, 3] and
# x in [4, 5] — the "one label, several polygons" case.
_ZONE_A = "POLYGON ((0 0, 0 1, 1 1, 1 0, 0 0))"
_ZONE_B_WEST = "POLYGON ((2 0, 2 1, 3 1, 3 0, 2 0))"
_ZONE_B_EAST = "POLYGON ((4 0, 4 1, 5 1, 5 0, 4 0))"
# Self-intersecting: OGC-invalid, must be repaired rather than dropped.
_ZONE_C_BOWTIE = "POLYGON ((10 10, 12 12, 12 10, 10 12, 10 10))"

_POLYGONS = [
    ("A", _ZONE_A),
    ("B", _ZONE_B_WEST),
    ("B", _ZONE_B_EAST),
    ("C", _ZONE_C_BOWTIE),
]


# ---------------------------------------------------------------------------
# add_point_coordinates
# ---------------------------------------------------------------------------


def test_add_point_coordinates_reads_geojson_lon_lat_order(spark):
    df = spark.createDataFrame(
        [Row(geometry=Row(type="Point", coordinates=[-97.14, 49.89]))]
    )

    result = add_point_coordinates(df, geometry_field="geometry").collect()[0]

    # GeoJSON is [longitude, latitude] — the first element is the longitude.
    assert result["longitude"] == pytest.approx(-97.14)
    assert result["latitude"] == pytest.approx(49.89)


def test_add_point_coordinates_yields_null_for_missing_geometry(spark):
    df = spark.createDataFrame(
        [
            Row(geometry=Row(type="Point", coordinates=[1.0, 2.0])),
            Row(geometry=None),
        ]
    )

    rows = add_point_coordinates(df, geometry_field="geometry").collect()

    assert rows[1]["longitude"] is None
    assert rows[1]["latitude"] is None


# ---------------------------------------------------------------------------
# build_zone_polygons
# ---------------------------------------------------------------------------


def test_build_zone_polygons_keeps_one_entry_per_polygon(spark):
    boundary = spark.createDataFrame(
        [
            Row(plow_zone="B", geometry_wkt=_ZONE_B_WEST),
            Row(plow_zone="B", geometry_wkt=_ZONE_B_EAST),
            Row(plow_zone="A", geometry_wkt=None),
        ]
    )

    polygons = build_zone_polygons(boundary, zone_field="plow_zone")

    # Both pieces of B survive as separate entries; the null-geometry row does not.
    assert sorted(polygons) == sorted([("B", _ZONE_B_WEST), ("B", _ZONE_B_EAST)])


# ---------------------------------------------------------------------------
# assign_zone
# ---------------------------------------------------------------------------


def _assign(spark, points):
    df = spark.createDataFrame(
        [Row(row_id=i, longitude=lon, latitude=lat) for i, (lon, lat) in enumerate(points)]
    )
    return {
        row["row_id"]: row
        for row in assign_zone(df, _POLYGONS).collect()
    }


def test_assign_zone_matches_point_inside_polygon(spark):
    rows = _assign(spark, [(0.5, 0.5)])

    assert rows[0]["has_geo"] is True
    assert rows[0]["geo_match_status"] == MATCH_STATUS_MATCHED
    assert rows[0]["zone"] == "A"


def test_assign_zone_matches_both_disjoint_pieces_of_one_label(spark):
    rows = _assign(spark, [(2.5, 0.5), (4.5, 0.5)])

    assert rows[0]["zone"] == "B"
    assert rows[1]["zone"] == "B"


def test_assign_zone_marks_outside_point_unmatched_not_no_geo(spark):
    rows = _assign(spark, [(99.0, 99.0)])

    # The distinction is the whole point of the three-value status: this row
    # HAS coordinates, so it belongs in the hit-rate denominator.
    assert rows[0]["has_geo"] is True
    assert rows[0]["geo_match_status"] == MATCH_STATUS_UNMATCHED
    assert rows[0]["zone"] is None


def test_assign_zone_marks_missing_coordinates_no_geo(spark):
    rows = _assign(spark, [(None, None), (1.5, 0.5), (None, 49.9)])

    assert rows[0]["has_geo"] is False
    assert rows[0]["geo_match_status"] == MATCH_STATUS_NO_GEO
    # A half-present coordinate pair is not usable either.
    assert rows[2]["geo_match_status"] == MATCH_STATUS_NO_GEO


def test_assign_zone_repairs_invalid_polygon_instead_of_dropping_it(spark):
    # make_valid splits the bowtie into its two triangles about the crossing
    # at (11, 11); (10.25, 11.0) is inside the western one. If the invalid
    # polygon were dropped instead of repaired, this would come back unmatched.
    rows = _assign(spark, [(10.25, 11.0)])

    assert rows[0]["geo_match_status"] == MATCH_STATUS_MATCHED
    assert rows[0]["zone"] == "C"


def test_assign_zone_honours_caller_supplied_column_names(spark):
    df = spark.createDataFrame([Row(lon=0.5, lat=0.5)])

    result = assign_zone(
        df,
        _POLYGONS,
        longitude_col="lon",
        latitude_col="lat",
        zone_col="plow_zone",
        match_status_col="geo_match_status",
    ).collect()[0]

    assert result["plow_zone"] == "A"
    assert "zone" not in result.asDict()


def test_assign_zone_rejects_empty_polygon_list(spark):
    df = spark.createDataFrame([Row(longitude=0.5, latitude=0.5)])

    with pytest.raises(ValueError, match="no polygons"):
        assign_zone(df, [])


def test_assign_zone_leaves_no_scratch_columns(spark):
    df = spark.createDataFrame([Row(longitude=0.5, latitude=0.5)])

    assert "_zone_assignment" not in assign_zone(df, _POLYGONS).columns


# ---------------------------------------------------------------------------
# spatial_hit_rate
# ---------------------------------------------------------------------------


def test_spatial_hit_rate_denominator_excludes_no_geo_rows(spark):
    df = spark.createDataFrame(
        [
            Row(longitude=0.5, latitude=0.5),   # matched
            Row(longitude=2.5, latitude=0.5),   # matched
            Row(longitude=99.0, latitude=99.0),  # unmatched
            Row(longitude=None, latitude=None),  # no_geo — must not dilute the rate
            Row(longitude=None, latitude=None),
        ]
    )

    matched, has_geo = spatial_hit_rate(assign_zone(df, _POLYGONS))

    assert (matched, has_geo) == (2, 3)
