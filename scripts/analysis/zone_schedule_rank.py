"""Scheduling-rank cross-check: are the last-plowed zones also the biggest ones?

Answers the question BO-2 leaves open once ``shift_end`` stops being read as a
completion time (ADR 0008): the plow schedule ranks every operational zone into
shifts, so *who gets scheduled first, and who waits?* — and is the waiting
concentrated where the addresses are?

Three public datasets, none of which shares a key with the next:

    zone boundaries      MultiPolygons, one per geometry piece
    clearing status      address points — carries **no zone field**
    plow shift schedule  one row per (event, zone), with a shift number

Address to zone is therefore a point-in-polygon assignment. There is no
shortcut: the clearing-status dataset's ``:@computed_region_*`` columns are
wards and neighbourhoods, not operational zones.

Resource ids and domains are read from ``config/sources/*.yaml`` — never
hardcoded here, so this stays correct when the deployed city changes.

``--since`` restricts the schedule to operations from a date onward. That is not
a convenience: ``rank_counterfactuals`` measured Spearman +0.591 between the
first and second halves of the record, with individual zones moving more than a
whole shift. The ten-year mean rank this probe computes by default therefore
describes an ordering that no longer holds, and the ``r`` it correlates against
address count has to be re-derived on a recent window before BO-2 can quote it.

Usage::

    uv run python -m scripts.analysis.zone_schedule_rank
    uv run python -m scripts.analysis.zone_schedule_rank --since 2021-01-01
    uv run python -m scripts.analysis.zone_schedule_rank --json out.json

Read-only against the public API. Writes nothing to object storage.
"""

from __future__ import annotations

import argparse
import logging
from collections import defaultdict
from dataclasses import asdict, dataclass

from shapely.geometry import Point, shape
from shapely.prepared import prep
from shapely.strtree import STRtree

from scripts.analysis._probe_common import (
    pearson,
    run_probe,
    socrata_dataset_of,
    soda_get,
    soda_walk,
)

logger = logging.getLogger(__name__)

# Sources this cross-check reads. Real ids from config/sources/ — the YAML is
# the authority for resource id, domain and field semantics.
BOUNDARY_SOURCE = "SRC-WPG-PLOW-ZONE"
ADDRESS_SOURCE = "SRC-WPG-SNOW"
SCHEDULE_SOURCE = "SRC-WPG-PLOW-SHIFT"

# Column names verified against the live API on 2026-08-07. Per AGENTS.md these
# are never assumed from memory — they match contracts/api-contracts/winnipeg-*.
ZONE_FIELD = "plow_zone"
GEOM_FIELD = "the_geom"
POINT_FIELD = "location"
SHIFT_FIELD = "shift_number"


@dataclass
class ZoneRow:
    """One operational zone: how late it is scheduled, and how much it holds."""

    zone: str
    avg_shift: float
    min_shift: int
    max_shift: int
    events: int
    addresses: int
    address_share: float


def assign_addresses_to_zones(
    boundaries: list[dict], addresses: list[dict],
) -> tuple[dict[str, int], int]:
    """Point-in-polygon count of address points per zone.

    Returns ``(counts_by_zone, unassigned)``. An address with no point geometry
    counts as unassigned rather than being silently dropped — the hit rate is
    itself a reported quantity (BO-4's acceptance criterion).
    """
    geometries, labels = [], []
    for row in boundaries:
        if not row.get(GEOM_FIELD):
            continue
        geometries.append(shape(row[GEOM_FIELD]))
        labels.append(row[ZONE_FIELD])

    tree = STRtree(geometries)
    prepared = [prep(g) for g in geometries]
    logger.info("%d polygons over %d zone values", len(geometries), len(set(labels)))

    counts: dict[str, int] = defaultdict(int)
    unassigned = 0
    for row in addresses:
        location = row.get(POINT_FIELD) or {}
        coordinates = location.get("coordinates")
        if not coordinates:
            unassigned += 1
            continue
        point = Point(*coordinates)
        # The r-tree returns bounding-box candidates; contains() is the real test.
        for index in tree.query(point):
            if prepared[index].contains(point):
                counts[labels[index]] += 1
                break
        else:
            unassigned += 1
    return dict(counts), unassigned


