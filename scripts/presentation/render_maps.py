"""Render the deck's four map slots as standalone SVG, from frozen exports only.

Three slides need a drawing of real municipal geometry: slide 16 (two outline maps
side by side), slide 18 (one zone with ward lines through it) and slide 29 (a
choropleth of estimated winter resident reports). Hand-drawing any of them would put
a *wrong map of a real city* in front of an audience that lives in it, so every
shape here comes from geometry and nothing is drawn by eye.

Two geometry sources, and the distinction is load-bearing:

* **Plow zones** are ours — ``geometry_wkt`` in ``FIG-BO4-00.json``, frozen under a
  certification run like every other figure in the deck.
* **Wards have no geometry anywhere in the warehouse.** ``dim_admin_label`` holds
  ward *names* taken from resident-entered 311 text and no shapes at all, and
  ``dim_region_crosswalk`` counts requests rather than intersecting polygons. The
  ward outline is therefore an **outside illustration** (City of Winnipeg open data,
  dataset ``t4cg-yaxs``), carried here as a local file and used for shape only.
  🔴 No number in the deck is derived from it, and nothing is joined to it.

Both sources are EPSG:4326 and their bounding boxes agree to four decimals, so the
two halves of slide 16 line up without adjustment as long as every figure is
projected with the same window — which is what ``--shared-window`` does.

Output is SVG. Insert it through PowerPoint's own Insert → Picture so PowerPoint
writes the PNG fallback: an ``<a:blip>`` carrying only ``asvg:svgBlip`` renders as a
blank slide, silently, on any viewer that does not know the extension.

Usage::

    python -m scripts.presentation.render_maps --out var/presentation/maps
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Palette lifted from docs/images/platform-architecture.drawio.xml so the maps sit
# in the same visual family as the architecture diagram on slide 35.
INK = "#3D3D3A"
MUTED = "#6E6D66"
HAIRLINE = "#A8A79E"
PAPER = "#FBFAF8"
ACCENT = "#185FA5"
ACCENT_DARK = "#0C447C"
# Single-hue ramp, pale → deep, for slide 29. Deliberately one hue: a diverging or
# multi-hue ramp reads as categories, and these are counts.
RAMP = ("#EAF2FB", "#CFE2F5", "#AECEEE", "#89B6E4", "#5F98D6", "#3B7CC4", "#185FA5")

DEFAULT_JSON_DIR = Path("var/presentation/outputjson")
DEFAULT_WARDS = Path("var/presentation/maps/wards-t4cg-yaxs.geojson")

# The model version to read for slide 29. The other version in FIG-BO1-03 is the
# deliberately degraded `nomonth` control, and lexical order picks *that* one — see
# CLAUDE.md, L3 §4.6.
GOOD_MODEL_VERSION = "m1-poisson-20260822-df31d954"

# The only hold-out event with a range wide enough to shade: predicted 9.0–159.4
# across 22 zones (sum 1546 against an actual 1547). The other six span roughly
# 0.4–7.0 and would print 22 near-identical numbers on a flat map.
SLIDE29_EVENT = "SNOW-20251218"

ZONE_V = "V"


# ---------------------------------------------------------------------------
# Geometry: rings in, rings out. No projection library — at this extent a
# cos(latitude) correction on x is the whole of it, and adding pyproj/geopandas
# for that would be a dependency the pipeline never needs.
# ---------------------------------------------------------------------------

Ring = list[tuple[float, float]]


@dataclass(frozen=True)
class Window:
    """The lon/lat box every figure is projected through."""

    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    @property
    def mid_lat(self) -> float:
        return (self.min_lat + self.max_lat) / 2

    def pad(self, fraction: float) -> Window:
        dx = (self.max_lon - self.min_lon) * fraction
        dy = (self.max_lat - self.min_lat) * fraction
        return Window(
            self.min_lon - dx, self.min_lat - dy, self.max_lon + dx, self.max_lat + dy
        )

    def widen_to(self, aspect: float) -> Window:
        """Grow the window sideways until it matches ``aspect`` (width / height).

        Used only where the extra width shows more city — slide 18 zooms one zone,
        so widening its window brings in the surrounding wards, which is the point
        of that slide. For a whole-city map the extra width would be blank paper,
        so those figures keep their natural shape and get centred in the frame
        instead.
        """
        kx = math.cos(math.radians(self.mid_lat))
        height = self.max_lat - self.min_lat
        want = height * aspect / kx
        have = self.max_lon - self.min_lon
        if want <= have:
            return self
        grow = (want - have) / 2
        return Window(self.min_lon - grow, self.min_lat, self.max_lon + grow, self.max_lat)

    def canvas(self, height: float) -> tuple[float, float]:
        """Pixel size of a canvas that fits this window exactly, at ``height`` px."""
        kx = math.cos(math.radians(self.mid_lat))
        span_x = (self.max_lon - self.min_lon) * kx
        span_y = self.max_lat - self.min_lat
        return height * span_x / span_y, height


def window_of(rings: Iterable[Ring]) -> Window:
    xs: list[float] = []
    ys: list[float] = []
    for ring in rings:
        for x, y in ring:
            xs.append(x)
            ys.append(y)
    if not xs:
        raise ValueError("no coordinates to take a window from")
    return Window(min(xs), min(ys), max(xs), max(ys))


class Projector:
    """Equirectangular, aspect-preserving, letterboxed into a fixed canvas.

    The canvas aspect comes from the pptx frame it has to sit in, so the map is
    centred rather than stretched — a stretched city is a wrong city.
    """

    def __init__(self, window: Window, width: float, height: float) -> None:
        self.window = window
        self.width = width
        self.height = height
        kx = math.cos(math.radians(window.mid_lat))
        span_x = (window.max_lon - window.min_lon) * kx
        span_y = window.max_lat - window.min_lat
        self.scale = min(width / span_x, height / span_y)
        self._kx = kx
        self._off_x = (width - span_x * self.scale) / 2
        self._off_y = (height - span_y * self.scale) / 2

    def __call__(self, lon: float, lat: float) -> tuple[float, float]:
        x = (lon - self.window.min_lon) * self._kx * self.scale + self._off_x
        # SVG y grows downward; latitude grows upward.
        y = (self.window.max_lat - lat) * self.scale + self._off_y
        return x, y


def rings_of(geometry: dict[str, Any]) -> list[Ring]:
    """Flatten a GeoJSON-style geometry to a list of rings (outer and inner alike)."""
    kind = geometry["type"]
    if kind == "GeometryCollection":
        # Trino's ST_ output wraps the zone multipolygons in a collection.
        out: list[Ring] = []
        for member in geometry["geometries"]:
            out.extend(rings_of(member))
        return out
    if kind in {"LineString", "MultiLineString", "Point", "MultiPoint"}:
        # `make_valid` turns a zero-width sliver in an invalid polygon into a bare
        # line or point and leaves it in the collection. It encloses no area, so it
        # is not part of the zone's shape — 8 of the 25 zones were repaired this way
        # (`geometry_repaired = true`), and the recorded `area_delta_pct` is ≤0.005%.
        return []
    coords = geometry["coordinates"]
    if kind == "Polygon":
        polygons = [coords]
    elif kind == "MultiPolygon":
        polygons = coords
    else:
        raise ValueError(f"unsupported geometry type: {kind}")
    return [[(float(x), float(y)) for x, y, *_ in ring] for poly in polygons for ring in poly]


def wkt_to_rings(text: str) -> list[Ring]:
    """Parse the MULTIPOLYGON / GEOMETRYCOLLECTION forms Trino emits.

    shapely is already a dependency of the probe layer, so this is a thin adapter
    rather than a parser.
    """
    from shapely import wkt as shapely_wkt
    from shapely.geometry import mapping

    return rings_of(mapping(shapely_wkt.loads(text)))


def path_of(rings: Sequence[Ring], project: Projector) -> str:
    parts: list[str] = []
    for ring in rings:
        if len(ring) < 3:
            continue
        points = [project(lon, lat) for lon, lat in ring]
        head = f"M{points[0][0]:.2f},{points[0][1]:.2f}"
        tail = "".join(f"L{x:.2f},{y:.2f}" for x, y in points[1:])
        parts.append(head + tail + "Z")
    return "".join(parts)


def anchor_of(wkt_text: str) -> tuple[float, float]:
    """A lon/lat point guaranteed to sit inside the zone's *largest* part.

    Zones are multipolygons — 82 polygons across 25 zones — and several are split
    across the city. An area-weighted centroid of all the parts lands between them,
    which is how zone numbers ended up stacked on each other and sometimes outside
    the shape they label. Taking a representative point of the biggest part instead
    puts every number inside the piece a reader would call that zone.
    """
    from shapely import wkt as shapely_wkt

    def polygons(geometry: Any) -> list[Any]:
        # A collection can nest: GEOMETRYCOLLECTION of MULTIPOLYGON of POLYGON, plus
        # the zero-area leftovers `make_valid` drops in. Recurse, keep real area.
        if geometry.geom_type == "Polygon":
            return [geometry] if geometry.area > 0 else []
        if hasattr(geometry, "geoms"):
            return [p for member in geometry.geoms for p in polygons(member)]
        return []

    parts = polygons(shapely_wkt.loads(wkt_text))
    if not parts:
        raise ValueError("geometry has no polygon with area")
    biggest = max(parts, key=lambda p: p.area)
    point = biggest.representative_point()
    return point.x, point.y


def spread(points: list[tuple[float, float]], gap: float, rounds: int = 240) -> list[tuple[float, float]]:
    """Nudge overlapping labels apart, keeping them near where they started.

    Purely cosmetic and deliberately weak: a few pixels of separation so two numbers
    can both be read. It is not a label-placement solver, and a label that has to
    travel far to stop overlapping is a sign the figure is too small, not something
    to fix by moving it further.
    """
    out = [list(p) for p in points]
    for _ in range(rounds):
        moved = False
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                dx = out[j][0] - out[i][0]
                dy = out[j][1] - out[i][1]
                distance = math.hypot(dx, dy)
                if distance >= gap:
                    continue
                if distance < 1e-6:
                    dx, dy, distance = 0.0, 1.0, 1.0
                push = (gap - distance) / 2
                ux, uy = dx / distance, dy / distance
                out[i][0] -= ux * push
                out[i][1] -= uy * push
                out[j][0] += ux * push
                out[j][1] += uy * push
                moved = True
        if not moved:
            break
    return [(x, y) for x, y in out]


def centroid_of(rings: Sequence[Ring]) -> tuple[float, float]:
    """Area-weighted centroid over outer rings, so a label lands inside the shape.

    A bounding-box centre puts zone labels in the notch of the several L-shaped
    zones; the shoelace centroid does not.
    """
    total = 0.0
    cx = 0.0
    cy = 0.0
    for ring in rings:
        if len(ring) < 3:
            continue
        area2 = 0.0
        rx = 0.0
        ry = 0.0
        for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1], strict=True):
            cross = x0 * y1 - x1 * y0
            area2 += cross
            rx += (x0 + x1) * cross
            ry += (y0 + y1) * cross
        if area2 == 0:
            continue
        total += area2
        cx += rx
        cy += ry
    if total == 0:
        flat = [p for ring in rings for p in ring]
        return (
            sum(p[0] for p in flat) / len(flat),
            sum(p[1] for p in flat) / len(flat),
        )
    return cx / (3 * total), cy / (3 * total)


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


def load_figure(json_dir: Path, fig_id: str) -> tuple[list[str], list[list[Any]], str]:
    payload = json.loads((json_dir / f"{fig_id}.json").read_text(encoding="utf-8"))
    certification = payload.get("certification") or {}
    run = certification.get("run_id") or certification.get("certification_run_id") or ""
    return payload["columns"], payload["rows"], str(run)


@dataclass(frozen=True)
class Zone:
    plow_zone: str
    has_plow_schedule: bool
    rings: list[Ring]
    anchor: tuple[float, float]


def load_zones(json_dir: Path) -> tuple[list[Zone], str]:
    columns, rows, run = load_figure(json_dir, "FIG-BO4-00")
    ix = {name: n for n, name in enumerate(columns)}
    zones = [
        Zone(
            plow_zone=row[ix["plow_zone"]],
            has_plow_schedule=bool(row[ix["has_plow_schedule"]]),
            rings=wkt_to_rings(row[ix["geometry_wkt"]]),
            anchor=anchor_of(row[ix["geometry_wkt"]]),
        )
        for row in rows
    ]
    return zones, run


def load_wards(path: Path) -> list[list[Ring]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [rings_of(feature["geometry"]) for feature in payload["features"]]


def load_slide29_values(json_dir: Path) -> tuple[dict[str, float], str]:
    columns, rows, run = load_figure(json_dir, "FIG-BO1-03")
    ix = {name: n for n, name in enumerate(columns)}
    values = {
        row[ix["plow_zone"]]: float(row[ix["predicted_count"]])
        for row in rows
        if row[ix["model_version"]] == GOOD_MODEL_VERSION
        and row[ix["snowfall_event_id"]] == SLIDE29_EVENT
    }
    if not values:
        raise ValueError(
            f"no rows for {SLIDE29_EVENT} under {GOOD_MODEL_VERSION} — check the export"
        )
    return values, run


# ---------------------------------------------------------------------------
# SVG
# ---------------------------------------------------------------------------


def svg_document(width: float, height: float, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" '
        f'height="{height:.0f}" viewBox="0 0 {width:.0f} {height:.0f}">'
        f'<rect width="{width:.0f}" height="{height:.0f}" fill="{PAPER}"/>'
        f'<g font-family="Calibri,Helvetica,Arial,sans-serif">{body}</g></svg>'
    )


def text(x: float, y: float, label: str, size: float, fill: str, weight: str = "400") -> str:
    return (
        f'<text x="{x:.2f}" y="{y:.2f}" font-size="{size:.1f}" fill="{fill}" '
        f'font-weight="{weight}" text-anchor="middle" '
        f'dominant-baseline="central">{label}</text>'
    )


def outline_map(
    shapes: Sequence[Sequence[Ring]],
    window: Window,
    width: float,
    height: float,
    stroke: str,
    stroke_width: float,
) -> str:
    project = Projector(window, width, height)
    return "".join(
        f'<path d="{path_of(rings, project)}" fill="none" stroke="{stroke}" '
        f'stroke-width="{stroke_width}" stroke-linejoin="round"/>'
        for rings in shapes
    )


def render_slide16_wards(wards: list[list[Ring]], window: Window) -> str:
    # Natural map shape, not the frame's: the frame is 5.36 x 2.20 in and the city is
    # nearly square, so a frame-shaped canvas would be two thirds blank paper baked
    # into the image. Centre it in the frame by hand instead.
    width, height = window.canvas(440.0)
    # Darker than the ward lines on slide 18: there they are backdrop, here they are
    # the subject of one half of a comparison and have to hold up on a projector.
    return svg_document(
        width, height, outline_map(wards, window, width, height, MUTED, 1.8)
    )


def render_slide16_zones(zones: list[Zone], window: Window) -> str:
    width, height = window.canvas(440.0)
    project = Projector(window, width, height)
    body = outline_map([z.rings for z in zones], window, width, height, ACCENT, 1.6)
    # A handful of letters only. Labelling all 25 at this size is unreadable and the
    # slide's claim is "different shapes", not "which zone is which".
    for zone in zones:
        if zone.plow_zone not in {"A", "H", "N", "S", "V"}:
            continue
        x, y = project(*zone.anchor)
        body += text(x, y, zone.plow_zone, 15, ACCENT_DARK, "700")
    return svg_document(width, height, body)


def render_slide18(zones: list[Zone], wards: list[list[Ring]], window: Window) -> str:
    """Zone V against the ward boundaries — city-wide, not a zoom.

    🔴 This slide cannot be the close-up crop the deck originally specified.
    **Zone V is six disconnected pieces and its largest piece is 25.6% of its area**,
    so its bounding box already covers most of the city: a "zoom" is a whole-city
    view with a few blue fragments in it, and the label lands on a quarter of the
    zone. Nearly every zone is like this — X has 8 parts, A and B and C and E have 7.

    Drawn city-wide the slide's own claim gets easier to see rather than harder:
    the reason no ward contains V is that V is scattered across the city, which a
    crop would have hidden.
    """
    width, height = window.canvas(812.0)  # frame is 7.60 x 4.06 in; centre it
    project = Projector(window, width, height)
    zone_v = next(z for z in zones if z.plow_zone == ZONE_V)
    # Ward lines in grey and first: they are the backdrop, not the subject. Ten
    # near-identical fills would ask "which ten wards", and the claim is "ten".
    body = "".join(
        f'<path d="{path_of(rings, project)}" fill="none" stroke="{HAIRLINE}" '
        f'stroke-width="1.6" stroke-linejoin="round"/>'
        for rings in wards
    )
    body += (
        f'<path d="{path_of(zone_v.rings, project)}" fill="{ACCENT}" fill-opacity="0.85" '
        f'fill-rule="evenodd" stroke="{ACCENT_DARK}" stroke-width="2.5" '
        f'stroke-linejoin="round"/>'
    )
    x, y = project(*zone_v.anchor)
    body += (
        f'<text x="{x:.2f}" y="{y:.2f}" font-size="42" fill="{ACCENT_DARK}" '
        f'font-weight="700" text-anchor="middle" dominant-baseline="central" '
        f'stroke="{PAPER}" stroke-width="7" paint-order="stroke" '
        f'stroke-linejoin="round">V</text>'
    )
    return svg_document(width, height, body)


def render_slide29(zones: list[Zone], values: dict[str, float]) -> str:
    width, height = 1540.0, 812.0  # 7.70 x 4.06 in frame
    # The city is nearly square in a frame nearly twice as wide, so the map takes the
    # left and the legend becomes a vertical block in the width that is left over,
    # rather than a strip under a map floating in blank paper.
    window = window_of([r for z in zones for r in z.rings]).pad(0.02)
    map_width, map_height = window.canvas(height - 24)
    project = Projector(window, map_width, map_height)
    shaded = list(values.values())
    lo, hi = min(shaded), max(shaded)

    def band(value: float) -> str:
        if hi == lo:
            return RAMP[0]
        step = (value - lo) / (hi - lo)
        return RAMP[min(len(RAMP) - 1, int(step * len(RAMP)))]

    body = ""
    placed: list[tuple[tuple[float, float], float, str]] = []
    for zone in zones:
        d = path_of(zone.rings, project)
        value = values.get(zone.plow_zone)
        if value is None or not zone.has_plow_schedule:
            # 🔴 Outline only. The palest band would read "quietest here" when it
            # means "this zone has no plow schedule at all".
            body += (
                f'<path d="{d}" fill="none" stroke="{HAIRLINE}" stroke-width="1.4" '
                f'stroke-dasharray="5 4" stroke-linejoin="round"/>'
            )
            continue
        body += (
            f'<path d="{d}" fill="{band(value)}" stroke="{PAPER}" stroke-width="1.2" '
            f'stroke-linejoin="round"/>'
        )
        placed.append((project(*zone.anchor), value, band(value)))

    # 🔴 The printed number is the whole reason this map is allowed to exist: a
    # light-to-dark city map is the visual grammar of a priority map, and the numbers
    # make it read as a data table laid on a map instead of a heat surface.
    labels = ""
    for (x, y), value, colour in zip(
        spread([p for p, _, _ in placed], gap=34.0),
        [v for _, v, _ in placed],
        [c for _, _, c in placed],
        strict=True,
    ):
        ink = "#FFFFFF" if colour in RAMP[-2:] else ACCENT_DARK
        labels += (
            f'<text x="{x:.2f}" y="{y:.2f}" font-size="19" fill="{ink}" '
            f'font-weight="700" text-anchor="middle" dominant-baseline="central" '
            f'stroke="{PAPER}" stroke-width="3.5" paint-order="stroke" '
            f'stroke-linejoin="round">{value:.0f}</text>'
        )

    left = map_width + 56
    top = 150.0
    swatch = 34.0

    def line(y: float, label: str, size: float, fill: str, weight: str = "400") -> str:
        return (
            f'<text x="{left:.1f}" y="{y:.1f}" font-size="{size:.1f}" fill="{fill}" '
            f'font-weight="{weight}" dominant-baseline="central">{label}</text>'
        )

    # 🔴 The legend wording is load-bearing and decided: never "load", never
    # "priority". This figure estimates resident reports and nothing else.
    body += line(top, "estimated winter-related", 25, INK, "700")
    body += line(top + 32, "resident reports", 25, INK, "700")
    for n, colour in enumerate(RAMP):
        body += (
            f'<rect x="{left + n * swatch:.1f}" y="{top + 66:.1f}" '
            f'width="{swatch:.1f}" height="20" fill="{colour}"/>'
        )
    body += line(top + 106, f"{lo:.0f} &#8594; {hi:.0f} per zone", 20, MUTED)
    body += line(top + 168, "one snowfall event", 20, MUTED)
    body += line(top + 196, f"{SLIDE29_EVENT}", 20, MUTED, "700")

    dash_y = top + 264
    body += (
        f'<path d="M{left:.1f},{dash_y:.1f}h{swatch * 2:.1f}" fill="none" '
        f'stroke="{HAIRLINE}" stroke-width="2" stroke-dasharray="5 4"/>'
    )
    for n, part in enumerate(("no plow schedule —", "outlined, not shaded,", "no number")):
        body += line(dash_y + 30 + n * 26, part, 19, MUTED)
    return svg_document(width, height, body + labels)


# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-dir", type=Path, default=DEFAULT_JSON_DIR)
    parser.add_argument("--wards", type=Path, default=DEFAULT_WARDS)
    parser.add_argument("--out", type=Path, default=Path("var/presentation/maps"))
    args = parser.parse_args()

    zones, zone_run = load_zones(args.json_dir)
    wards = load_wards(args.wards)
    values, value_run = load_slide29_values(args.json_dir)

    # Slide 16's two halves must share one window or the comparison is a lie about
    # scale. The two sources' extents agree to four decimals, so the union is a
    # formality — but it is the formality that makes it true rather than lucky.
    shared = window_of(
        [r for z in zones for r in z.rings] + [r for w in wards for r in w]
    ).pad(0.02)

    args.out.mkdir(parents=True, exist_ok=True)
    written = {
        "slide-16-left_wards-outline.svg": render_slide16_wards(wards, shared),
        "slide-16-right_plow-zones-outline.svg": render_slide16_zones(zones, shared),
        "slide-18_zone-v-with-ward-lines.svg": render_slide18(zones, wards, shared),
        "slide-29_estimated-reports-choropleth.svg": render_slide29(zones, values),
    }
    for name, markup in written.items():
        (args.out / name).write_text(markup, encoding="utf-8")
        print(f"wrote {args.out / name}")

    print(f"\nzone geometry frozen under : {zone_run}")
    print(f"slide 29 values frozen under: {value_run}")
    print(f"slide 29 event              : {SLIDE29_EVENT} / {GOOD_MODEL_VERSION}")
    print(f"shaded zones                : {len(values)}")
    print(f"outline-only zones          : {sum(1 for z in zones if not z.has_plow_schedule)}")
    print(
        "\nward outline is an outside illustration (data.winnipeg.ca t4cg-yaxs); "
        "no number in the deck comes from it."
    )
    print(
        "Insert through PowerPoint's Insert -> Picture so a PNG fallback is written."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
