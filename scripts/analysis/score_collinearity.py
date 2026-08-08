"""BO-6 probe: are the three scoring factors actually measuring three things?

The Winter Operational Load Score is

    0.40 x predicted request volume   (BO-1 / M1)
  + 0.30 x scheduling rank            (BO-2)
  + 0.30 x weather severity           (BO-3)

and M1's dominant input *is* the weather. If the first and third terms move
together, the "composite" score is a monotone transform of snowfall wearing
three coats, and its factor attribution — which BO-8 turns into a
recommendation — attributes to three causes what is one. ``business-objectives``
has never checked this.

Grain: **zone x event**, restricted to the schedule era. That is the only grain
where all three factors are natively defined. Rank exists per ``plow_zone`` and
nowhere else, and BO-6 §有效分析窗口 already restricts scoring to 2015-12
onward, so nothing is lost by measuring where the score will actually live.

Three things make the measurement non-obvious, and each is why a number here
differs from the naive one:

- **The request factor is the observed count, not M1's prediction.** M1 does
  not exist. This is a *lower* bound on the collinearity that matters: a model
  fitted with weather among its inputs can only track weather more closely than
  its own target does. If the observed correlation is already high, the
  predicted one cannot be lower for the right reasons.
- **Requests are counted per zone by point-in-polygon**, not by the ``ward``
  text field. Rank has no ward, ward has no rank, and BO-4's mapping between
  them does not exist yet. Joining through the geometry avoids inventing it.
- **Weather severity is sampled at each zone's own point.** BO-2 measured the
  between-zone spread at 2.1%, so severity is very nearly an event-level
  constant — which is itself half the answer to "does rank add a dimension":
  within one event, rank varies across zones and weather does not.

Severity follows BO-3's definition — a 0-1 composite of snowfall and cold —
normalised across the observed events. The split between the two halves is a
CLI flag, not a buried constant: it is a modelling choice this probe is meant
to expose, not settle.

Usage::

    uv run python -m scripts.analysis.score_collinearity
    uv run python -m scripts.analysis.score_collinearity --threshold 3 --json out.json

Read-only against public APIs. Writes nothing to object storage.
"""

from __future__ import annotations

import argparse
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime

from shapely.geometry import shape
from shapely.ops import unary_union
from shapely.validation import make_valid

from scripts.analysis._probe_common import (
    ZoneIndex,
    pearson,
    run_probe,
    socrata_dataset_of,
    soda_walk,
    spearman,
    summarise,
    weather_archive,
)
from scripts.analysis.snowfall_events import (
    Event,
    event_days,
    fetch_daily_snowfall,
    segment_events,
    winter_type_filter,
)

logger = logging.getLogger(__name__)

REQUEST_SOURCE = "SRC-WPG-311"
BOUNDARY_SOURCE = "SRC-WPG-PLOW-ZONE"
SCHEDULE_SOURCE = "SRC-WPG-PLOW-SHIFT"
ADDRESS_SOURCE = "SRC-WPG-SNOW"

ZONE_FIELD = "plow_zone"
GEOM_FIELD = "the_geom"
ADDRESS_POINT_FIELD = "location"
SHIFT_FIELD = "shift_number"
SNOW_FIELD = "snowfall_sum"
COLD_FIELD = "temperature_2m_min"

# The plow schedule starts 2015-12 and BO-6 scores nothing before it. Sampling
# earlier events would size the normalisation on a period the score never runs
# over.
ERA_START = date(2015, 11, 1)
ERA_FIRST_WINTER = 2015

# BO-3's chosen threshold, measured in metric-feasibility-audit.md on
# 2026-08-08. Overridable, because a re-calibration there must be re-runnable
# here without editing code.
DEFAULT_THRESHOLD = 3.0

# BO-6's own acceptance bar: three factors claiming to be independent must not
# be pairwise correlated past this.
COLLINEARITY_LIMIT = 0.7


@dataclass
class Cell:
    """One scoring row: what BO-6 would compute a score for."""

    event: int
    zone: str
    start: date
    requests: int
    snow_cm: float
    cold_c: float
    rank: float | None


# ── Upstream reads ───────────────────────────────────────────────────────────


def fetch_boundaries() -> list[dict]:
    """Zone boundary rows — fetched once and used for both the index and points."""
    ds = socrata_dataset_of(BOUNDARY_SOURCE)
    return soda_walk(ds, f"{ZONE_FIELD},{GEOM_FIELD}")


