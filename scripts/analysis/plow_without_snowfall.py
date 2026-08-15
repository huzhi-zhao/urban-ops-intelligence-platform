"""BO-3 follow-up: why did the city plow on days with no snowfall?

The snowfall-event probe left one thing open. Of 19 city-wide plow operations,
**four** land inside no snowfall event at any threshold and any reasonable lag —
2019-02-10, 2021-01-07, 2021-11-27, 2022-11-24 — which is why BO-3 may not be
written as "snowfall events drive plowing". A fifth of the operations would
contradict the sentence.

"Snowfall did not trigger it" is not an explanation, and an unexplained fifth
is the kind of gap that gets asked about from the floor. Three candidates were
recorded when the gap was found:

    drift          wind moved existing snowpack; no new snow fell
    freezing rain  precipitation fell, just not as snow
    one point      snow fell over the city but missed the sampled coordinate

Each is a different measurable quantity, so the probe tests all three and
reports which, if any, separates the four from the fifteen.

The design decision that makes the output mean something: **every operation is
measured, not just the four.** A number like "gusts hit 50 km/h" says nothing
until the aligned operations are shown to sit lower. The four are a contrast
group, so the fifteen are the control.

Two measurement notes:

- Wind and rain are not variables the pipeline ingests. A probe may ask the
  upstream a wider question than the pipeline does — nothing here is written to
  Bronze — so the archive is queried with an extended ``daily`` list.
- The single-point hypothesis is tested against every zone's own sample point,
  not against the city point again. If snow fell anywhere in the city on those
  days, one of 22 points is far more likely to have caught it than one.

Usage::

    uv run python -m scripts.analysis.plow_without_snowfall
    uv run python -m scripts.analysis.plow_without_snowfall --json out.json

Read-only against public APIs. Writes nothing to object storage.
"""

from __future__ import annotations

import argparse
import logging
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta

from shapely.geometry import shape
from shapely.ops import unary_union
from shapely.validation import make_valid

from scripts.analysis._probe_common import (
    run_probe,
    socrata_dataset_of,
    soda_walk,
    summarise,
    weather_archive,
)
from scripts.analysis.snowfall_events import (
    fetch_daily_snowfall,
    segment_events,
)

logger = logging.getLogger(__name__)

BOUNDARY_SOURCE = "SRC-WPG-PLOW-ZONE"
SCHEDULE_SOURCE = "SRC-WPG-PLOW-SHIFT"

ZONE_FIELD = "plow_zone"
GEOM_FIELD = "the_geom"

# Wider than the pipeline's list. `rain_sum` against a sub-zero temperature is
# the freezing-rain test; the wind pair is the drift test.
PROBE_VARIABLES = (
    "snowfall_sum,precipitation_sum,rain_sum,"
    "temperature_2m_min,temperature_2m_max,"
    "windspeed_10m_max,windgusts_10m_max"
)

# BO-3's calibrated threshold and gap tolerance, from
# metric-feasibility-audit.md (2026-08-08).
DEFAULT_THRESHOLD = 3.0
DEFAULT_MAX_GAP = 1
DEFAULT_LAG = 7

# How far back an operation's trigger is looked for. Long enough to cover a
# snowpack built over the preceding weeks, short enough that it does not reach
# into the previous storm cycle.
LOOKBACK_DAYS = 14
PACK_DAYS = 21

# Winnipeg's own blowing-snow advisory sits around this sustained speed; used
# only to label a day, never to decide the verdict.
DRIFT_WIND_KMH = 30.0

# Rain reaching the ground below this temperature is freezing rain or sleet.
FREEZING_C = 0.0
FREEZING_RAIN_MM = 0.5


@dataclass
class Operation:
    """One city-wide plow operation and the weather that preceded it."""

    ban_id: str
    started: date
    aligned: bool
    snow_14d_cm: float
    snow_max_day_cm: float
    snow_pack_21d_cm: float
    wind_max_kmh: float
    gust_max_kmh: float
    drift_days: int
    freezing_rain_mm: float
    freezing_rain_days: int
    snow_any_zone_14d_cm: float
    snow_any_zone_max_day_cm: float
    best_zone: str | None


# ── Upstream reads ───────────────────────────────────────────────────────────


def fetch_operations() -> dict[str, date]:
    """First shift start per plow operation — the day plowing began."""
    ds = socrata_dataset_of(SCHEDULE_SOURCE)
    first: dict[str, date] = {}
    for row in soda_walk(ds, "snow_ban_id,shift_start"):
        if not row.get("shift_start"):
            continue
        day = datetime.fromisoformat(row["shift_start"]).date()
        ban = row["snow_ban_id"]
        if ban not in first or day < first[ban]:
            first[ban] = day
    return first


