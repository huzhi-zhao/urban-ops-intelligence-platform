"""Point-to-operational-zone attribution, shared by every Silver job that
carries coordinates.

Role-generic by construction: this module knows about *points*, *polygons* and
*zone labels*. Which dataset supplies the points, what the zone label is
called downstream, and which city it belongs to are the calling job's concern
(CLAUDE.md §城市无关护栏). Two jobs use it — the service-request build and the
address-snapshot build — and they must share one implementation, because
BO-4's spatial hit rate is only comparable if both counted the same way.

Pipeline order:
  1. add_point_coordinates — GeoJSON Point struct -> longitude / latitude
  2. build_zone_polygons  — collect the boundary table to a driver-side list
  3. assign_zone          — broadcast the polygons, join each point to a label

Attribution is a **three-value** outcome, never a bare NULL (see
``MATCH_STATUS_*`` below). A row without coordinates and a row whose
coordinates fall outside every polygon are different failures with different
owners, and collapsing them into NULL is what makes an alert denominator
wrong: 79% of upstream service requests carry no coordinates at all, so a
hit-rate measured over the whole table fires forever and gets muted. Alert on
the ``has_geo`` subset only (CLAUDE.md §"Escalate to human").

Invalid upstream polygons are repaired with ``make_valid`` rather than
dropped, matching spark/transforms/geography_boundary.py and the probe in
scripts/analysis/_probe_common.py — 8 of the deployed boundary polygons are
OGC-invalid, and dropping them would move the hit rate without anyone
knowing an edge had moved (ADR 0010 D6).
"""

from __future__ import annotations

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql.types import (
    BooleanType,
    DoubleType,
    StringType,
    StructField,
    StructType,
)

# A point carries usable coordinates and falls inside a polygon.
MATCH_STATUS_MATCHED = "matched"
# A point carries usable coordinates but falls outside every polygon. This is
# the only status that indicates a possible boundary or data problem.
MATCH_STATUS_UNMATCHED = "unmatched"
# The row carries no usable coordinates upstream. Expected and common; it is
# not a failure and must stay out of the hit-rate denominator.
MATCH_STATUS_NO_GEO = "no_geo"

_ZONE_ASSIGNMENT_SCHEMA = StructType(
    [
        StructField("has_geo", BooleanType(), nullable=False),
        StructField("match_status", StringType(), nullable=False),
        StructField("zone", StringType(), nullable=True),
    ]
)

# One prepared r-tree per executor process, keyed by the broadcast payload's
# identity. Rebuilding the index per row would dominate the runtime; per
# partition would still rebuild it thousands of times over a decade backfill.
_INDEX_CACHE: dict[int, object] = {}


class _ZoneIndex:
    """Prepared point-in-polygon index over (zone_label, polygon_wkt) pairs.

    A zone label spans several disjoint polygons (82 pieces over 25 labels in
    the deployed instance), so the index maps *polygon* -> label and several
    entries legitimately share a label. Unioning by label first would be
    wrong: the r-tree wants the individual pieces, and a union would also
    merge away the invalidity that make_valid needs to see.
    """

    def __init__(self, polygons: list[tuple[str, str]]) -> None:
        from shapely import wkt as shapely_wkt  # deferred: executor-side import
        from shapely.prepared import prep
        from shapely.strtree import STRtree
        from shapely.validation import make_valid

        geometries, labels = [], []
        for label, polygon_wkt in polygons:
            if not polygon_wkt or label is None:
                continue
            geometry = shapely_wkt.loads(polygon_wkt)
            if not geometry.is_valid:
                geometry = make_valid(geometry)
            geometries.append(geometry)
            labels.append(label)

        self._tree = STRtree(geometries)
        self._prepared = [prep(g) for g in geometries]
        self._labels = labels

    def assign(self, longitude: float, latitude: float) -> str | None:
        """The label of the polygon containing the point, else ``None``."""
        from shapely.geometry import Point

        point = Point(longitude, latitude)
        # The r-tree returns bounding-box candidates; contains() is the real test.
        for index in self._tree.query(point):
            if self._prepared[index].contains(point):
                return self._labels[index]
        return None


def _get_index(polygons: list[tuple[str, str]]) -> _ZoneIndex:
    key = id(polygons)
    index = _INDEX_CACHE.get(key)
    if index is None:
        index = _ZoneIndex(polygons)
        _INDEX_CACHE[key] = index
    return index  # type: ignore[return-value]


