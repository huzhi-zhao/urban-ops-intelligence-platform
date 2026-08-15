"""BO-2 probe: can anything other than policy explain the scheduling rank?

Scheduling rank replaced ``shift_end`` as BO-2's core evidence on 2026-08-07,
and inherited its predecessor's problem: it looks convincing and nothing has
been run against it. ``business-objectives.md`` BO-2 lists four alternative
explanations that must be excluded before rank can be presented as a finding.
One is already done (``zone_schedule_rank`` — later zones hold *more*
addresses, r = +0.491, so "they are small" does not explain it). This probe
takes the rest:

    snowfall     do later-scheduled zones simply get more snow?
    drift        has the rank changed over the years, with the mean hiding it?
    semantics    is shift_number "which batch", or some other numbering?
    linkage      how many of the 49 bans carry any shift rows at all?

The road-network explanation (``ngsx-caav``) is deliberately not here: BO-2's
own discipline is to spend that integration only if the cheaper three leave
the question open.

Two method notes that matter for reading the output:

- Snowfall is sampled at one representative interior point per zone, not the
  bounding-box centre — a crescent-shaped zone's centroid can fall outside it.
  The weather archive's grid is coarser than the city, so finding that every
  zone shares one series is itself the answer: a variable with no spatial
  variance cannot explain a spatial ranking.
- Rank drift is measured per ban event and then compared between the earliest
  and latest halves of the era. A stable rank is evidence *for* the policy
  reading; a drifted one means the ten-year mean is an artefact.

Usage::

    uv run python -m scripts.analysis.rank_counterfactuals
    uv run python -m scripts.analysis.rank_counterfactuals --json out.json

Read-only against public APIs. Writes nothing to object storage.
"""

from __future__ import annotations

import argparse
import logging
from collections import defaultdict
from datetime import date, datetime

from shapely.geometry import shape
from shapely.ops import unary_union
from shapely.validation import make_valid

from scripts.analysis._probe_common import (
    pearson,
    run_probe,
    socrata_dataset_of,
    soda_get,
    soda_walk,
    spearman,
    summarise,
    weather_archive,
)

logger = logging.getLogger(__name__)

BOUNDARY_SOURCE = "SRC-WPG-PLOW-ZONE"
SCHEDULE_SOURCE = "SRC-WPG-PLOW-SHIFT"
BAN_SOURCE = "SRC-WPG-PARKING-BAN"

ZONE_FIELD = "plow_zone"
GEOM_FIELD = "the_geom"
SHIFT_FIELD = "shift_number"
SNOW_FIELD = "snowfall_sum"

# The schedule starts 2015-12. Sampling snowfall before that would average in
# years the ranking cannot have been responding to.
ERA_START = date(2015, 11, 1)


# ── Upstream reads ───────────────────────────────────────────────────────────


def fetch_schedule() -> list[dict]:
    """Every (event, zone) shift row, with the fields the counterfactuals need."""
    ds = socrata_dataset_of(SCHEDULE_SOURCE)
    return soda_walk(ds, f"snow_ban_id,{SHIFT_FIELD},shift_start,shift_end,{ZONE_FIELD}")


def fetch_zone_points() -> tuple[dict[str, tuple[float, float]], list[str]]:
    """One representative interior point per zone value, plus repaired zones.

    A zone value can span several disjoint MultiPolygons (82 polygons over 25
    values). They are unioned first so the sample point represents the zone as
    scheduled, not whichever piece the API returned first.

    Some upstream polygons are not OGC-valid and make the union throw
    (``unable to assign free hole to a shell``). They are repaired with
    ``make_valid`` and the affected zones are reported rather than silently
    fixed: BO-4's spatial join runs over these same geometries, and a
    repair that changes an edge changes which requests land in which zone.
    """
    ds = socrata_dataset_of(BOUNDARY_SOURCE)
    rows = soda_walk(ds, f"{ZONE_FIELD},{GEOM_FIELD}")

    pieces: dict[str, list] = defaultdict(list)
    repaired: set[str] = set()
    for row in rows:
        if not row.get(GEOM_FIELD):
            continue
        geometry = shape(row[GEOM_FIELD])
        if not geometry.is_valid:
            repaired.add(row[ZONE_FIELD])
            geometry = make_valid(geometry)
        pieces[row[ZONE_FIELD]].append(geometry)

    points = {}
    for zone, geometries in pieces.items():
        # representative_point() is guaranteed inside the geometry; centroid is
        # not, and a crescent-shaped zone would be sampled outside itself.
        point = unary_union(geometries).representative_point()
        points[zone] = (round(point.y, 4), round(point.x, 4))
    return points, sorted(repaired)