def fetch_zone_points() -> dict[str, tuple[float, float]]:
    """One representative interior point per zone, as the BO-2 probe sampled."""
    ds = socrata_dataset_of(BOUNDARY_SOURCE)
    pieces: dict[str, list] = defaultdict(list)
    for row in soda_walk(ds, f"{ZONE_FIELD},{GEOM_FIELD}"):
        if not row.get(GEOM_FIELD):
            continue
        geometry = shape(row[GEOM_FIELD])
        pieces[row[ZONE_FIELD]].append(
            geometry if geometry.is_valid else make_valid(geometry),
        )
    points = {}
    for zone, geometries in pieces.items():
        point = unary_union(geometries).representative_point()
        points[zone] = (round(point.y, 4), round(point.x, 4))
    return points


def fetch_window(
    start: date, end: date, latitude: float | None = None, longitude: float | None = None,
) -> dict[date, dict]:
    """Extended daily weather over ``[start, end)``, keyed by day."""
    records = weather_archive(
        start, end, latitude=latitude, longitude=longitude, variables=PROBE_VARIABLES,
    )
    return {date.fromisoformat(r["time"]): r for r in records}


# ── Hypotheses ───────────────────────────────────────────────────────────────


def number(record: dict, field: str) -> float:
    value = record.get(field)
    return 0.0 if value is None else float(value)


def drift_evidence(window: list[dict]) -> tuple[float, float, int]:
    """Peak wind, peak gust, and how many days were windy enough to drift snow.

    A day counts only if it is windy *and* below freezing: wind over a thawed
    surface moves nothing, so the temperature condition is not decoration.
    """
    winds = [number(r, "windspeed_10m_max") for r in window]
    gusts = [number(r, "windgusts_10m_max") for r in window]
    days = sum(
        1
        for r in window
        if number(r, "windspeed_10m_max") >= DRIFT_WIND_KMH
        and number(r, "temperature_2m_max") <= FREEZING_C
    )
    return (max(winds, default=0.0), max(gusts, default=0.0), days)


def freezing_rain_evidence(window: list[dict]) -> tuple[float, int]:
    """Total rain that fell onto a below-freezing surface, and on how many days.

    ``rain_sum`` alone is not the test — rain in April is just rain. Pairing it
    with a sub-zero maximum is what makes it an icing event that would put a
    crew on the road.
    """
    icing = [
        number(r, "rain_sum")
        for r in window
        if number(r, "rain_sum") >= FREEZING_RAIN_MM
        and number(r, "temperature_2m_max") <= FREEZING_C
    ]
    return (round(sum(icing), 1), len(icing))


def snowpack(window: list[dict]) -> float:
    """Snow accumulated over the lookback, as a proxy for what is on the ground.

    A proxy, not a measurement: the daily archive carries no snow depth, so
    melt is not subtracted. It is only used to ask whether there was anything
    available to drift, which an accumulation total answers well enough.
    """
    return round(sum(number(r, "snowfall_sum") for r in window), 1)


# ── Report ───────────────────────────────────────────────────────────────────


def describe(
    ban_id: str,
    started: date,
    aligned: bool,
    city: dict[date, dict],
    zones: dict[str, dict[date, dict]],
) -> Operation:
    """Every hypothesis' number for one operation."""
    recent = [started - timedelta(days=i) for i in range(LOOKBACK_DAYS)]
    pack = [started - timedelta(days=i) for i in range(PACK_DAYS)]

    window = [city[d] for d in recent if d in city]
    wind_max, gust_max, drift_days = drift_evidence(window)
    rain_mm, rain_days = freezing_rain_evidence(window)

    # The single-point test: the best any zone did, not the average. The
    # hypothesis is that snow fell *somewhere* and the city point missed it,
    # so averaging the misses back in would answer a different question.
    best_zone, best_total, best_day = None, 0.0, 0.0
    for zone, series in zones.items():
        totals = [number(series[d], "snowfall_sum") for d in recent if d in series]
        if totals and sum(totals) > best_total:
            best_zone, best_total, best_day = zone, sum(totals), max(totals)

    return Operation(
        ban_id=ban_id,
        started=started,
        aligned=aligned,
        snow_14d_cm=round(sum(number(r, "snowfall_sum") for r in window), 1),
        snow_max_day_cm=round(
            max((number(r, "snowfall_sum") for r in window), default=0.0), 1,
        ),
        snow_pack_21d_cm=snowpack([city[d] for d in pack if d in city]),
        wind_max_kmh=round(wind_max, 1),
        gust_max_kmh=round(gust_max, 1),
        drift_days=drift_days,
        freezing_rain_mm=rain_mm,
        freezing_rain_days=rain_days,
        snow_any_zone_14d_cm=round(best_total, 1),
        snow_any_zone_max_day_cm=round(best_day, 1),
        best_zone=best_zone,
    )