def fetch_request_points(index: ZoneIndex) -> tuple[dict[tuple[date, str], int], dict]:
    """Winter request counts per (day, zone), assigned by point-in-polygon.

    Rows without geometry are excluded rather than distributed: 79% of the full
    table has no coordinates upstream (CLAUDE.md's escalation rule), and a
    denominator that included them would measure the fill rate instead of the
    load. The share that falls outside every zone is reported, because that is
    BO-4's hit rate on *requests* — a number ADR 0008 measured for addresses
    (100.0%) but never for requests, and the two are not the same population.
    """
    ds = socrata_dataset_of(REQUEST_SOURCE)
    where = (
        f"({winter_type_filter()}) AND geometry IS NOT NULL "
        f"AND open_date >= '{ERA_START.isoformat()}T00:00:00.000'"
    )
    rows = soda_walk(ds, "open_date,geometry", where=where)

    counts: dict[tuple[date, str], int] = defaultdict(int)
    outside = 0
    for row in rows:
        coordinates = (row.get("geometry") or {}).get("coordinates")
        if not coordinates:
            outside += 1
            continue
        zone = index.assign(float(coordinates[0]), float(coordinates[1]))
        if zone is None:
            outside += 1
            continue
        counts[(datetime.fromisoformat(row["open_date"]).date(), zone)] += 1

    hit_rate = {
        "requests_with_geometry": len(rows),
        "assigned_to_a_zone": len(rows) - outside,
        "outside_every_zone": outside,
        "hit_rate": round((len(rows) - outside) / len(rows), 4) if rows else None,
    }
    return dict(counts), hit_rate


def fetch_zone_addresses(index: ZoneIndex) -> dict[str, int]:
    """Address points per zone — BO-6's stated normalisation denominator.

    Without it the request factor is mostly a measure of how big a zone is,
    and a correlation computed against it would be reading zone size where it
    means to read winter load. ``zone_schedule_rank`` counts the same points
    for BO-2; both go through the same index so the two probes cannot disagree
    about which zone a point is in.
    """
    ds = socrata_dataset_of(ADDRESS_SOURCE)
    counts: dict[str, int] = defaultdict(int)
    for row in soda_walk(ds, f"address_id,{ADDRESS_POINT_FIELD}"):
        coordinates = (row.get(ADDRESS_POINT_FIELD) or {}).get("coordinates")
        if not coordinates:
            continue
        zone = index.assign(float(coordinates[0]), float(coordinates[1]))
        if zone is not None:
            counts[zone] += 1
    return dict(counts)


def fetch_ranks() -> dict[date, dict[str, float]]:
    """Shift number per zone, keyed by the date plowing for that event began.

    Keyed by date rather than ``snow_ban_id`` so a rank can be matched to a
    snowfall event without going through the ban table: the question here is
    which snowfall event a plow operation belongs to, and only the date answers
    that.
    """
    ds = socrata_dataset_of(SCHEDULE_SOURCE)
    rows = soda_walk(ds, f"snow_ban_id,{SHIFT_FIELD},shift_start,{ZONE_FIELD}")

    per_ban: dict[str, dict[str, float]] = defaultdict(dict)
    first_day: dict[str, date] = {}
    for row in rows:
        if not row.get("shift_start"):
            continue
        ban = row["snow_ban_id"]
        per_ban[ban][row[ZONE_FIELD]] = float(row[SHIFT_FIELD])
        day = datetime.fromisoformat(row["shift_start"]).date()
        if ban not in first_day or day < first_day[ban]:
            first_day[ban] = day
    return {first_day[ban]: ranks for ban, ranks in per_ban.items() if ban in first_day}


def fetch_zone_weather(
    boundaries: list[dict], zones: list[str], last_winter: int,
) -> dict[str, dict[date, tuple[float, float]]]:
    """Daily (snowfall, minimum temperature) at each zone's sample point.

    One archive call per zone-season, sampled at the same representative
    interior point the BO-2 counterfactual used — so the seasons already in
    ``var/probe-cache/`` are re-used rather than re-downloaded.
    """
    pieces: dict[str, list] = defaultdict(list)
    for row in boundaries:
        if not row.get(GEOM_FIELD):
            continue
        geometry = shape(row[GEOM_FIELD])
        pieces[row[ZONE_FIELD]].append(
            geometry if geometry.is_valid else make_valid(geometry),
        )

    # One wide window per zone, deliberately identical to the one
    # rank_counterfactuals used, so this re-reads that probe's cache instead of
    # re-issuing 25 x 11 rate-limited calls for the same days.
    end = min(date(last_winter + 1, 5, 1), date.today())

    series: dict[str, dict[date, tuple[float, float]]] = {}
    for zone in zones:
        # representative_point() is guaranteed inside the geometry; a centroid
        # is not, and a crescent-shaped zone would be sampled outside itself.
        point = unary_union(pieces[zone]).representative_point()
        latitude, longitude = round(point.y, 4), round(point.x, 4)
        daily: dict[date, tuple[float, float]] = {}
        for record in weather_archive(
            ERA_START, end, latitude=latitude, longitude=longitude,
        ):
            snow, cold = record.get(SNOW_FIELD), record.get(COLD_FIELD)
            if snow is None:
                continue
            daily[date.fromisoformat(record["time"])] = (
                float(snow), float(cold) if cold is not None else 0.0,
            )
        series[zone] = daily
        logger.info("zone %s @ %s,%s: %d days", zone, latitude, longitude, len(daily))
    return series