def add_point_coordinates(
    df: DataFrame,
    geometry_field: str,
    longitude_col: str = "longitude",
    latitude_col: str = "latitude",
) -> DataFrame:
    """Extract lon/lat from a GeoJSON Point struct into two double columns.

    GeoJSON orders coordinates ``[longitude, latitude]`` — the opposite of how
    a coordinate pair is usually spoken. Getting this backwards puts every
    point in the Indian Ocean, which is at least loud; the reason it is worth
    a comment is that a *partially* swapped pipeline is not loud at all.

    A missing geometry yields NULL in both columns rather than failing:
    absent coordinates are the normal case for the service-request source, and
    assign_zone() turns them into ``no_geo``.
    """
    coordinates = F.col(f"{geometry_field}.coordinates")
    return df.withColumn(
        longitude_col, coordinates.getItem(0).cast(DoubleType())
    ).withColumn(latitude_col, coordinates.getItem(1).cast(DoubleType()))


def build_zone_polygons(
    boundary_df: DataFrame,
    zone_field: str,
    wkt_field: str = "geometry_wkt",
) -> list[tuple[str, str]]:
    """Collect the boundary table to a driver-side list of (label, WKT).

    Collecting to the driver is deliberate and safe at this size: the boundary
    table is one row per polygon (82 in the deployed instance, a few MB of
    WKT). It is then broadcast, so each executor holds one copy. The
    alternative — a spatial join in Spark — would need a geospatial extension
    the stack does not carry (ADR 0006 §9 lists what is deployed).
    """
    rows = boundary_df.select(zone_field, wkt_field).collect()
    return [(row[zone_field], row[wkt_field]) for row in rows if row[wkt_field]]


def assign_zone(
    df: DataFrame,
    zone_polygons: list[tuple[str, str]],
    longitude_col: str = "longitude",
    latitude_col: str = "latitude",
    zone_col: str = "zone",
    has_geo_col: str = "has_geo",
    match_status_col: str = "geo_match_status",
) -> DataFrame:
    """Attribute each row's point to a zone label.

    Adds three columns, named by the caller because the output column names
    are instance vocabulary while this module is generic:

    - ``has_geo_col``       — bool, whether usable coordinates were present
    - ``match_status_col``  — one of the three ``MATCH_STATUS_*`` values
    - ``zone_col``          — the label, set **iff** the status is ``matched``

    ``zone_col`` is NULL for both ``no_geo`` and ``unmatched``; the status
    column is what distinguishes them. Callers must not reconstruct the
    distinction from the label being NULL.

    Raises on an empty polygon list: silently marking every row ``unmatched``
    would look exactly like a boundary outage and would be discovered a whole
    backfill later.
    """
    if not zone_polygons:
        raise ValueError(
            "assign_zone() got no polygons — the boundary table is empty or "
            "the WKT column is all NULL. Refusing to attribute every row as "
            f"{MATCH_STATUS_UNMATCHED!r}, which would be indistinguishable "
            "from a real boundary outage."
        )

    broadcast_polygons = df.sparkSession.sparkContext.broadcast(zone_polygons)

    def _assign(longitude: float | None, latitude: float | None) -> tuple[bool, str, str | None]:
        if longitude is None or latitude is None:
            return (False, MATCH_STATUS_NO_GEO, None)
        label = _get_index(broadcast_polygons.value).assign(longitude, latitude)
        if label is None:
            return (True, MATCH_STATUS_UNMATCHED, None)
        return (True, MATCH_STATUS_MATCHED, label)

    assignment = F.udf(_assign, _ZONE_ASSIGNMENT_SCHEMA)(
        F.col(longitude_col), F.col(latitude_col)
    )
    return (
        df.withColumn("_zone_assignment", assignment)
        .withColumn(has_geo_col, F.col("_zone_assignment.has_geo"))
        .withColumn(match_status_col, F.col("_zone_assignment.match_status"))
        .withColumn(zone_col, F.col("_zone_assignment.zone"))
        .drop("_zone_assignment")
    )


def spatial_hit_rate(df: DataFrame, match_status_col: str = "geo_match_status") -> tuple[int, int]:
    """Return ``(matched, has_geo)`` — the hit rate's numerator and denominator.

    The denominator is the ``has_geo`` subset, not the row count. This
    function exists so that no job computes the ratio its own way: the
    measured baseline (134,123 / 134,258 = 99.9%) is only a threshold if
    everything measures it identically.
    """
    counts = (
        df.groupBy(match_status_col)
        .count()
        .rdd.map(lambda row: (row[match_status_col], row["count"]))
        .collectAsMap()
    )
    matched = counts.get(MATCH_STATUS_MATCHED, 0)
    return matched, matched + counts.get(MATCH_STATUS_UNMATCHED, 0)
