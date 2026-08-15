"""ADR 0009 blocking item: what is ``B/D``?

Three ``plow_zone`` values have a boundary but no shift record: ``B/D``
(11,150 addresses), ``X`` (2,590) and ``Downtown`` (574). ADR 0009 §3.3
upgraded ``B/D`` specifically from an open question to a **blocking item**:
statistically it is one row in the 22-zone panel now, so guessing wrong about
what it is corrupts both the M1 training set and the BO-6 rank factor for
whichever zone(s) it should have been merged into.

Two competing explanations, both raised in ADR 0008 §3 and never adjudicated:

    (a) legacy label   the boundary table's ``B/D`` is a stale merge of two
                        polygons that were once one zone, and its addresses
                        rightly belong split between ``B`` and ``D``
    (b) real area       ``B/D`` is a genuine polygon that the city's
                        residential plow program does not cover at all — same
                        status as ``X`` and ``Downtown``

This probe distinguishes them with two independent signals against public
data only, no guessing from the label string:

    adjacency     does the ``B/D`` polygon touch ``B``'s polygons, ``D``'s,
                  both, or neither? A stale split-merge should touch (or
                  nearly touch) at least one of its namesakes.
    address density
                  addresses per km2 inside ``B/D`` versus the median across
                  scheduled zones. A genuine residential zone omitted by
                  clerical error should look like its neighbours; industrial
                  or park land that legitimately sits outside the program
                  should not.

Usage::

    uv run python -m scripts.analysis.zone_bd_investigation
    uv run python -m scripts.analysis.zone_bd_investigation --json out.json

Read-only against public APIs. Writes nothing to object storage.
"""

from __future__ import annotations

import argparse
import logging
import math
from collections import defaultdict

from shapely.geometry import Point, shape
from shapely.prepared import prep
from shapely.strtree import STRtree
from shapely.validation import make_valid

from scripts.analysis._probe_common import run_probe, socrata_dataset_of, soda_walk

logger = logging.getLogger(__name__)

BOUNDARY_SOURCE = "SRC-WPG-PLOW-ZONE"
ADDRESS_SOURCE = "SRC-WPG-SNOW"

ZONE_FIELD = "plow_zone"
GEOM_FIELD = "the_geom"
POINT_FIELD = "location"

UNSCHEDULED_ZONES = ("B/D", "X", "Downtown")
TARGET_ZONE = "B/D"

# deg -> km at Winnipeg's latitude (~49.9 N), used only to turn polygon area
# into a human-readable km^2 for the density comparison, not for anything
# geodesically exact.
KM_PER_DEG_LAT = 111.32
KM_PER_DEG_LON = 111.32 * math.cos(math.radians(49.9))


def _valid_geometries(rows: list[dict]) -> tuple[list, list[str], set[str]]:
    geometries, labels, repaired = [], [], set()
    for row in rows:
        if not row.get(GEOM_FIELD):
            continue
        geometry = shape(row[GEOM_FIELD])
        if not geometry.is_valid:
            repaired.add(row[ZONE_FIELD])
            geometry = make_valid(geometry)
        geometries.append(geometry)
        labels.append(row[ZONE_FIELD])
    return geometries, labels, repaired


def area_km2(geometry) -> float:
    return geometry.area * KM_PER_DEG_LAT * KM_PER_DEG_LON


def adjacency(boundaries: list[dict]) -> dict:
    """Distance in km from the ``B/D`` polygon to every other zone's polygons.

    A distance of ~0 means the polygons touch or overlap. Repaired with
    ``make_valid`` first — 8 of 25 zones (``B/D`` included) carry OGC-invalid
    geometry per the BO-4 audit, and an unrepaired self-intersection makes
    ``distance`` unreliable near the defect.
    """
    geometries, labels, repaired = _valid_geometries(boundaries)

    target_geoms = [g for g, label in zip(geometries, labels, strict=True) if label == TARGET_ZONE]
    if len(target_geoms) != 1:
        raise ValueError(f"expected exactly one {TARGET_ZONE} polygon, found {len(target_geoms)}")
    target = target_geoms[0]

    by_zone: dict[str, list] = defaultdict(list)
    for geometry, label in zip(geometries, labels, strict=True):
        if label != TARGET_ZONE:
            by_zone[label].append(geometry)

    distances = {
        zone: round(min(target.distance(g) for g in geoms) * KM_PER_DEG_LAT, 4)
        for zone, geoms in by_zone.items()
    }
    touching = sorted(zone for zone, d in distances.items() if d < 0.01)

    return {
        "repaired_zones": sorted(repaired),
        "target_area_km2": round(area_km2(target), 3),
        "target_centroid": [round(c, 5) for c in target.centroid.coords[0]],
        "distance_km_to_zone": dict(sorted(distances.items(), key=lambda kv: kv[1])),
        "touching_or_overlapping": touching,
        "touches_B": "B" in touching,
        "touches_D": "D" in touching,
    }