# ── Factor construction ──────────────────────────────────────────────────────


def normalise(values: list[float]) -> list[float]:
    """Min-max to 0-1, or all-zero when the input is constant.

    A constant factor is mapped to 0 rather than 0.5 or 1: it contributes
    nothing to any ranking, and saying so with a zero keeps the weighted sum
    honest about how much of the score it explains.
    """
    low, high = min(values), max(values)
    if high == low:
        return [0.0] * len(values)
    return [(v - low) / (high - low) for v in values]


def severity(cells: list[Cell], snow_weight: float) -> list[float]:
    """BO-3's weather severity: a 0-1 composite of snowfall and cold.

    Both halves are normalised over the observed events before mixing, so the
    composite is not dominated by whichever component happens to have the wider
    numeric range (centimetres against degrees).
    """
    snow = normalise([c.snow_cm for c in cells])
    # Cold enters as degrees *below* zero so that colder means more severe;
    # using the raw minimum would invert the factor.
    cold = normalise([max(0.0, -c.cold_c) for c in cells])
    return [snow_weight * s + (1 - snow_weight) * k for s, k in zip(snow, cold, strict=True)]


def rank_factor(cells: list[Cell]) -> list[float | None]:
    """Scheduling rank normalised to 0-1, ``None`` where there was no operation.

    NULL, never 0. BO-6 §有效分析窗口 is explicit that a missing rank must not
    be filled — "no shift record" does not mean "scheduled last", and it does
    not mean "scheduled first" either.
    """
    known = [c.rank for c in cells if c.rank is not None]
    if not known:
        return [None] * len(cells)
    low, high = min(known), max(known)
    span = (high - low) or 1.0
    return [None if c.rank is None else (c.rank - low) / span for c in cells]


# ── Analysis ─────────────────────────────────────────────────────────────────


def correlations(named: dict[str, list[float]]) -> dict[str, float]:
    """Pearson r for every pair, on the shared rows."""
    keys = sorted(named)
    return {
        f"{a}~{b}": round(pearson(named[a], named[b]), 4)
        for i, a in enumerate(keys)
        for b in keys[i + 1:]
    }


def variance_decomposition(cells: list[Cell], values: list[float]) -> dict:
    """Split a factor's variance into between-event and within-event parts.

    The within-event part is the one that matters for BO-8: a recommendation
    ranks zones *inside* one event, so a factor with no within-event variance
    cannot change any recommendation regardless of its weight.
    """
    overall = sum(values) / len(values)
    per_event: dict[int, list[float]] = defaultdict(list)
    for cell, value in zip(cells, values, strict=True):
        per_event[cell.event].append(value)

    total = sum((v - overall) ** 2 for v in values)
    between = sum(
        len(group) * (sum(group) / len(group) - overall) ** 2
        for group in per_event.values()
    )
    return {
        "total": round(total, 4),
        "between_event": round(between, 4),
        "within_event": round(total - between, 4),
        "within_event_share": round((total - between) / total, 4) if total else None,
    }


def rank_influence(
    cells: list[Cell], factors: dict[str, list[float]], weights: dict[str, float],
) -> dict:
    """Does dropping the rank term change how zones are ordered within an event?

    The comparison is per event and rank-based, because that is the form BO-8
    consumes — an ordering of zones for one operation, not a score level. A
    Spearman of 1.0 in every event would mean the 0.30 rank weight buys nothing.
    """
    score = [
        sum(weights[name] * factors[name][i] for name in factors)
        for i in range(len(cells))
    ]
    without = [
        sum(weights[name] * factors[name][i] for name in factors if name != "rank")
        for i in range(len(cells))
    ]

    per_event: dict[int, list[int]] = defaultdict(list)
    for index, cell in enumerate(cells):
        per_event[cell.event].append(index)

    rhos, changed = [], 0
    for indices in per_event.values():
        if len({score[i] for i in indices}) < 2:
            continue
        rho = spearman([score[i] for i in indices], [without[i] for i in indices])
        rhos.append(rho)
        if rho < 0.999:
            changed += 1
    return {
        "events_compared": len(rhos),
        "events_where_order_changes": changed,
        "spearman_with_vs_without_rank": summarise(rhos),
    }