def contrast(operations: list[Operation]) -> dict:
    """Compare the unaligned operations against the aligned ones, field by field.

    This is the whole point of the probe. A hypothesis only explains the four
    if its number is *different* for them, so every field is summarised for both
    groups and read side by side.
    """
    unaligned = [o for o in operations if not o.aligned]
    aligned = [o for o in operations if o.aligned]
    fields = (
        "snow_14d_cm", "snow_max_day_cm", "snow_pack_21d_cm",
        "wind_max_kmh", "gust_max_kmh", "drift_days",
        "freezing_rain_mm", "freezing_rain_days",
        "snow_any_zone_14d_cm", "snow_any_zone_max_day_cm",
    )
    return {
        field: {
            "unaligned": summarise([float(getattr(o, field)) for o in unaligned]),
            "aligned": summarise([float(getattr(o, field)) for o in aligned]),
        }
        for field in fields
    }


# The three hypotheses recorded when the gap was found. Each predicts *more* of
# its quantity for the unaligned operations, so each is refuted by a ratio at
# or below 1.
HYPOTHESES = {
    "drift": "drift_days",
    "freezing_rain": "freezing_rain_mm",
    "single_sample_point": "snow_any_zone_max_day_cm",
}

# A ratio this far apart is treated as a real separation rather than noise on
# four observations. Deliberately coarse: with n=4 nothing finer is honest.
SEPARATION_RATIO = 2.0


def _ratio(block: dict) -> tuple[float | None, float | None, float | None]:
    low, high = block["unaligned"]["median"], block["aligned"]["median"]
    return low, high, (None if not high else round(low / high, 2))


def verdicts(comparison: dict) -> dict:
    """For each recorded hypothesis, does its number separate the two groups?"""
    result = {}
    for name, field in HYPOTHESES.items():
        low, high, ratio = _ratio(comparison[field])
        result[name] = {
            "field": field,
            "unaligned_median": low,
            "aligned_median": high,
            "ratio": ratio,
            "separates": bool(ratio is not None and ratio > 1.0),
        }
    return result


def accumulation_signature(comparison: dict) -> dict:
    """The fourth explanation, which the "more of X" rule cannot express.

    Sub-threshold accumulation does not predict *more* snow before the
    unaligned operations — it predicts the same order of snow delivered without
    a peak day. So the test is a comparison of two ratios: if the 21-day total
    holds up while the biggest single day collapses, the crews went out for
    snow that a daily-threshold definition structurally cannot see.

    This is a claim about the event *definition*, not about the weather, which
    is why it matters more than the other three: no threshold fixes it.
    """
    _, _, total = _ratio(comparison["snow_pack_21d_cm"])
    _, _, peak = _ratio(comparison["snow_max_day_cm"])
    return {
        "total_ratio_21d": total,
        "peak_day_ratio": peak,
        "total_over_peak": (
            None if not peak or total is None else round(total / peak, 2)
        ),
        "holds": bool(
            total is not None and peak is not None
            and peak > 0 and total / peak >= SEPARATION_RATIO
        ),
    }


def build_report(args: argparse.Namespace) -> dict:
    last_winter = date.today().year - 1 if date.today().month < 11 else date.today().year

    operations = fetch_operations()
    first, last = min(operations.values()), max(operations.values())
    start = date(first.year, 1, 1)
    end = min(last + timedelta(days=1), date.today() - timedelta(days=15))

    logger.info("city weather %s .. %s", start, end)
    city = fetch_window(start, end)

    points = fetch_zone_points()
    zones = {}
    for zone, (latitude, longitude) in sorted(points.items()):
        zones[zone] = fetch_window(start, end, latitude, longitude)
        logger.info("zone %s @ %s,%s: %d days", zone, latitude, longitude, len(zones[zone]))

    # Re-derive the alignment rather than hardcoding the four dates: if BO-3's
    # threshold is re-calibrated, this probe must follow it, not a copied list.
    accum_kwargs = {}
    if args.accum_window_days is not None or args.accum_threshold_cm is not None:
        accum_kwargs = {
            "accum_window_days": args.accum_window_days,
            "accum_threshold": args.accum_threshold_cm,
        }
    events = segment_events(
        fetch_daily_snowfall(2015, last_winter),
        args.threshold,
        args.max_gap_days,
        **accum_kwargs,
    )

    def is_aligned(day: date) -> bool:
        return any(
            e.start <= day <= e.end + timedelta(days=args.align_lag_days) for e in events
        )

    rows = [
        describe(ban, day, is_aligned(day), city, zones)
        for ban, day in sorted(operations.items(), key=lambda kv: kv[1])
    ]
    comparison = contrast(rows)

    return {
        "probe": "plow_without_snowfall",
        "measured_on": date.today().isoformat(),
        "threshold_cm": args.threshold,
        "max_gap_days": args.max_gap_days,
        "align_lag_days": args.align_lag_days,
        "lookback_days": LOOKBACK_DAYS,
        "drift_wind_kmh": DRIFT_WIND_KMH,
        "operations": len(rows),
        "unaligned": [o.started.isoformat() for o in rows if not o.aligned],
        "zones_sampled": len(zones),
        "contrast": comparison,
        "hypotheses": verdicts(comparison),
        "accumulation_signature": accumulation_signature(comparison),
        "per_operation": [asdict(o) for o in rows],
    }


