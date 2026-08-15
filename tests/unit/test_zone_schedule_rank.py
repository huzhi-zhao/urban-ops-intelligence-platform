"""Unit tests for the scheduling-rank cross-check.

Only the pure functions are covered — the fetch path talks to a live public API
and belongs in integration, not here. Geometry is synthetic: two unit squares,
so the expected assignment is arithmetic rather than a fixture of real polygons.
"""

from __future__ import annotations

import pytest

from scripts.analysis.zone_schedule_rank import assign_addresses_to_zones, pearson


def _square(x0: float, y0: float, size: float = 1.0) -> dict:
    """A GeoJSON polygon covering [x0, x0+size] x [y0, y0+size]."""
    return {
        "type": "Polygon",
        "coordinates": [[
            [x0, y0], [x0 + size, y0], [x0 + size, y0 + size], [x0, y0 + size], [x0, y0],
        ]],
    }


def _point(x: float, y: float) -> dict:
    return {"location": {"type": "Point", "coordinates": [x, y]}}


BOUNDARIES = [
    {"plow_zone": "A", "the_geom": _square(0, 0)},
    {"plow_zone": "B", "the_geom": _square(10, 10)},
    # A zone made of two disjoint pieces — the real boundary table has 82
    # polygons over 25 zone values, so multi-piece zones are the normal case.
    {"plow_zone": "A", "the_geom": _square(20, 20)},
]


def test_counts_points_per_zone_across_disjoint_pieces() -> None:
    addresses = [_point(0.5, 0.5), _point(20.5, 20.5), _point(10.5, 10.5)]

    counts, unassigned = assign_addresses_to_zones(BOUNDARIES, addresses)

    assert counts == {"A": 2, "B": 1}
    assert unassigned == 0


def test_point_outside_every_polygon_is_unassigned_not_dropped() -> None:
    counts, unassigned = assign_addresses_to_zones(BOUNDARIES, [_point(99, 99)])

    assert counts == {}
    assert unassigned == 1


def test_row_without_geometry_counts_as_unassigned() -> None:
    """The hit rate is a reported quantity; a missing point must not vanish."""
    addresses = [{"location": None}, {}, {"location": {"type": "Point", "coordinates": []}}]

    counts, unassigned = assign_addresses_to_zones(BOUNDARIES, addresses)

    assert counts == {}
    assert unassigned == 3


def test_boundary_row_without_geometry_is_skipped() -> None:
    boundaries = [*BOUNDARIES, {"plow_zone": "C", "the_geom": None}]

    counts, _ = assign_addresses_to_zones(boundaries, [_point(0.5, 0.5)])

    assert counts == {"A": 1}


def test_bounding_box_candidate_outside_the_polygon_is_rejected() -> None:
    """An r-tree hit is not containment: an L-shape's notch must not count."""
    ell = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [2, 0], [2, 1], [1, 1], [1, 2], [0, 2], [0, 0]]],
    }
    boundaries = [{"plow_zone": "L", "the_geom": ell}]

    # (1.5, 1.5) is inside the bounding box but inside the missing quadrant.
    counts, unassigned = assign_addresses_to_zones(boundaries, [_point(1.5, 1.5)])

    assert counts == {}
    assert unassigned == 1


@pytest.mark.parametrize(
    ("xs", "ys", "expected"),
    [
        ([1.0, 2.0, 3.0], [2.0, 4.0, 6.0], 1.0),
        ([1.0, 2.0, 3.0], [6.0, 4.0, 2.0], -1.0),
    ],
)
def test_pearson_on_exact_linear_relations(
    xs: list[float], ys: list[float], expected: float,
) -> None:
    assert pearson(xs, ys) == pytest.approx(expected)


def test_pearson_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError, match="zip"):
        pearson([1.0, 2.0], [1.0])
