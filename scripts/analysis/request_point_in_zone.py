"""BO-4 probe: where do the requests land, and can ward be mapped to a zone?

``score_collinearity`` already measured the headline number as a side effect —
99.9% of winter requests carrying a coordinate fall inside some operational
zone. That closes BO-4's ">90% spatial hit rate" acceptance criterion but
leaves the two questions the hit rate cannot answer:

    unscheduled share   what share of requests land in a zone that is never
                        scheduled? ADR 0008 measured 6.0% of *addresses* in the
                        three unscheduled zones; requests are a different
                        population and may not be distributed the same way.
    ward <-> zone       BO-4 needs a crossmap between the ward the request
                        carries as text and the zone the geometry falls in.
                        Whether one exists at all depends on whether the two
                        partitions nest — and they are drawn by different
                        departments for different purposes, so they need not.

The unscheduled share matters because a request there has no BO-2 rank, and
BO-6 must leave that factor NULL rather than fill it (``rank_factor`` already
does). If the share were large, "no rank" would stop being an edge case and the
score would be undefined for a meaningful slice of the city.

**What this probe does not measure**: whether the ward *text label* is correct,
in the sense of agreeing with an independent spatial assignment to a ward
polygon. That needs a ward-boundary source, and Winnipeg's three ward-boundary
datasets are catalogued but not retrievable — neither SODA nor the geospatial
export answers for them. The crossmap below measures how cleanly the two
partitions nest, which is what BO-4 consumes; it cannot detect a label that is
wrong in a way that is spatially consistent.

Usage::

    uv run python -m scripts.analysis.request_point_in_zone
    uv run python -m scripts.analysis.request_point_in_zone --json out.json

Read-only against public APIs. Writes nothing to object storage.
"""

from __future__ import annotations

import argparse
import logging
from collections import defaultdict
from datetime import date

from scripts.analysis._probe_common import (
    ZoneIndex,
    run_probe,
    socrata_dataset_of,
    soda_walk,
    summarise,
)
from scripts.analysis.snowfall_events import winter_type_filter

logger = logging.getLogger(__name__)

REQUEST_SOURCE = "SRC-WPG-311"
BOUNDARY_SOURCE = "SRC-WPG-PLOW-ZONE"
SCHEDULE_SOURCE = "SRC-WPG-PLOW-SHIFT"

ZONE_FIELD = "plow_zone"
GEOM_FIELD = "the_geom"

# The schedule era. Requests before it can still be placed in a zone, but
# "unscheduled zone" is only meaningful where a schedule exists, so the two
# populations are reported separately rather than pooled.
ERA_START = date(2015, 11, 1)


# ── Upstream reads ───────────────────────────────────────────────────────────


def fetch_scheduled_zones() -> set[str]:
    """Zone labels that appear in the plow schedule at least once.

    A zone with a boundary but no shift record is *unscheduled*, not
    "scheduled last" — the distinction ADR 0008 retired the supply-gap factor
    over, and the reason this set is derived from the data rather than from the
    three labels everyone quotes from memory.
    """
    ds = socrata_dataset_of(SCHEDULE_SOURCE)
    return {row[ZONE_FIELD] for row in soda_walk(ds, ZONE_FIELD) if row.get(ZONE_FIELD)}


def fetch_winter_requests() -> list[dict]:
    """Winter requests carrying a coordinate, with their ward label.

    Rows without geometry are excluded upstream rather than counted as misses:
    79% of the whole table has no coordinates, so including them would measure
    the upstream fill rate and call it a spatial hit rate — the denominator
    error CLAUDE.md's escalation rule exists to prevent.
    """
    ds = socrata_dataset_of(REQUEST_SOURCE)
    where = f"({winter_type_filter()}) AND geometry IS NOT NULL"
    return soda_walk(ds, "open_date,ward,geometry", where=where)


# ── Analysis ─────────────────────────────────────────────────────────────────


def assign(rows: list[dict], index: ZoneIndex) -> list[tuple[date, str | None, str | None]]:
    """One ``(day, ward label, zone)`` triple per request; zone ``None`` if outside."""
    placed = []
    for row in rows:
        coordinates = (row.get("geometry") or {}).get("coordinates")
        if not coordinates:
            continue
        placed.append((
            date.fromisoformat(row["open_date"][:10]),
            row.get("ward"),
            index.assign(float(coordinates[0]), float(coordinates[1])),
        ))
    return placed


def zone_shares(
    placed: list[tuple[date, str | None, str | None]],
    scheduled: set[str],
    era_start: date,
) -> dict:
    """Hit rate and the share landing in a zone that is never scheduled."""
    era = [p for p in placed if p[0] >= era_start]
    counts: dict[str, int] = defaultdict(int)
    outside = 0
    for _day, _ward, zone in era:
        if zone is None:
            outside += 1
        else:
            counts[zone] += 1

    assigned = sum(counts.values())
    unscheduled = {z: n for z, n in counts.items() if z not in scheduled}
    return {
        "requests_with_geometry": len(era),
        "assigned_to_a_zone": assigned,
        "outside_every_zone": outside,
        "hit_rate": round(assigned / len(era), 4) if era else None,
        "zones_hit": len(counts),
        "unscheduled_zones": dict(sorted(unscheduled.items(), key=lambda kv: -kv[1])),
        "requests_in_unscheduled_zones": sum(unscheduled.values()),
        "unscheduled_share": (
            round(sum(unscheduled.values()) / assigned, 4) if assigned else None
        ),
        "per_zone": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
    }


