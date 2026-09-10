"""`scripts/presentation/render_maps.py` — see design/20260906-final-deck-figure-slots.md §3.3.

These four SVGs are the only figures in the deck drawn from raw municipal geometry
rather than from an aggregate, and three things about them cannot be caught by
looking at the picture:

1. **A stretched or mis-scaled city still looks like a city.** Slide 16's whole
   argument is that two boundary systems cover the same ground, which is a lie if
   the halves are drawn at two scales.
2. **Slide 29's discipline is invisible when it is broken.** A zone with no plow
   schedule shaded in the palest band reads as "quietest here", and a legend that
   says "load" reads as a claim the project does not make.
3. **The slide-29 values must come from the good model version.** Lexical order
   picks the deliberately degraded `nomonth` control (CLAUDE.md, L3 §4.6), and a
   map drawn from it is a plausible wrong map.

Everything here runs on synthetic geometry: no frozen export, no network, and no
`var/` directory.
"""

from __future__ import annotations

import json
import math
import re

import pytest

from scripts.presentation.render_maps import (
    GOOD_MODEL_VERSION,
    SLIDE29_EVENT,
    Projector,
    Window,
    Zone,
    anchor_of,
    centroid_of,
    load_slide29_values,
    load_wards,
    load_zones,
    render_slide16_wards,
    render_slide16_zones,
    render_slide18,
    render_slide29,
    rings_of,
    spread,
    window_of,
    wkt_to_rings,
)

# A square "city" near Winnipeg's latitude, so the cos(lat) correction is exercised
# with the same magnitude the real figures see.
LAT = 49.9


def square(x: float, y: float, size: float) -> list[tuple[float, float]]:
    return [(x, y), (x + size, y), (x + size, y + size), (x, y + size)]


def wkt_square(x: float, y: float, size: float) -> str:
    ring = square(x, y, size) + [(x, y)]
    return "MULTIPOLYGON(((" + ",".join(f"{a} {b}" for a, b in ring) + ")))"


def zone(name: str, x: float, *, scheduled: bool = True, size: float = 0.05) -> Zone:
    wkt = wkt_square(x, LAT, size)
    return Zone(
        plow_zone=name,
        has_plow_schedule=scheduled,
        rings=wkt_to_rings(wkt),
        anchor=anchor_of(wkt),
    )


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def test_make_valid_leftovers_are_not_part_of_a_zone_shape():
    """8 of the 25 zones were repaired by `make_valid`, which leaves bare lines and
    points in the collection. They enclose no area, so they are not the zone."""
    rings = rings_of(
        {
            "type": "GeometryCollection",
            "geometries": [
                {"type": "Polygon", "coordinates": [[*square(0, LAT, 0.1), (0, LAT)]]},
                {"type": "LineString", "coordinates": [(0, LAT), (0.1, LAT)]},
                {"type": "Point", "coordinates": (0, LAT)},
            ],
        }
    )
    assert len(rings) == 1


def test_a_multipolygon_flattens_to_one_ring_per_part():
    rings = rings_of(
        {
            "type": "MultiPolygon",
            "coordinates": [
                [[*square(0, LAT, 0.1), (0, LAT)]],
                [[*square(1, LAT, 0.1), (1, LAT)]],
            ],
        }
    )
    assert len(rings) == 2


def test_an_unsupported_geometry_type_raises_rather_than_drawing_nothing():
    with pytest.raises(ValueError, match="unsupported geometry type"):
        rings_of({"type": "Sphere", "coordinates": []})


def test_wkt_to_rings_reads_the_form_trino_emits():
    rings = wkt_to_rings(wkt_square(0, LAT, 0.1))
    assert len(rings) == 1
    assert len(rings[0]) >= 4


def test_window_of_refuses_an_empty_geometry():
    with pytest.raises(ValueError, match="no coordinates"):
        window_of([])


# ---------------------------------------------------------------------------
# Projection — a stretched city is a wrong city
# ---------------------------------------------------------------------------


