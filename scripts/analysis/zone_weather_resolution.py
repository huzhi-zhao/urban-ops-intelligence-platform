"""Does per-zone weather carry enough spatial resolution to reorder a ranking?

F6 joins ``severity_score`` from ``dim_snowfall_event`` on the event alone, so
inside one event all 22 scored zones receive the **same** weather factor. It
lifts the whole event's scores and contributes exactly nothing to who ranks
first — the nominal three-factor score is a two-factor ordering.

The obvious fix is to sample weather per zone. Open-Meteo takes a coordinate,
so that costs one config change. The question this probe answers is whether it
would *buy* anything, and it is a measurement, not an opinion:

    if the per-zone weather factor barely varies inside an event, then its
    0.30 weight still cannot move a single position, and per-zone weather is
    a more expensive way to compute the same ordering.

``rank_counterfactuals`` already measured that zones differ by only 2.1% in
**ten-year total** snowfall. That number does not answer this question: a
ten-year sum averages away exactly the per-storm differences at issue. Two
zones can differ sharply in any one storm and still land within 2% over a
decade. So this probe segments events first and measures inside each one.

The sample points, the era window and the repair rule are taken from
``rank_counterfactuals`` rather than re-derived, so the two probes' numbers are
comparable by construction.

Reads public APIs only: the boundary dataset and the weather archive. Nothing
here touches MinIO, Trino or any Gold table.
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

from scripts.analysis._probe_common import (
    ARCHIVE_SETTLE_DAYS,
    pearson,
    run_probe,
    summarise,
    weather_archive,
)
from scripts.analysis.rank_counterfactuals import ERA_START, fetch_zone_points
from scripts.analysis.snowfall_events import segment_events

logger = logging.getLogger(__name__)

SNOW_FIELD = "snowfall_sum"
TEMP_FIELD = "temperature_2m_min"

# The production rule, from spark/jobs/etl_weather_archive.py. Hardcoding a
# different one here would make the events this probe measures inside a
# different cohort from the one Gold scores.
THRESHOLD_CM = 3.0
GAP_DAYS = 1
ACCUM_WINDOW_DAYS = 10
ACCUM_THRESHOLD_CM = 10.0

# BO-6's weather factor: half normalised snowfall, half normalised cold.
SNOW_WEIGHT = 0.5

# dim_snowfall_event.sql: MONTH(start_date) IN (11, 12, 1, 2, 3, 4).
WINTER_MONTHS = frozenset({11, 12, 1, 2, 3, 4})
# F6's weight on the weather term. The headline number is expressed in
# load-score points, which is what 0.30 x 100 converts a factor delta into.
WEATHER_WEIGHT = 0.30
SCORE_SCALE = 100.0


# ── Upstream reads ───────────────────────────────────────────────────────────


def fetch_series(
    latitude: float | None, longitude: float | None, start: date, end: date,
) -> dict[date, tuple[float, float | None]]:
    """Daily (snowfall_cm, min_temperature_c) at one point over one wide window.

    One call per point rather than one per point-season, following
    ``rank_counterfactuals.fetch_zone_snowfall``: 25 calls answer this as well
    as 275 would, and the cache key is the window.
    """
    records = weather_archive(start, end, latitude=latitude, longitude=longitude)
    series: dict[date, tuple[float, float | None]] = {}
    for record in records:
        snow = record.get(SNOW_FIELD)
        if snow is None:
            continue
        temp = record.get(TEMP_FIELD)
        series[date.fromisoformat(record["time"])] = (
            float(snow), None if temp is None else float(temp),
        )
    return series


# ── Panel ────────────────────────────────────────────────────────────────────


def event_window(start: date, end: date) -> list[date]:
    """Every calendar day the event spans, ends included."""
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def build_panel(
    events: list, zone_series: dict[str, dict[date, tuple[float, float | None]]],
) -> tuple[dict[tuple[int, str], tuple[float, float]], list[str]]:
    """(event index, zone) -> (total snowfall cm, cold magnitude c).

    Totals sum **every** day in the event window, not only the days that met
    the threshold. The threshold picked the event out of the citywide series;
    asking how much fell on a given zone during that window is a different
    question, and filtering by the citywide hit days would import the city
    point's spatial bias into the per-zone measurement.

    Cold follows dim_snowfall_event.sql: ``max(0, -min_temperature)``, with a
    missing reading contributing no cold rather than an invented one.
    """
    zones = sorted(zone_series)
    panel: dict[tuple[int, str], tuple[float, float]] = {}
    for index, event in enumerate(events):
        days = event_window(event.start, event.end)
        for zone in zones:
            series = zone_series[zone]
            readings = [series[d] for d in days if d in series]
            if not readings:
                continue
            total = sum(snow for snow, _ in readings)
            temps = [t for _, t in readings if t is not None]
            cold = max(0.0, -min(temps)) if temps else 0.0
            panel[(index, zone)] = (round(total, 2), round(cold, 2))
    return panel, zones


def severity_factors(
    panel: dict[tuple[int, str], tuple[float, float]],
) -> dict[tuple[int, str], float]:
    """Min-max each half over the WHOLE panel, then mix — dim_snowfall_event.sql.

    Normalising within each event instead would force one 1.0 and one 0.0 into
    every event and manufacture exactly the within-event spread this probe is
    trying to measure.
    """
    snows = [s for s, _ in panel.values()]
    colds = [c for _, c in panel.values()]
    snow_lo, snow_hi = min(snows), max(snows)
    cold_lo, cold_hi = min(colds), max(colds)

    def scale(value: float, lo: float, hi: float) -> float:
        return 0.0 if hi == lo else (value - lo) / (hi - lo)

    return {
        key: SNOW_WEIGHT * scale(snow, snow_lo, snow_hi)
        + (1 - SNOW_WEIGHT) * scale(cold, cold_lo, cold_hi)
        for key, (snow, cold) in panel.items()
    }


# ── Measurements ─────────────────────────────────────────────────────────────


def relative_spread_by_event(
    panel: dict[tuple[int, str], tuple[float, float]], n_events: int,
) -> list[float]:
    """(max - min) / mean of zone snowfall totals, one value per event.

    The direct per-event counterpart of rank_counterfactuals' 2.1% over ten
    years. Events whose every zone recorded zero are skipped: the ratio is
    undefined, and reporting 0 would claim perfect agreement.
    """
    spreads = []
    for index in range(n_events):
        totals = [
            snow for (i, _), (snow, _) in panel.items() if i == index
        ]
        if not totals:
            continue
        mean = sum(totals) / len(totals)
        if mean == 0:
            continue
        spreads.append(round((max(totals) - min(totals)) / mean, 4))
    return spreads


def within_event_swing(
    factors: dict[tuple[int, str], float], n_events: int,
) -> list[float]:
    """Load-score points the weather term could move WITHIN one event.

    ``0.30 x (max - min) x 100``. This is the headline: it is an upper bound on
    how far per-zone weather could push any zone past any other inside one
    event, on the same 0-100 scale F6 reports.
    """
    swings = []
    for index in range(n_events):
        values = [f for (i, _), f in factors.items() if i == index]
        if len(values) < 2:
            continue
        swings.append(round((max(values) - min(values)) * WEATHER_WEIGHT * SCORE_SCALE, 3))
    return swings


def variance_split(
    factors: dict[tuple[int, str], float], n_events: int,
) -> dict[str, float]:
    """Share of the factor's variance sitting between events vs within them.

    The repo already records 99.4% / 0.6% for the citywide factor, where the
    within-event share can only come from the panel being unbalanced. Giving
    each zone its own weather is precisely an attempt to raise that 0.6%; this
    is the number that says whether it worked.
    """
    values = list(factors.values())
    grand = sum(values) / len(values)
    per_event = {}
    for index in range(n_events):
        cell = [f for (i, _), f in factors.items() if i == index]
        if cell:
            per_event[index] = cell

    between = sum(
        len(cell) * (sum(cell) / len(cell) - grand) ** 2 for cell in per_event.values()
    )
    within = sum(
        sum((f - sum(cell) / len(cell)) ** 2 for f in cell) for cell in per_event.values()
    )
    total = between + within
    return {
        "between_event_share": round(between / total, 5) if total else None,
        "within_event_share": round(within / total, 5) if total else None,
    }


def pairwise_correlations(
    zone_series: dict[str, dict[date, tuple[float, float | None]]],
) -> dict[str, float | None]:
    """Pearson r between every pair of zones' daily snowfall series.

    A median near 1.0 means the grid handed back the same curve at every
    sample point, which is the failure mode this probe exists to detect.
    """
    zones = sorted(zone_series)
    days = sorted(set.intersection(*(set(s) for s in zone_series.values())))
    correlations = []
    for i, left in enumerate(zones):
        for right in zones[i + 1:]:
            xs = [zone_series[left][d][0] for d in days]
            ys = [zone_series[right][d][0] for d in days]
            correlations.append(pearson(xs, ys))
    return summarise([round(c, 5) for c in correlations])


def distinct_daily_values(
    zone_series: dict[str, dict[date, tuple[float, float | None]]],
) -> dict[str, float | None]:
    """How many distinct snowfall readings the zones report on the same day.

    Blunter than a correlation and harder to argue with: if 25 sample points
    return one value on almost every day, the grid has no sub-city resolution
    at all and nothing downstream can recover it.
    """
    days = sorted(set.intersection(*(set(s) for s in zone_series.values())))
    counts = [
        float(len({zone_series[z][d][0] for z in zone_series})) for d in days
    ]
    return summarise(counts)


# ── Report ───────────────────────────────────────────────────────────────────


def build_report(args: argparse.Namespace) -> dict:
    start = date.fromisoformat(args.since) if args.since else ERA_START
    # Default to a SETTLED end. weather_archive only caches windows whose end
    # is at least ARCHIVE_SETTLE_DAYS old, so an end of `today` silently
    # disables the cache and re-pays 25 archive calls on every run — which is
    # how this probe first tripped the free tier's rate limit.
    end = (
        date.fromisoformat(args.until) if args.until
        else date.today() - timedelta(days=ARCHIVE_SETTLE_DAYS + 1)
    )

    points, repaired = fetch_zone_points()
    if args.scheduled_only:
        for zone in args.unscheduled:
            points.pop(zone, None)

    logger.info("sampling %d zones over [%s, %s)", len(points), start, end)
    zone_series = {}
    for zone, (latitude, longitude) in sorted(points.items()):
        zone_series[zone] = fetch_series(latitude, longitude, start, end)
        logger.info("zone %s @ %s,%s: %d days", zone, latitude, longitude, len(zone_series[zone]))

    city = fetch_series(None, None, start, end)
    events = segment_events(
        {d: snow for d, (snow, _) in city.items()},
        THRESHOLD_CM,
        GAP_DAYS,
        ACCUM_WINDOW_DAYS,
        ACCUM_THRESHOLD_CM,
    )
    logger.info("segmented %d events on the citywide series", len(events))
    if not args.all_months:
        # dim_snowfall_event.sql keeps only Nov..Apr. Summer events would sit at
        # the bottom of the min-max range and stretch the normalisation the
        # whole measurement is expressed in.
        kept = [e for e in events if e.start.month in WINTER_MONTHS]
        logger.info("winter cohort: %d of %d events", len(kept), len(events))
        events = kept

    panel, zones = build_panel(events, zone_series)
    factors = severity_factors(panel)
    swings = within_event_swing(factors, len(events))

    return {
        "window": [start.isoformat(), end.isoformat()],
        "zones_sampled": len(zones),
        "zones_repaired": repaired,
        "sample_points": {z: list(points[z]) for z in zones},
        "events": len(events),
        "panel_cells": len(panel),
        "event_rule": {
            "threshold_cm": THRESHOLD_CM,
            "gap_days": GAP_DAYS,
            "accum_window_days": ACCUM_WINDOW_DAYS,
            "accum_threshold_cm": ACCUM_THRESHOLD_CM,
        },
        "daily_distinct_values": distinct_daily_values(zone_series),
        "pairwise_correlation": pairwise_correlations(zone_series),
        "per_event_relative_spread": summarise(
            relative_spread_by_event(panel, len(events)),
        ),
        "within_event_score_swing_points": summarise(swings),
        "within_event_score_swing_max": max(swings) if swings else None,
        "full_panel_score_range_points": round(
            (max(factors.values()) - min(factors.values())) * WEATHER_WEIGHT * SCORE_SCALE, 3,
        ) if factors else None,
        "variance": variance_split(factors, len(events)),
    }


def print_report(report: dict) -> None:
    print(f"window        : {report['window'][0]} .. {report['window'][1]}")
    print(f"zones sampled : {report['zones_sampled']}")
    if report["zones_repaired"]:
        print(f"  make_valid repaired: {', '.join(report['zones_repaired'])}")
    print(f"events        : {report['events']}  ({report['panel_cells']} event x zone cells)")
    print()

    distinct = report["daily_distinct_values"]
    print("Does the grid resolve zones at all")
    print("  distinct snowfall values across zones on one day:")
    print(f"    median {distinct['median']}  mean {distinct['mean']}  max {distinct['max']}")
    corr = report["pairwise_correlation"]
    print(f"  pairwise r between zone daily series: median {corr['median']}  min {corr['min']}")
    print()

    spread = report["per_event_relative_spread"]
    print("Per-event disagreement between zones")
    print("  (max - min) / mean of zone totals, per event:")
    print(f"    median {_pct(spread['median'])}  mean {_pct(spread['mean'])}  max {_pct(spread['max'])}")
    print("  rank_counterfactuals measured 2.1% on the TEN-YEAR total, which is a")
    print("  different quantity - a decade sum averages the per-storm gaps away.")
    print()

    swing = report["within_event_score_swing_points"]
    print("The decisive number")
    print("  load-score points the weather term could move WITHIN one event")
    print("  (0.30 x factor range x 100):")
    print(f"    median {swing['median']}  mean {swing['mean']}  max {report['within_event_score_swing_max']}")
    print(f"  for contrast, the same term's range over the whole panel: "
          f"{report['full_panel_score_range_points']} points")
    variance = report["variance"]
    print(f"  variance: {_pct(variance['between_event_share'])} between events, "
          f"{_pct(variance['within_event_share'])} within")
    print()
    print("  Read it against how far apart adjacent zones' load scores actually sit.")
    print("  A swing smaller than that gap cannot reorder anything, and per-zone")
    print("  weather would be a more expensive way to compute today's ordering.")


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.2f}%"


def main(argv: list[str] | None = None) -> int:
    def add_arguments(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--since", metavar="YYYY-MM-DD",
            help="start of the sampling window; defaults to the scheduling era "
                 "start used by rank_counterfactuals (2015-11-01)",
        )
        parser.add_argument(
            "--scheduled-only", action="store_true",
            help="drop the zones that carry no schedule, leaving the 22 that "
                 "the scoring panel actually covers",
        )
        parser.add_argument(
            "--until", metavar="YYYY-MM-DD",
            help="end of the sampling window, exclusive; defaults to just inside "
                 "the archive's settle horizon so the fetch cache engages",
        )
        parser.add_argument(
            "--all-months", action="store_true",
            help="keep events starting outside Nov..Apr, which dim_snowfall_event "
                 "excludes; off by default so the cohort matches Gold's",
        )
        parser.add_argument(
            "--unscheduled", nargs="+", default=["B/D", "X", "Downtown"],
            metavar="ZONE",
            help="zone values treated as unscheduled by --scheduled-only",
        )

    return run_probe(__doc__, build_report, print_report, add_arguments, argv)


if __name__ == "__main__":
    raise SystemExit(main())