def address_density(boundaries: list[dict], addresses: list[dict]) -> dict:
    """Addresses per km^2 for every zone, unscheduled ones flagged.

    Reuses the same point-in-polygon approach as
    :mod:`scripts.analysis.zone_schedule_rank` — a different question (density,
    not rank correlation) but the same assignment, so it is worth rebuilding
    the index rather than importing zone_schedule_rank's private helper and
    coupling the two probes.
    """
    geometries, labels, _ = _valid_geometries(boundaries)
    tree = STRtree(geometries)
    prepared = [prep(g) for g in geometries]

    counts: dict[str, int] = defaultdict(int)
    unassigned = 0
    for row in addresses:
        location = row.get(POINT_FIELD) or {}
        coordinates = location.get("coordinates")
        if not coordinates:
            unassigned += 1
            continue
        point = Point(*coordinates)
        for index in tree.query(point):
            if prepared[index].contains(point):
                counts[labels[index]] += 1
                break
        else:
            unassigned += 1

    area_by_zone: dict[str, float] = defaultdict(float)
    for geometry, label in zip(geometries, labels, strict=True):
        area_by_zone[label] += area_km2(geometry)

    density = {
        zone: round(counts.get(zone, 0) / area_by_zone[zone], 1)
        for zone in area_by_zone
        if area_by_zone[zone] > 0
    }
    scheduled_densities = sorted(
        v for zone, v in density.items() if zone not in UNSCHEDULED_ZONES
    )
    median_scheduled = (
        scheduled_densities[len(scheduled_densities) // 2] if scheduled_densities else None
    )

    return {
        "addresses_total": len(addresses),
        "addresses_unassigned": unassigned,
        "density_by_zone": dict(sorted(density.items(), key=lambda kv: -kv[1])),
        "median_density_scheduled_zones": median_scheduled,
        "unscheduled_zone_density": {z: density.get(z) for z in UNSCHEDULED_ZONES},
        "target_density_ratio_to_median": (
            round(density[TARGET_ZONE] / median_scheduled, 2)
            if median_scheduled
            else None
        ),
    }


def build_report(args: argparse.Namespace) -> dict:  # noqa: ARG001 - run_probe contract
    boundary_ds = socrata_dataset_of(BOUNDARY_SOURCE)
    address_ds = socrata_dataset_of(ADDRESS_SOURCE)

    logger.info("fetching zone boundaries...")
    boundaries = soda_walk(boundary_ds, f"{ZONE_FIELD},city_area,{GEOM_FIELD}")

    logger.info("fetching address points...")
    addresses = soda_walk(address_ds, f"address_id,street_side,{POINT_FIELD}")

    return {
        "probe": "zone_bd_investigation",
        "adjacency": adjacency(boundaries),
        "density": address_density(boundaries, addresses),
    }


def print_report(report: dict) -> None:
    adj = report["adjacency"]
    print(f"── {TARGET_ZONE} adjacency ──────────────────────────────")
    print(f"  area: {adj['target_area_km2']} km2, centroid: {adj['target_centroid']}")
    print(f"  repaired (invalid) geometries: {adj['repaired_zones']}")
    print("  distance to nearest polygon of each zone (km):")
    for zone, d in adj["distance_km_to_zone"].items():
        marker = " <- touches" if d < 0.01 else ""
        print(f"    {zone:<10} {d}{marker}")
    print(f"  touches B: {adj['touches_B']}, touches D: {adj['touches_D']}")

    density = report["density"]
    print(f"\n── address density ({density['addresses_total']:,} addresses, "
          f"{density['addresses_unassigned']} unassigned) ──")
    print(f"  median scheduled-zone density: {density['median_density_scheduled_zones']} addr/km2")
    for zone in UNSCHEDULED_ZONES:
        d = density["unscheduled_zone_density"].get(zone)
        print(f"  {zone:<10} {d} addr/km2")
    print(f"  {TARGET_ZONE} / median ratio: {density['target_density_ratio_to_median']}")


def main(argv: list[str] | None = None) -> int:
    return run_probe(__doc__, build_report, print_report, argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