def test_the_projector_letterboxes_instead_of_stretching():
    window = Window(0.0, LAT, 0.1, LAT + 0.1)
    project = Projector(window, 1000.0, 400.0)

    left = project(window.min_lon, window.max_lat)
    right = project(window.max_lon, window.max_lat)
    bottom = project(window.min_lon, window.min_lat)

    drawn_width = right[0] - left[0]
    drawn_height = bottom[1] - left[1]
    # One scale on both axes, with the cos(latitude) correction and nothing else:
    # a degree of longitude here is 0.644 of a degree of latitude.
    expected = math.cos(math.radians(window.mid_lat))
    assert drawn_width / drawn_height == pytest.approx(expected, rel=1e-9)
    # ...and the map is centred in the leftover width, not pinned to one edge.
    assert left[0] == pytest.approx(1000.0 - right[0], rel=1e-9)


def test_latitude_grows_upward_and_svg_y_grows_downward():
    project = Projector(Window(0.0, LAT, 0.1, LAT + 0.1), 400.0, 400.0)
    assert project(0.0, LAT + 0.1)[1] < project(0.0, LAT)[1]


def test_widening_a_window_only_ever_adds_width():
    window = Window(0.0, LAT, 0.1, LAT + 0.1)
    wider = window.widen_to(3.0)
    assert wider.max_lon - wider.min_lon > window.max_lon - window.min_lon
    assert (wider.min_lat, wider.max_lat) == (window.min_lat, window.max_lat)
    # Already wide enough: leave it alone rather than cropping the city.
    assert window.widen_to(0.1) is window


def test_a_label_anchor_lands_inside_the_largest_part_not_between_the_parts():
    """Zones are scattered — V is six pieces. An area-weighted centroid of all the
    parts lands in the gap between them, outside the shape it labels."""
    small = "((" + ",".join(f"{a} {b}" for a, b in square(0.0, LAT, 0.01) + [(0.0, LAT)]) + "))"
    big = "((" + ",".join(f"{a} {b}" for a, b in square(1.0, LAT, 0.20) + [(1.0, LAT)]) + "))"
    x, y = anchor_of(f"MULTIPOLYGON({small},{big})")
    assert 1.0 < x < 1.2
    assert LAT < y < LAT + 0.2


def test_an_anchor_for_a_geometry_with_no_area_raises():
    with pytest.raises(ValueError, match="no polygon with area"):
        anchor_of("LINESTRING(0 49.9, 1 49.9)")


def test_the_centroid_of_a_square_is_its_middle():
    x, y = centroid_of([square(0.0, LAT, 0.10)])
    assert x == pytest.approx(0.05)
    assert y == pytest.approx(LAT + 0.05)


def test_a_degenerate_ring_falls_back_to_the_mean_instead_of_dividing_by_zero():
    x, y = centroid_of([[(0.0, LAT), (0.0, LAT), (0.0, LAT)]])
    assert (x, y) == pytest.approx((0.0, LAT))


def test_spread_pushes_two_stacked_labels_apart_and_leaves_the_rest_alone():
    moved = spread([(100.0, 100.0), (100.0, 104.0), (400.0, 400.0)], gap=34.0)
    apart = ((moved[0][0] - moved[1][0]) ** 2 + (moved[0][1] - moved[1][1]) ** 2) ** 0.5
    assert apart == pytest.approx(34.0, abs=0.5)
    assert moved[2] == pytest.approx((400.0, 400.0))


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


def _write_export(tmp_path, fig_id: str, columns: list[str], rows: list[list]) -> None:
    (tmp_path / f"{fig_id}.json").write_text(
        json.dumps(
            {
                "columns": columns,
                "rows": rows,
                "certification": {"run_id": "dq-test-000000"},
            }
        ),
        encoding="utf-8",
    )


def test_zone_geometry_is_read_with_its_certification_run(tmp_path):
    _write_export(
        tmp_path,
        "FIG-BO4-00",
        ["plow_zone", "has_plow_schedule", "geometry_wkt"],
        [["V", True, wkt_square(0.0, LAT, 0.1)], ["X", False, wkt_square(0.2, LAT, 0.1)]],
    )
    zones, run = load_zones(tmp_path)
    assert [z.plow_zone for z in zones] == ["V", "X"]
    assert [z.has_plow_schedule for z in zones] == [True, False]
    assert run == "dq-test-000000"


def test_slide_29_reads_the_good_model_version_and_not_the_degraded_control(tmp_path):
    """Lexical order picks `nomonth`, which was trained badly on purpose."""
    _write_export(
        tmp_path,
        "FIG-BO1-03",
        ["model_version", "snowfall_event_id", "plow_zone", "predicted_count"],
        [
            [GOOD_MODEL_VERSION, SLIDE29_EVENT, "V", 159.4],
            ["m1-poisson-nomonth", SLIDE29_EVENT, "V", 9.0],
            [GOOD_MODEL_VERSION, "SNOW-19990101", "V", 1.0],
        ],
    )
    values, _ = load_slide29_values(tmp_path)
    assert values == {"V": pytest.approx(159.4)}