def fetch_ban_ids() -> set[str]:
    ds = socrata_dataset_of(BAN_SOURCE)
    return {r["id"] for r in soda_get(ds, **{"$select": "id", "$limit": "5000"})}


def fetch_zone_snowfall(
    points: dict[str, tuple[float, float]], start: date, end: date,
) -> dict[str, float]:
    """Total snowfall at each zone's sample point over one wide window.

    One call per zone rather than one per zone-season: the counterfactual asks
    whether zones differ from each other, and 25 calls answer that as well as
    275 would.
    """
    totals = {}
    for zone, (latitude, longitude) in sorted(points.items()):
        records = weather_archive(start, end, latitude=latitude, longitude=longitude)
        totals[zone] = round(
            sum(float(r[SNOW_FIELD]) for r in records if r.get(SNOW_FIELD) is not None), 1,
        )
        logger.info("zone %s @ %s,%s: %.1f cm", zone, latitude, longitude, totals[zone])
    return totals


# ── Counterfactual 1: snowfall ───────────────────────────────────────────────


def mean_shift_by_zone(rows: list[dict]) -> dict[str, float]:
    """Average shift number per zone — the rank being challenged."""
    per_zone: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        per_zone[row[ZONE_FIELD]].append(float(row[SHIFT_FIELD]))
    return {zone: sum(v) / len(v) for zone, v in per_zone.items()}


def snowfall_counterfactual(
    ranks: dict[str, float], snowfall: dict[str, float],
) -> dict:
    """Does snowfall covary with rank, and does it vary between zones at all?

    The second question comes first. If every zone's sample point returns the
    same total, the correlation is undefined — and reporting an undefined
    correlation as "no relationship" would be the same error as reading a
    zero-variance ``shift_end`` as a completion time.
    """
    shared = sorted(set(ranks) & set(snowfall))
    values = [snowfall[z] for z in shared]
    distinct = len(set(values))
    spread = (max(values) - min(values)) if values else 0.0

    result = {
        "zones_compared": len(shared),
        "distinct_snowfall_totals": distinct,
        "snowfall_cm": summarise(values),
        "spread_cm": round(spread, 1),
        "relative_spread": round(spread / (sum(values) / len(values)), 4) if values else None,
        "correlation_rank_vs_snowfall": None,
        "verdict": "no variance to explain anything with",
    }
    if distinct > 1:
        result["correlation_rank_vs_snowfall"] = round(
            pearson([ranks[z] for z in shared], values), 4,
        )
        result["verdict"] = "see correlation"
    return result


# ── Counterfactual 2: rank drift ─────────────────────────────────────────────


def rank_by_event(rows: list[dict]) -> dict[str, dict[str, float]]:
    """Shift number per zone, per ban event."""
    per_event: dict[str, dict[str, float]] = defaultdict(dict)
    for row in rows:
        per_event[row["snow_ban_id"]][row[ZONE_FIELD]] = float(row[SHIFT_FIELD])
    return dict(per_event)


def event_order(rows: list[dict]) -> list[str]:
    """Ban event ids in chronological order of their first shift."""
    first: dict[str, str] = {}
    for row in rows:
        start = row.get("shift_start")
        if not start:
            continue
        key = row["snow_ban_id"]
        if key not in first or start < first[key]:
            first[key] = start
    return [event for event, _ in sorted(first.items(), key=lambda kv: kv[1])]