def crossmap(placed: list[tuple[date, str | None, str | None]]) -> dict:
    """How cleanly the ward partition nests inside the zone partition.

    Measured in both directions, because "can ward be mapped to a zone" and
    "can a zone be attributed to a ward" are different questions and BO-4 asks
    the first while BO-6's panel implies the second.

    The number reported per label is the share of its requests falling in its
    single most common counterpart — the best any 1:1 crossmap could achieve.
    A share near 1.0 means the two partitions nest; anything lower is the
    error rate a ward-keyed model inherits when it is read as a zone-keyed one.
    """
    both = [(w, z) for _day, w, z in placed if w and z]
    ward_to_zone: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    zone_to_ward: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for ward, zone in both:
        ward_to_zone[ward][zone] += 1
        zone_to_ward[zone][ward] += 1

    def concentration(mapping: dict[str, dict[str, int]]) -> tuple[dict, list[float]]:
        rows, shares = {}, []
        for label, counterparts in sorted(mapping.items()):
            total = sum(counterparts.values())
            best, best_n = max(counterparts.items(), key=lambda kv: kv[1])
            share = best_n / total
            shares.append(share)
            rows[label] = {
                "requests": total,
                "counterparts": len(counterparts),
                "dominant": best,
                "dominant_share": round(share, 4),
            }
        return rows, shares

    ward_rows, ward_shares = concentration(ward_to_zone)
    zone_rows, zone_shares_ = concentration(zone_to_ward)

    # The weighted figure is the one to quote: an unweighted mean over labels
    # lets a ward with 40 requests count as much as one with 40,000.
    def weighted(rows: dict) -> float:
        total = sum(r["requests"] for r in rows.values())
        return round(
            sum(r["requests"] * r["dominant_share"] for r in rows.values()) / total, 4,
        ) if total else 0.0

    return {
        "requests_with_both_labels": len(both),
        "ward_to_zone": ward_rows,
        "zone_to_ward": zone_rows,
        "ward_to_zone_dominant_share": summarise(ward_shares),
        "zone_to_ward_dominant_share": summarise(zone_shares_),
        "ward_to_zone_weighted": weighted(ward_rows),
        "zone_to_ward_weighted": weighted(zone_rows),
    }


# ── Report ───────────────────────────────────────────────────────────────────


def build_report(args: argparse.Namespace) -> dict:
    boundary_ds = socrata_dataset_of(BOUNDARY_SOURCE)
    logger.info("fetching zone boundaries...")
    boundaries = soda_walk(boundary_ds, f"{ZONE_FIELD},{GEOM_FIELD}")
    index = ZoneIndex(boundaries, ZONE_FIELD, GEOM_FIELD)

    scheduled = fetch_scheduled_zones()
    logger.info("fetching winter requests with geometry...")
    placed = assign(fetch_winter_requests(), index)

    era_start = date.fromisoformat(args.era_start)
    return {
        "probe": "request_point_in_zone",
        "measured_on": date.today().isoformat(),
        "era_from": era_start.isoformat(),
        "zones_with_a_boundary": len(index.zones),
        "zones_in_the_schedule": len(scheduled),
        "zones_never_scheduled": sorted(set(index.zones) - scheduled),
        "zones_with_invalid_geometry_repaired": index.repaired,
        "spatial": zone_shares(placed, scheduled, era_start),
        "crossmap": crossmap([p for p in placed if p[0] >= era_start]),
    }


def print_report(report: dict) -> None:
    print(
        f"{report['zones_with_a_boundary']} zones have a boundary, "
        f"{report['zones_in_the_schedule']} appear in the schedule; "
        f"never scheduled: {report['zones_never_scheduled']}",
    )
    if report["zones_with_invalid_geometry_repaired"]:
        print(
            f"repaired invalid geometry in: "
            f"{report['zones_with_invalid_geometry_repaired']}",
        )

    spatial = report["spatial"]
    print(f"\n── winter requests since {report['era_from']} ──────────────")
    print(f"  with geometry        {spatial['requests_with_geometry']:,}")
    print(
        f"  inside a zone        {spatial['assigned_to_a_zone']:,} "
        f"({spatial['hit_rate']:.1%})",
    )
    print(f"  outside every zone   {spatial['outside_every_zone']:,}")
    print(
        f"  in unscheduled zones {spatial['requests_in_unscheduled_zones']:,} "
        f"({spatial['unscheduled_share']:.1%} of those inside a zone)",
    )
    for zone, n in spatial["unscheduled_zones"].items():
        print(f"    {zone:<12}{n:>10,}")
    print("  these carry no BO-2 rank; BO-6 must leave the factor NULL, not 0")

    cross = report["crossmap"]
    print("\n── ward <-> zone crossmap ──────────────────────────────")
    print(f"  requests carrying both labels: {cross['requests_with_both_labels']:,}")
    print(
        f"  ward -> zone: a ward's requests fall in its dominant zone "
        f"{cross['ward_to_zone_weighted']:.1%} of the time "
        f"(median over wards {cross['ward_to_zone_dominant_share']['median']:.2f})",
    )
    print(
        f"  zone -> ward: a zone's requests carry its dominant ward "
        f"{cross['zone_to_ward_weighted']:.1%} of the time "
        f"(median over zones {cross['zone_to_ward_dominant_share']['median']:.2f})",
    )
    print("  1.0 would mean the two partitions nest; lower is the error a")
    print("  ward-keyed model inherits when its output is read per zone.")


def main(argv: list[str] | None = None) -> int:
    def add_arguments(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--era-start", default=ERA_START.isoformat(), metavar="YYYY-MM-DD",
            help="only count requests from this date on (default 2015-11-01, "
                 "when the plow schedule starts and 'unscheduled' first means "
                 "something)",
        )

    return run_probe(__doc__, build_report, print_report, add_arguments, argv)


if __name__ == "__main__":
    raise SystemExit(main())