def test_a_slide_29_export_without_the_chosen_event_raises(tmp_path):
    _write_export(
        tmp_path,
        "FIG-BO1-03",
        ["model_version", "snowfall_event_id", "plow_zone", "predicted_count"],
        [["m1-poisson-nomonth", SLIDE29_EVENT, "V", 9.0]],
    )
    with pytest.raises(ValueError, match="check the export"):
        load_slide29_values(tmp_path)


def test_the_ward_outline_is_read_from_a_plain_geojson_file(tmp_path):
    path = tmp_path / "wards.geojson"
    path.write_text(
        json.dumps(
            {
                "features": [
                    {
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[*square(0.0, LAT, 0.1), (0.0, LAT)]],
                        }
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert len(load_wards(path)) == 1


# ---------------------------------------------------------------------------
# The drawings themselves
# ---------------------------------------------------------------------------


def _zones() -> list[Zone]:
    return [zone("V", 0.0), zone("S", 0.2), zone("X", 0.4, scheduled=False)]


def _wards() -> list[list[list[tuple[float, float]]]]:
    return [[square(0.0, LAT, 0.3)], [square(0.3, LAT, 0.3)]]


def _shared_window(zones: list[Zone], wards) -> Window:
    return window_of([r for z in zones for r in z.rings] + [r for w in wards for r in w])


def test_both_halves_of_slide_16_are_drawn_at_one_scale():
    """The slide's claim is that the two boundary systems cover the same ground.
    Two canvases of different size would make that comparison a lie about scale."""
    zones, wards = _zones(), _wards()
    window = _shared_window(zones, wards)
    left = render_slide16_wards(wards, window)
    right = render_slide16_zones(zones, window)

    def size(svg: str) -> tuple[str, str]:
        return (
            re.search(r'width="(\d+)"', svg).group(1),
            re.search(r'height="(\d+)"', svg).group(1),
        )

    assert size(left) == size(right)


def test_slide_18_draws_wards_as_backdrop_and_never_fills_them():
    """Ten similar fills would ask 'which ten wards'. The claim is 'ten'."""
    zones, wards = _zones(), _wards()
    svg = render_slide18(zones, wards, _shared_window(zones, wards))
    filled = re.findall(r'<path [^>]*fill="(?!none)([^"]+)"', svg)
    # Exactly one filled path: zone V itself.
    assert len(filled) == 1


def test_a_zone_with_no_plow_schedule_is_outlined_and_carries_no_number():
    """The palest band would read 'quietest here'; it means 'no schedule data'."""
    zones = _zones()
    svg = render_slide29(zones, {"V": 159.4, "S": 42.0})
    # stroke-width 1.4 is the zone outline; the legend key is dashed too, at 2.
    outlines = re.findall(
        r'<path [^>]*fill="none"[^>]*stroke-width="1.4"[^>]*stroke-dasharray[^>]*/>', svg
    )
    assert len(outlines) == 1
    # Two shaded zones, so two printed counts — the unscheduled one gets none.
    assert len(re.findall(r'>\d+</text>', svg)) == 2


def test_the_slide_29_legend_never_says_load_or_priority():
    svg = render_slide29(_zones(), {"V": 159.4, "S": 42.0})
    assert "estimated winter-related" in svg
    assert "resident reports" in svg
    lowered = svg.lower()
    assert "priority" not in lowered
    assert "load" not in lowered


def test_every_map_is_self_contained():
    """Same rule as the HTML figures: nothing may be fetched when it is looked at."""
    zones, wards = _zones(), _wards()
    window = _shared_window(zones, wards)
    for svg in (
        render_slide16_wards(wards, window),
        render_slide16_zones(zones, window),
        render_slide18(zones, wards, window),
        render_slide29(zones, {"V": 159.4, "S": 42.0}),
    ):
        # The SVG namespace is a URI, not a fetch — everything else is suspect.
        body = svg.replace('xmlns="http://www.w3.org/2000/svg"', "")
        assert "http" not in body
        assert "<image" not in body
        assert "<script" not in svg