def drift_counterfactual(rows: list[dict]) -> dict:
    """Compare the zone ranking in the earliest half of events to the latest.

    If the two halves agree, the ten-year mean is not hiding a policy change.
    If they disagree, every statement built on the mean is about a schedule
    that no longer exists.
    """
    per_event = rank_by_event(rows)
    order = event_order(rows)
    if len(order) < 4:
        return {"events": len(order), "spearman_early_vs_late": None}

    half = len(order) // 2
    early, late = order[:half], order[half:]

    def average(events: list[str]) -> dict[str, float]:
        acc: dict[str, list[float]] = defaultdict(list)
        for event in events:
            for zone, shift in per_event[event].items():
                acc[zone].append(shift)
        return {zone: sum(v) / len(v) for zone, v in acc.items()}

    early_rank, late_rank = average(early), average(late)
    shared = sorted(set(early_rank) & set(late_rank))

    moved = sorted(
        (
            {
                "zone": zone,
                "early": round(early_rank[zone], 2),
                "late": round(late_rank[zone], 2),
                "delta": round(late_rank[zone] - early_rank[zone], 2),
            }
            for zone in shared
        ),
        key=lambda d: -abs(d["delta"]),
    )

    return {
        "events": len(order),
        "early_events": len(early),
        "late_events": len(late),
        "zones_in_both": len(shared),
        "zones_early_only": sorted(set(early_rank) - set(late_rank)),
        "zones_late_only": sorted(set(late_rank) - set(early_rank)),
        "spearman_early_vs_late": round(
            spearman([early_rank[z] for z in shared], [late_rank[z] for z in shared]), 4,
        ),
        "largest_moves": moved[:5],
    }


# ── Question 1: what does shift_number mean? ─────────────────────────────────


def semantics_check(rows: list[dict]) -> dict:
    """Is ``shift_number`` the batch order, i.e. does it agree with time?

    Within one event, sorting zones by ``shift_number`` should reproduce
    sorting them by ``shift_start``. Checked on every event rather than one:
    a numbering that agrees with time in most events and not others is not a
    batch order, it is a coincidence.
    """
    per_event: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for row in rows:
        if row.get("shift_start"):
            per_event[row["snow_ban_id"]].append(
                (float(row[SHIFT_FIELD]), row["shift_start"]),
            )

    consistent, inconsistent, single_valued = 0, [], 0
    for event, pairs in per_event.items():
        shifts = [s for s, _ in pairs]
        if len(set(shifts)) < 2:
            single_valued += 1
            continue
        starts = [t for _, t in pairs]
        # Rank correlation, not equality: several zones share a shift number and
        # therefore share a start time, so ties must not count as disagreement.
        rho = spearman(shifts, [datetime.fromisoformat(t).timestamp() for t in starts])
        if rho > 0.999:
            consistent += 1
        else:
            inconsistent.append({"event": event, "spearman": round(rho, 4)})

    return {
        "events_checked": len(per_event),
        "events_with_one_shift_value": single_valued,
        "events_where_shift_number_matches_time": consistent,
        "events_where_it_does_not": inconsistent,
    }


# ── Question 3: ban ↔ schedule linkage ───────────────────────────────────────


def linkage_check(rows: list[dict], ban_ids: set[str]) -> dict:
    """How completely does ``snow_ban_id`` resolve against the ban table?

    Two different numbers, and conflating them is how a join silently drops
    rows: bans that have no schedule (expected — most bans are not full plow
    events) versus schedule rows pointing at a ban id that does not exist
    (not expected — that is referential breakage).
    """
    scheduled = {row["snow_ban_id"] for row in rows if row.get("snow_ban_id")}
    dangling = sorted(scheduled - ban_ids)
    return {
        "bans": len(ban_ids),
        "bans_with_schedule": len(scheduled & ban_ids),
        "ban_coverage": round(len(scheduled & ban_ids) / len(ban_ids), 4) if ban_ids else None,
        "schedule_events": len(scheduled),
        "schedule_events_with_no_matching_ban": dangling,
        "rows_without_a_ban_id": sum(1 for row in rows if not row.get("snow_ban_id")),
    }


# ── Report ───────────────────────────────────────────────────────────────────


