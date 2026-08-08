"""BO-3 probe: how many snowfall events are there actually, and are they usable?

``business-objectives.md`` BO-3 carries an **estimate** — "18 winters should
yield events in the low hundreds" — and says in the same breath that it must be
measured and not carried forward as fact. It never was. Meanwhile the event
count ``N`` is the sample size of M1 (15 wards × N), the row count BO-6 scores,
and the number of backtests BO-8 can run: three layers quoting a number nobody
produced. That is the shape ``shift_end`` had before ADR 0008.

Four numbers come out of here:

    N                     events per threshold, whole period and schedule era
    duration / interval   are events distinct occurrences or one long smear?
    alignment             do the 19 plow events and 49 bans land inside events?
    ward x event non-zero the share of (ward, event) cells carrying any request

The last one is the one BO-1 has never had: 0.34 requests/day is a *daily*
figure at neighbourhood grain. An event lasts several days and the modelling
unit is the ward, so the daily number neither confirms nor refutes the panel's
usability.

Definition used here: an **event** is a maximal run of days whose daily
snowfall total reaches the threshold, tolerating gaps of up to ``--max-gap-days``
below-threshold days. The gap tolerance exists because a two-day storm with a
quiet afternoon in the middle is one operational event, not two — but it is a
parameter, not a constant, because the right value is what this probe is for.

Usage::

    uv run python -m scripts.analysis.snowfall_events
    uv run python -m scripts.analysis.snowfall_events --json out.json

Read-only against public APIs. Writes nothing to object storage.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta

from scripts.analysis._probe_common import (
    run_probe,
    socrata_dataset_of,
    soda_get,
    soda_group_walk,
    summarise,
    weather_archive,
    winters,
)

logger = logging.getLogger(__name__)

REQUEST_SOURCE = "SRC-WPG-311"
SCHEDULE_SOURCE = "SRC-WPG-PLOW-SHIFT"
BAN_SOURCE = "SRC-WPG-PARKING-BAN"

# Open-Meteo returns snowfall_sum in centimetres.
SNOW_FIELD = "snowfall_sum"
DEFAULT_THRESHOLDS = (3.0, 5.0, 8.0, 10.0)

# 311 goes back to 2008-06; the plow schedule only to 2015-12. Both periods are
# reported: the long one sizes N, the short one is the only one where alignment
# with plow events can be checked at all.
FIRST_WINTER = 2008
SCHEDULE_FIRST_WINTER = 2015

# Winter-operations request identification, measured in
# winnipeg-data-sources.md §3.4. `%ICE%` is deliberately absent: it matches
# `Serv-ice` / `Pol-ice` / `Not-ice` / `Invo-ice` and inflated the winter share
# from 1.50% to 10.40% before it was narrowed to `%ICE CONTROL%`.
WINTER_TYPE_PATTERNS = (
    "%SNOW%", "%FROZEN%", "%PLOW%", "%PLOUGH%", "%SANDING%",
    "%WINDROW%", "%ICE CONTROL%",
)


def winter_type_filter() -> str:
    """SoQL predicate selecting winter-operations requests."""
    return " OR ".join(f"upper(type) like '{p}'" for p in WINTER_TYPE_PATTERNS)


@dataclass
class Event:
    """One snowfall event: a run of days at or above the threshold."""

    start: date
    end: date
    days: int
    total_cm: float
    peak_cm: float


def segment_events(
    daily: dict[date, float], threshold: float, max_gap_days: int,
) -> list[Event]:
    """Cut a daily snowfall series into events at ``threshold``.

    Only days at or above the threshold contribute to ``total_cm``; a tolerated
    gap day is bridged but not counted as snowfall. Counting it would let a run
    of dry days inflate an event's total, which is the opposite of what the
    threshold is for.
    """
    hits = sorted(d for d, cm in daily.items() if cm >= threshold)
    events: list[Event] = []
    run: list[date] = []

    for day in hits:
        if run and (day - run[-1]).days > max_gap_days + 1:
            events.append(_event(run, daily))
            run = []
        run.append(day)
    if run:
        events.append(_event(run, daily))
    return events


def _event(run: list[date], daily: dict[date, float]) -> Event:
    totals = [daily[d] for d in run]
    return Event(
        start=run[0],
        end=run[-1],
        days=(run[-1] - run[0]).days + 1,
        total_cm=round(sum(totals), 1),
        peak_cm=round(max(totals), 1),
    )


def gaps_between(events: list[Event]) -> list[int]:
    """Days from one event's end to the next event's start, within a season.

    Cross-season gaps (a summer) are dropped: they say nothing about how often
    events recur, and including them would put a 200-day mode in the histogram.
    """
    return [
        (b.start - a.end).days
        for a, b in zip(events, events[1:], strict=False)
        if (b.start - a.end).days <= 90
    ]


# ── Upstream reads ───────────────────────────────────────────────────────────


def fetch_daily_snowfall(first_winter: int, last_winter: int) -> dict[date, float]:
    """Daily snowfall totals over every winter season in the range."""
    daily: dict[date, float] = {}
    for start, end in winters(first_winter, last_winter):
        logger.info("weather archive %s .. %s", start, end)
        for record in weather_archive(start, end):
            value = record.get(SNOW_FIELD)
            if value is None:
                continue
            daily[date.fromisoformat(record["time"])] = float(value)
    return daily


def fetch_plow_event_dates() -> dict[str, date]:
    """First shift start per ban event — when plowing for that event began.

    Keyed by ``snow_ban_id`` because that, not the row, is the event: the table
    holds one row per (event, zone).
    """
    ds = socrata_dataset_of(SCHEDULE_SOURCE)
    rows = soda_get(ds, **{
        "$select": "snow_ban_id,min(shift_start) as first_shift",
        "$group": "snow_ban_id",
        "$limit": "5000",
    })
    return {
        r["snow_ban_id"]: datetime.fromisoformat(r["first_shift"]).date()
        for r in rows
        if r.get("first_shift")
    }


def fetch_ban_dates() -> dict[str, date]:
    """Ban start date per parking-ban id."""
    ds = socrata_dataset_of(BAN_SOURCE)
    rows = soda_get(ds, **{"$select": "id,ban_start", "$limit": "5000"})
    return {
        r["id"]: datetime.fromisoformat(r["ban_start"]).date()
        for r in rows
        if r.get("ban_start")
    }


def fetch_ward_daily_requests(first_winter: int) -> dict[tuple[date, str], int]:
    """Winter-operations request counts per (day, ward).

    One grouped walk over the whole period rather than a query per event: the
    event boundaries are a function of the threshold, and re-querying for each
    of four thresholds would multiply the request count by four for no new data.

    Rows without a ward are excluded — 79% of the full table has no geography
    (CLAUDE.md's escalation rule), so a ward-panel denominator that included
    them would be measuring the fill rate, not the panel.
    """
    ds = socrata_dataset_of(REQUEST_SOURCE)
    where = (
        f"({winter_type_filter()}) AND ward IS NOT NULL "
        f"AND open_date >= '{first_winter}-11-01T00:00:00.000'"
    )
    rows = soda_group_walk(
        ds,
        select="date_trunc_ymd(open_date) as day,ward,count(*) as n",
        group="day,ward",
        order="day,ward",
        where=where,
    )
    return {
        (datetime.fromisoformat(r["day"]).date(), r["ward"]): int(r["n"])
        for r in rows
    }


# ── Analysis ─────────────────────────────────────────────────────────────────


def event_days(event: Event) -> list[date]:
    return [event.start + timedelta(days=i) for i in range((event.end - event.start).days + 1)]


def ward_panel(
    events: list[Event], counts: dict[tuple[date, str], int], wards: list[str],
) -> dict:
    """Non-zero rate of the ward × event panel — M1's actual training matrix."""
    if not events or not wards:
        return {"cells": 0, "non_zero": 0, "non_zero_rate": None, "per_cell": {}}

    per_cell: list[float] = []
    non_zero = 0
    for event in events:
        days = event_days(event)
        for ward in wards:
            total = sum(counts.get((d, ward), 0) for d in days)
            per_cell.append(float(total))
            if total:
                non_zero += 1
    cells = len(events) * len(wards)
    return {
        "cells": cells,
        "non_zero": non_zero,
        "non_zero_rate": round(non_zero / cells, 4),
        "requests_per_cell": summarise(per_cell),
    }


def alignment(
    events: list[Event], marks: dict[str, date], lag_days: int,
) -> dict:
    """Share of dated upstream marks (plow events, bans) falling inside an event.

    The window is ``[start, end + lag]``: plowing and bans *follow* snowfall,
    so requiring the mark to land strictly inside the snowfall run would score
    a correctly-timed response as a miss.
    """
    if not marks:
        return {"marks": 0, "matched": 0, "match_rate": None, "unmatched": []}
    matched, unmatched = 0, []
    for key, day in sorted(marks.items(), key=lambda kv: kv[1]):
        if any(e.start <= day <= e.end + timedelta(days=lag_days) for e in events):
            matched += 1
        else:
            unmatched.append({"id": key, "date": day.isoformat()})
    return {
        "marks": len(marks),
        "matched": matched,
        "match_rate": round(matched / len(marks), 4),
        "unmatched": unmatched,
    }


def build_report(args: argparse.Namespace) -> dict:
    """Run the probe over every threshold and return a serialisable result."""
    last_winter = date.today().year - 1 if date.today().month < 11 else date.today().year

    daily = fetch_daily_snowfall(FIRST_WINTER, last_winter)
    plow_events = fetch_plow_event_dates()
    bans = fetch_ban_dates()
    counts = fetch_ward_daily_requests(FIRST_WINTER)
    wards = sorted({ward for _, ward in counts})

    schedule_era_start = date(SCHEDULE_FIRST_WINTER, 11, 1)

    thresholds = {}
    for threshold in args.thresholds:
        events = segment_events(daily, threshold, args.max_gap_days)
        era = [e for e in events if e.start >= schedule_era_start]
        thresholds[f"{threshold:g}cm"] = {
            "events_total": len(events),
            "events_schedule_era": len(era),
            "duration_days": summarise([float(e.days) for e in events]),
            "total_cm": summarise([e.total_cm for e in events]),
            "interval_days": summarise([float(g) for g in gaps_between(events)]),
            "plow_event_alignment": alignment(era, plow_events, args.align_lag_days),
            "ban_alignment": alignment(era, bans, args.align_lag_days),
            "ward_panel_total": ward_panel(events, counts, wards),
            "ward_panel_schedule_era": ward_panel(era, counts, wards),
            "events": [asdict(e) for e in events],
        }

    return {
        "probe": "snowfall_events",
        "measured_on": date.today().isoformat(),
        "winters": [FIRST_WINTER, last_winter],
        "schedule_era_from": schedule_era_start.isoformat(),
        "max_gap_days": args.max_gap_days,
        "align_lag_days": args.align_lag_days,
        "snowfall_days_observed": len(daily),
        "plow_events": len(plow_events),
        "bans": len(bans),
        "wards": wards,
        "winter_request_rows": sum(counts.values()),
        "thresholds": thresholds,
    }


def print_report(report: dict) -> None:
    """Human-readable rendering on stdout."""
    print(
        f"days of weather archive: {report['snowfall_days_observed']:,}  "
        f"winters {report['winters'][0]}-{report['winters'][1]}  "
        f"wards {len(report['wards'])}  "
        f"winter requests with a ward: {report['winter_request_rows']:,}",
    )
    print(
        f"upstream marks: {report['plow_events']} plow events, "
        f"{report['bans']} parking bans "
        f"(alignment lag {report['align_lag_days']}d, gap tolerance "
        f"{report['max_gap_days']}d)\n",
    )

    header = f"{'thr':>6}{'N':>6}{'N(era)':>8}{'dur':>7}{'gap':>7}{'plow':>8}{'ban':>8}{'nonzero':>9}"
    print(header)
    print("-" * len(header))
    for label, block in report["thresholds"].items():
        panel = block["ward_panel_total"]["non_zero_rate"]
        print(
            f"{label:>6}{block['events_total']:>6}{block['events_schedule_era']:>8}"
            f"{block['duration_days']['median']:>7}"
            f"{block['interval_days']['median']:>7}"
            f"{_rate(block['plow_event_alignment']['match_rate']):>8}"
            f"{_rate(block['ban_alignment']['match_rate']):>8}"
            f"{_rate(panel):>9}",
        )

    print("\n  N       events over the whole archive period")
    print("  N(era)  events since the plow schedule starts (2015-11)")
    print("  dur/gap median event length and median inter-event gap, in days")
    print("  plow/ban  share of the 19 plow events / 49 bans landing in an event")
    print("  nonzero   share of ward x event cells carrying at least one request")
    print("\nBO-3 acceptance (design §3.3 task 1): N >= 60 and non-zero rate >= 70%.")


def _rate(value: float | None) -> str:
    return "-" if value is None else f"{value:.1%}"


def main(argv: list[str] | None = None) -> int:
    def add_arguments(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--thresholds", type=float, nargs="+", default=list(DEFAULT_THRESHOLDS),
            metavar="CM", help="daily snowfall thresholds to segment on",
        )
        parser.add_argument(
            "--max-gap-days", type=int, default=1,
            help="below-threshold days tolerated inside one event (default 1)",
        )
        parser.add_argument(
            "--align-lag-days", type=int, default=3,
            help="days after an event a plow event or ban may still align (default 3)",
        )

    return run_probe(__doc__, build_report, print_report, add_arguments, argv)


if __name__ == "__main__":
    raise SystemExit(main())