def build_report(since: str | None = None) -> dict:
    """Run the whole cross-check and return a serialisable result.

    ``since`` filters the schedule to operations starting on or after that
    date. Address counts are not filtered — the address dataset is an
    overwrite-in-place snapshot with no history, so it is always "now"
    regardless of the schedule window. That asymmetry is a real limitation of
    the correlation and is reported rather than hidden.
    """
    boundary_ds = socrata_dataset_of(BOUNDARY_SOURCE)
    address_ds = socrata_dataset_of(ADDRESS_SOURCE)
    schedule_ds = socrata_dataset_of(SCHEDULE_SOURCE)

    logger.info("fetching zone boundaries...")
    boundaries = soda_walk(boundary_ds, f"{ZONE_FIELD},city_area,{GEOM_FIELD}")

    logger.info("fetching address points...")
    addresses = soda_walk(address_ds, f"address_id,street_side,{POINT_FIELD}")

    counts, unassigned = assign_addresses_to_zones(boundaries, addresses)
    assigned = sum(counts.values())

    logger.info("fetching shift schedule...")
    query = {
        "$select": (
            f"{ZONE_FIELD},avg({SHIFT_FIELD}) as avg_shift,"
            f"min({SHIFT_FIELD}) as min_shift,max({SHIFT_FIELD}) as max_shift,"
            f"count(*) as events"
        ),
        "$group": ZONE_FIELD,
    }
    if since:
        query["$where"] = f"shift_start >= '{since}T00:00:00.000'"
    ranks = soda_get(schedule_ds, **query)

    rows = [
        ZoneRow(
            zone=r[ZONE_FIELD],
            avg_shift=float(r["avg_shift"]),
            min_shift=int(float(r["min_shift"])),
            max_shift=int(float(r["max_shift"])),
            events=int(r["events"]),
            addresses=counts.get(r[ZONE_FIELD], 0),
            address_share=counts.get(r[ZONE_FIELD], 0) / assigned,
        )
        for r in ranks
    ]
    rows.sort(key=lambda r: -r.avg_shift)

    # Zones that have a boundary but never appear in the schedule. They must be
    # excluded from any ranking statement — "no shift record" is not "scheduled
    # last", and reading it that way is exactly the false positive that ADR 0008
    # retired the supply-gap factor over.
    scheduled = {r.zone for r in rows}
    orphans = {
        label: counts.get(label, 0)
        for label in {b[ZONE_FIELD] for b in boundaries}
        if label not in scheduled
    }

    return {
        "schedule_since": since,
        "zones": [asdict(r) for r in rows],
        "spatial_hit_rate": assigned / len(addresses),
        "addresses_total": len(addresses),
        "addresses_assigned": assigned,
        "addresses_unassigned": unassigned,
        "correlation_shift_vs_addresses": pearson(
            [r.avg_shift for r in rows], [float(r.addresses) for r in rows],
        ),
        "unscheduled_zones": orphans,
        "unscheduled_address_share": sum(orphans.values()) / assigned,
    }


def print_report(report: dict) -> None:
    """Human-readable rendering on stdout."""
    if report.get("schedule_since"):
        print(f"schedule restricted to operations from {report['schedule_since']}\n")
    print(f"{'zone':<7}{'avg_shift':>10}{'range':>8}{'events':>8}{'addresses':>11}{'share':>8}")
    print("-" * 52)
    for row in report["zones"]:
        span = f"{row['min_shift']}-{row['max_shift']}"
        print(
            f"{row['zone']:<7}{row['avg_shift']:>10.2f}{span:>8}{row['events']:>8}"
            f"{row['addresses']:>11,}{row['address_share']:>7.1%}",
        )

    print(
        f"\nspatial hit rate: {report['addresses_assigned']:,}/"
        f"{report['addresses_total']:,} = {report['spatial_hit_rate']:.1%}"
        f"  ({report['addresses_unassigned']} unassigned)",
    )
    correlation = report["correlation_shift_vs_addresses"]
    print(f"Pearson r(avg_shift, addresses) = {correlation:+.3f}")
    print("  r > 0 -> later-scheduled zones hold MORE addresses")
    print("  r < 0 -> later-scheduled zones hold FEWER addresses")

    if report["unscheduled_zones"]:
        print(
            f"\nzones with a boundary but no shift record: "
            f"{report['unscheduled_zones']} "
            f"({report['unscheduled_address_share']:.1%} of assigned addresses)",
        )
        print("  exclude these from any ranking claim — see ADR 0008 §3.")


def main(argv: list[str] | None = None) -> int:
    def add_arguments(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--since", metavar="YYYY-MM-DD",
            help="only count plow operations starting on or after this date; "
                 "the default ten-year mean rank is known to average over a "
                 "reordering (rank_counterfactuals: rho = +0.591)",
        )

    return run_probe(
        __doc__, lambda args: build_report(args.since), print_report, add_arguments, argv,
    )


if __name__ == "__main__":
    raise SystemExit(main())