def build_report(args: argparse.Namespace) -> dict:
    rows = fetch_schedule()
    ranks = mean_shift_by_zone(rows)
    points, repaired = fetch_zone_points()
    ban_ids = fetch_ban_ids()

    end = date(date.today().year + (1 if date.today().month >= 11 else 0), 5, 1)
    snowfall = fetch_zone_snowfall(
        {z: p for z, p in points.items() if z in ranks} if args.scheduled_zones_only else points,
        ERA_START,
        min(end, date.today()),
    )

    return {
        "probe": "rank_counterfactuals",
        "measured_on": date.today().isoformat(),
        "schedule_rows": len(rows),
        "zones_scheduled": len(ranks),
        "zones_with_boundary": len(points),
        "zones_with_invalid_geometry_repaired": repaired,
        "snowfall_window": [ERA_START.isoformat(), min(end, date.today()).isoformat()],
        "zone_sample_points": {z: list(p) for z, p in sorted(points.items())},
        "mean_shift_by_zone": {z: round(v, 3) for z, v in sorted(ranks.items())},
        "zone_snowfall_cm": snowfall,
        "counterfactual_snowfall": snowfall_counterfactual(ranks, snowfall),
        "counterfactual_drift": drift_counterfactual(rows),
        "semantics_shift_number": semantics_check(rows),
        "linkage_ban_to_schedule": linkage_check(rows, ban_ids),
    }


def print_report(report: dict) -> None:
    print(
        f"{report['schedule_rows']} shift rows · {report['zones_scheduled']} scheduled zones · "
        f"{report['zones_with_boundary']} zones with a boundary\n",
    )

    print("── shift_number semantics ─────────────────────────────────")
    sem = report["semantics_shift_number"]
    print(
        f"  events checked {sem['events_checked']}, "
        f"batch order agrees with time in {sem['events_where_shift_number_matches_time']}, "
        f"disagrees in {len(sem['events_where_it_does_not'])}, "
        f"single-valued {sem['events_with_one_shift_value']}",
    )
    for bad in sem["events_where_it_does_not"]:
        print(f"    event {bad['event']}: spearman {bad['spearman']}")

    print("\n── counterfactual: do later zones get more snow? ──────────")
    snow = report["counterfactual_snowfall"]
    print(
        f"  {snow['zones_compared']} zones, "
        f"{snow['distinct_snowfall_totals']} distinct totals, "
        f"spread {snow['spread_cm']} cm "
        f"({_pct(snow['relative_spread'])} of the mean)",
    )
    correlation = snow["correlation_rank_vs_snowfall"]
    if correlation is None:
        print("  r undefined — snowfall does not vary across zones at this resolution")
        print("  => the snowfall explanation cannot account for the ranking")
    else:
        print(f"  Pearson r(mean_shift, snowfall) = {correlation:+.3f}")

    print("\n── counterfactual: has the rank drifted? ──────────────────")
    drift = report["counterfactual_drift"]
    print(
        f"  {drift['early_events']} early vs {drift['late_events']} late events, "
        f"{drift['zones_in_both']} zones in both, "
        f"spearman {drift['spearman_early_vs_late']:+.3f}",
    )
    for move in drift["largest_moves"]:
        print(
            f"    zone {move['zone']}: {move['early']} -> {move['late']} "
            f"({move['delta']:+.2f})",
        )
    if drift["zones_early_only"] or drift["zones_late_only"]:
        print(
            f"    zones only early: {drift['zones_early_only']}  "
            f"only late: {drift['zones_late_only']}",
        )

    print("\n── linkage: ban <-> schedule ──────────────────────────────")
    link = report["linkage_ban_to_schedule"]
    print(
        f"  {link['bans_with_schedule']}/{link['bans']} bans carry shift rows "
        f"({_pct(link['ban_coverage'])}); "
        f"{len(link['schedule_events_with_no_matching_ban'])} schedule events "
        f"point at a ban that does not exist",
    )
    print(
        "\nBO-2 acceptance (design §3.3 task 2): at least the first three "
        "counterfactuals resolved,\nand none of them fully explains the rank.",
    )


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.1%}"


def main(argv: list[str] | None = None) -> int:
    def add_arguments(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--scheduled-zones-only", action="store_true",
            help="sample snowfall only for zones that appear in the schedule",
        )

    return run_probe(__doc__, build_report, print_report, add_arguments, argv)


if __name__ == "__main__":
    raise SystemExit(main())