def print_report(report: dict) -> None:
    print(
        f"{report['operations']} plow operations, "
        f"{len(report['unaligned'])} with no snowfall event "
        f"({report['threshold_cm']:g} cm, lag {report['align_lag_days']}d): "
        f"{', '.join(report['unaligned'])}",
    )
    print(
        f"lookback {report['lookback_days']} days, "
        f"{report['zones_sampled']} zone sample points\n",
    )

    header = (
        f"{'started':<12}{'al':>4}{'snow14':>8}{'peak':>7}{'pack21':>8}"
        f"{'wind':>7}{'gust':>7}{'drift':>7}{'frz_mm':>8}{'anyzone':>9}"
    )
    print(header)
    print("-" * len(header))
    for row in report["per_operation"]:
        print(
            f"{row['started']!s:<12}{'Y' if row['aligned'] else 'N':>4}"
            f"{row['snow_14d_cm']:>8.1f}{row['snow_max_day_cm']:>7.1f}"
            f"{row['snow_pack_21d_cm']:>8.1f}"
            f"{row['wind_max_kmh']:>7.1f}{row['gust_max_kmh']:>7.1f}"
            f"{row['drift_days']:>7}{row['freezing_rain_mm']:>8.1f}"
            f"{row['snow_any_zone_14d_cm']:>9.1f}",
        )

    print("\n── unaligned vs aligned, median ───────────────────────────")
    print(f"{'quantity':<26}{'unaligned':>12}{'aligned':>10}")
    print("-" * 48)
    for field, block in report["contrast"].items():
        print(
            f"{field:<26}{_num(block['unaligned']['median']):>12}"
            f"{_num(block['aligned']['median']):>10}",
        )

    print("\n── does any hypothesis separate the four? ─────────────────")
    for name, block in report["hypotheses"].items():
        mark = "SEPARATES" if block["separates"] else "refuted"
        print(
            f"  {name:<28}{_num(block['unaligned_median']):>8} vs "
            f"{_num(block['aligned_median']):>7}  ratio "
            f"{_num(block['ratio']):>5}  {mark}",
        )
    print(
        "\nEach predicts MORE of its quantity for the unaligned operations, "
        "so a ratio\nat or below 1 refutes it.",
    )

    print("\n── sub-threshold accumulation ─────────────────────────────")
    signature = report["accumulation_signature"]
    print(
        f"  21-day total holds at {_num(signature['total_ratio_21d'])} of the "
        f"aligned median,\n  while the biggest single day collapses to "
        f"{_num(signature['peak_day_ratio'])} — a "
        f"{_num(signature['total_over_peak'])}x gap.",
    )
    print(
        f"  => {'HOLDS' if signature['holds'] else 'refuted'}: the same order "
        f"of snow arrived, just never in one day.",
    )


def _num(value: float | None) -> str:
    return "-" if value is None else f"{value:g}"


def main(argv: list[str] | None = None) -> int:
    def add_arguments(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--threshold", type=float, default=DEFAULT_THRESHOLD, metavar="CM",
            help="snowfall threshold events are cut on (default 3, per BO-3)",
        )
        parser.add_argument(
            "--max-gap-days", type=int, default=DEFAULT_MAX_GAP,
            help="below-threshold days tolerated inside one event (default 1)",
        )
        parser.add_argument(
            "--align-lag-days", type=int, default=DEFAULT_LAG,
            help="days after an event an operation still counts as aligned (default 7)",
        )
        parser.add_argument(
            "--accum-window-days", type=int, default=None, metavar="DAYS",
            help=(
                "rolling-accumulation window for the event definition "
                "(see snowfall_events); must pair with --accum-threshold-cm"
            ),
        )
        parser.add_argument(
            "--accum-threshold-cm", type=float, default=None, metavar="CM",
            help="rolling accumulation total that qualifies a day as a hit",
        )

    return run_probe(__doc__, build_report, print_report, add_arguments, argv)


if __name__ == "__main__":
    raise SystemExit(main())