# ── Report ───────────────────────────────────────────────────────────────────


def build_cells(
    events: list[Event],
    counts: dict[tuple[date, str], int],
    weather: dict[str, dict[date, tuple[float, float]]],
    ranks: dict[date, dict[str, float]],
    zones: list[str],
    lag_days: int,
) -> list[Cell]:
    """One row per (event, zone), with each factor's raw input attached."""
    cells: list[Cell] = []
    for number, event in enumerate(events):
        days = event_days(event)
        # Match the plow operation to the event by date, with the same lag the
        # BO-3 alignment used: plowing follows snowfall, so an operation
        # starting a few days after the snow stopped belongs to that event.
        operation = next(
            (
                zone_ranks
                for day, zone_ranks in sorted(ranks.items())
                if event.start <= day and (day - event.end).days <= lag_days
            ),
            None,
        )

        for zone in zones:
            snow = sum(weather[zone].get(d, (0.0, 0.0))[0] for d in days)
            cold = min(
                (weather[zone][d][1] for d in days if d in weather[zone]),
                default=0.0,
            )
            cells.append(
                Cell(
                    event=number,
                    zone=zone,
                    start=event.start,
                    requests=sum(counts.get((d, zone), 0) for d in days),
                    snow_cm=round(snow, 1),
                    cold_c=cold,
                    rank=(operation or {}).get(zone),
                ),
            )
    return cells


def build_report(args: argparse.Namespace) -> dict:
    last_winter = date.today().year - 1 if date.today().month < 11 else date.today().year

    boundaries = fetch_boundaries()
    index = ZoneIndex(boundaries, ZONE_FIELD, GEOM_FIELD)
    counts, hit_rate = fetch_request_points(index)
    addresses = fetch_zone_addresses(index)
    ranks = fetch_ranks()
    scheduled_zones = sorted({z for r in ranks.values() for z in r})
    weather = fetch_zone_weather(boundaries, scheduled_zones, last_winter)

    # Events are cut on the configured city point, the series BO-3 measured on,
    # so this probe scores the events the audit's BO-3 row names rather than a
    # second set of its own.
    events = segment_events(
        fetch_daily_snowfall(ERA_FIRST_WINTER, last_winter),
        args.threshold,
        args.max_gap_days,
    )

    cells = build_cells(
        events, counts, weather, ranks, scheduled_zones, args.align_lag_days,
    )

    # BO-6 normalises the request factor by zone address count, so the factor
    # measures winter load rather than how many people live in the zone. Raw
    # counts are correlated alongside it: if the two answers differ, the
    # denominator is doing the work and that has to be visible.
    density = [
        c.requests / addresses[c.zone] * 1000 if addresses.get(c.zone) else 0.0
        for c in cells
    ]
    factors_all = {
        "requests": normalise(density),
        "weather": severity(cells, args.snow_weight),
    }
    raw_counts = normalise([float(c.requests) for c in cells])
    rank_values = rank_factor(cells)

    # The three-way comparison only exists where rank does. Reported separately
    # from the two-way one rather than instead of it: the two-way panel is five
    # times larger, and a correlation measured on 19 events would be quoted with
    # a confidence it has not earned.
    with_rank = [i for i, value in enumerate(rank_values) if value is not None]
    three = {
        "requests": [factors_all["requests"][i] for i in with_rank],
        "weather": [factors_all["weather"][i] for i in with_rank],
        "rank": [rank_values[i] for i in with_rank],
    }
    rank_cells = [cells[i] for i in with_rank]
    weights = {"requests": 0.40, "rank": 0.30, "weather": 0.30}

    return {
        "probe": "score_collinearity",
        "measured_on": date.today().isoformat(),
        "threshold_cm": args.threshold,
        "max_gap_days": args.max_gap_days,
        "align_lag_days": args.align_lag_days,
        "snow_weight_in_severity": args.snow_weight,
        "era_from": ERA_START.isoformat(),
        "events": len(events),
        "zones": len(scheduled_zones),
        "cells": len(cells),
        "cells_with_rank": len(with_rank),
        "plow_operations": len(ranks),
        "operations_matched_to_an_event": len({c.event for c in rank_cells}),
        "request_spatial_hit_rate": hit_rate,
        "zones_with_invalid_geometry_repaired": index.repaired,
        "requests_per_cell": summarise([float(c.requests) for c in cells]),
        "addresses_per_zone": {z: addresses.get(z, 0) for z in scheduled_zones},
        "requests_per_1000_addresses": summarise(density),
        "correlations_all_events": correlations(factors_all),
        "correlation_raw_counts_vs_weather": round(
            pearson(raw_counts, factors_all["weather"]), 4,
        ),
        "correlations_with_rank": correlations(three),
        "variance_requests": variance_decomposition(cells, factors_all["requests"]),
        "variance_weather": variance_decomposition(cells, factors_all["weather"]),
        "variance_rank": variance_decomposition(rank_cells, three["rank"]),
        "score_unit_spread": {
            name: {
                "weight": weights[name],
                "factor_spread": round(max(values) - min(values), 4),
                "weighted_spread": round(weights[name] * (max(values) - min(values)), 4),
            }
            for name, values in three.items()
        },
        "rank_influence": rank_influence(rank_cells, three, weights),
        "collinearity_limit": COLLINEARITY_LIMIT,
    }


def print_report(report: dict) -> None:
    print(
        f"{report['events']} events at {report['threshold_cm']:g} cm x "
        f"{report['zones']} zones = {report['cells']:,} cells; "
        f"{report['cells_with_rank']} carry a scheduling rank "
        f"({report['operations_matched_to_an_event']}/{report['plow_operations']} "
        f"plow operations matched to an event)",
    )
    hit = report["request_spatial_hit_rate"]
    print(
        f"requests with geometry: {hit['requests_with_geometry']:,}, "
        f"in a zone {hit['assigned_to_a_zone']:,} ({_pct(hit['hit_rate'])}), "
        f"outside every zone {hit['outside_every_zone']:,}\n",
    )

    print("── pairwise correlation ───────────────────────────────────")
    print(f"  all {report['events']} events (rank undefined):")
    for pair, value in report["correlations_all_events"].items():
        print(f"    r({pair}) = {value:+.3f}{_flag(value)}")
    print(
        f"    r(raw counts~weather) = "
        f"{report['correlation_raw_counts_vs_weather']:+.3f}"
        f"  (no address denominator — for comparison only)",
    )
    print(f"  the {report['cells_with_rank']} cells that carry a rank:")
    for pair, value in report["correlations_with_rank"].items():
        print(f"    r({pair}) = {value:+.3f}{_flag(value)}")

    print("\n── where each factor's variance lives ─────────────────────")
    for name in ("requests", "weather", "rank"):
        block = report[f"variance_{name}"]
        print(
            f"  {name:<9} within-event {_pct(block['within_event_share'])} "
            f"of total variance",
        )
    print("  a factor with no within-event variance cannot reorder anything")

    print("\n── how far each factor can move the score ─────────────────")
    for name, block in report["score_unit_spread"].items():
        print(
            f"  {name:<9} weight {block['weight']:.2f} x spread "
            f"{block['factor_spread']:.3f} = {block['weighted_spread']:.3f} score units",
        )

    print("\n── does the rank term change the recommendation? ──────────")
    influence = report["rank_influence"]
    print(
        f"  order changes in {influence['events_where_order_changes']}/"
        f"{influence['events_compared']} events; "
        f"median spearman(with, without) = "
        f"{influence['spearman_with_vs_without_rank']['median']}",
    )

    print(
        f"\nBO-6 acceptance (design §3.3 task 3): all pairwise |r| < "
        f"{report['collinearity_limit']}, and the rank term visibly affects the "
        f"final ordering.",
    )


def _flag(value: float) -> str:
    return "  <-- exceeds the limit" if abs(value) >= COLLINEARITY_LIMIT else ""


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.1%}"


def main(argv: list[str] | None = None) -> int:
    def add_arguments(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--threshold", type=float, default=DEFAULT_THRESHOLD, metavar="CM",
            help="daily snowfall threshold events are cut on (default 3, per BO-3)",
        )
        parser.add_argument(
            "--max-gap-days", type=int, default=1,
            help="below-threshold days tolerated inside one event (default 1)",
        )
        parser.add_argument(
            "--align-lag-days", type=int, default=7,
            help="days after an event a plow operation still belongs to it (default 7)",
        )
        parser.add_argument(
            "--snow-weight", type=float, default=0.5, metavar="W",
            help="snowfall's share of the severity composite, rest is cold (default 0.5)",
        )

    return run_probe(__doc__, build_report, print_report, add_arguments, argv)


if __name__ == "__main__":
    raise SystemExit(main())
