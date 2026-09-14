"""Package the frozen presentation exports for the static portfolio site.

The catalogue is derived from `sql/presentation/` via `load_figures()`, never from
a second hand-kept list of fig_ids. Public copy is English only: captions come from
the hand-checked `render_html.ENGLISH_CAPTIONS`, and the ten figures that table does
not cover are translated here under the same rule — translate, do not soften.

Output (`dashboard/public/data/`, untracked):

- `evidence.json` — one entry per query: header, SQL, English copy, frozen rows,
  freeze time and certification. A missing export stays an explicit `missing` entry;
  an export whose certification says SAMPLE is marked `sample`, never `frozen`.
- `zones.json` — the 25 plow-zone outlines projected to SVG paths, joined to
  FIG-BO2-01's mean scheduled shift. Zones without a residential schedule carry no
  value, so the page can only draw them as outlines.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from scripts.eda.run import load_figures
from scripts.presentation.render_html import ENGLISH_CAPTIONS
from scripts.presentation.render_maps import (
    Projector,
    anchor_of,
    window_of,
    wkt_to_rings,
)
from scripts.presentation.zone_lookup import (
    PROFILE_FIG,
    TRANSITION_FIG,
    LookupError,
    build_context,
)

# Added after the original 19 (launch 20260906 §1); shown as explanatory views.
EXPLANATORY = frozenset({"FIG-BO1-04", "FIG-BO3-00", "FIG-BO4-00", "FIG-BO6-00"})

# The `carrier: lookup` pair (ADR 0013). They keep their own role rather than
# joining the core 19: those are the story's findings, and these two exist to
# answer a reader's question about one zone. Counting them as core would move a
# number the launch record states.
LOOKUP = frozenset({PROFILE_FIG, TRANSITION_FIG})

# Which story chapter a figure supports. The page's chapters, not the BO numbers.
CHAPTERS = {
    "BO-2": "Scheduled order",
    "BO-4": "Work zones and wards",
    "BO-3": "Snowfall events",
    "BO-1": "Demand and model",
    "BO-6": "Load score",
    "BO-8": "Recommendation experiment",
}

TITLES = {
    "FIG-BO1-01": "Ward and neighbourhood label counts by year",
    "FIG-BO1-02": "Winter requests per event-zone have a long tail",
    "FIG-BO1-03": "Model error, shown with its controls",
    "FIG-BO1-04": "What residents reported during snowfall events",
    "FIG-BO2-01": "Average scheduled shift by plow zone",
    "FIG-BO2-02": "Scheduled position moves over time",
    "FIG-BO2-03": "Nineteen operations, every zone accounted for",
    "FIG-BO2-04": "More addresses do not explain earlier scheduling",
    "FIG-BO2-05": "Parking bans and plow operations are different records",
    "FIG-BO2-06": "Where each zone has sat in the order",
    "FIG-BO2-07": "How often the last position repeats",
    "FIG-BO3-00": "Where does one snowfall end?",
    "FIG-BO3-01": "Eighteen winters, ninety-nine snowfall events",
    "FIG-BO3-02": "Snow seasons differ sharply",
    "FIG-BO3-03": "Plowing often starts before the snow stops",
    "FIG-BO4-00": "Twenty-five plow zones",
    "FIG-BO4-01": "A work zone is not a ward",
    "FIG-BO4-02": "One ward rarely holds a whole work zone",
    "FIG-BO4-03": "Spatial assignment, with its denominator",
    "FIG-BO6-00": "Three raw observations per event-zone",
    "FIG-BO6-01": "A complete panel can hold incomplete evidence",
    "FIG-BO6-02": "Nominal weights are not observed influence",
    "FIG-BO6-03": "Two score profiles, two different scales",
    "FIG-BO8-01": "Rank displacement is not improvement",
    "FIG-BO8-02": "Most cases have no single dominant factor",
}

COPY = {
    "FIG-BO1-01": {
        "caption": (
            "Counted as label appearances, not requests: a request carrying both a ward and a "
            "neighbourhood produces two rows. The two lines are identical every year before 2019; "
            "from 2019 the ward count is slightly higher, by at most 16 a year."
        ),
        "must_not_say": (
            "Do not read the vertical axis as request count, and do not add the two lines. The "
            "cause of the post-2019 gap has not been investigated; do not explain it as more "
            "requests or a changed labelling rule."
        ),
    },
    "FIG-BO1-03": {
        "caption": (
            "Holdout season: 7 events, 154 event-zone cells. All three lines are drawn. M1 records "
            "MAE 7.345; the no-month control, with its month feature deliberately removed, 7.919; "
            "the expanding-mean baseline 23.628. The model and its control differ by 0.57 MAE, "
            "both differ from the baseline by about 16. This does not support “the model beats "
            "the baseline”; the baseline is more likely too weak."
        ),
        "must_not_say": (
            "Do not call this a model beating a baseline: the holdout has 7 events, the target is "
            "zero-inflated, and the control is only 7.8% worse. Do not drop the no-month line; "
            "showing only M1 against the baseline is selective presentation."
        ),
    },
    "FIG-BO1-04": {
        "caption": (
            "Winter requests across 99 snowfall events × 22 scheduled zones, by category. All six "
            "categories scan the same 2,178 cells, so WINDROW's zero means no request landed there, "
            "not that none were counted: that request type went unused for the whole window."
        ),
        "must_not_say": (
            "Do not read WINDROW's zero as a pipeline or classification failure; ICE_CONTROL "
            "receives 847 in the same column. Do not call the total “all winter requests”: it "
            "counts only requests inside event windows that land in a scheduled zone. Do not rank "
            "services by category size; this is what residents reported, not what to clear first."
        ),
    },
    "FIG-BO2-06": {
        "caption": (
            "One row per plow zone, driven from all 25 rather than from the 22 that appear in "
            "the rank panel: a zone with no residential schedule comes back with 0 operations "
            "and an explicit reason, not as a missing row. The columns describe a zone's "
            "position across the 19 completed operations: mean, range, how often it fell in "
            "each shift, and the split between the first 9 and the last 10."
        ),
        "must_not_say": (
            "Do not read the mean shift as a promise about any single operation: the range "
            "columns are on the same row because zones move. Do not read a zone with 0 "
            "operations as a data gap; those three zones have no residential schedule at all."
        ),
    },
    "FIG-BO2-07": {
        "caption": (
            "Transition counts at (zone, previous shift, next shift) across consecutive "
            "operations: 18 transitions for each of the 22 scheduled zones. Counts only, so "
            "the hit rate can be summed once over the whole city instead of averaged across "
            "zones of different sizes."
        ),
        "must_not_say": (
            "Do not call this a forecast. It measures how often the previous position repeated, "
            "over 19 operations, and it is only informative next to the control of always "
            "guessing one fixed shift. Do not average the per-zone rates: a zone contributes 18 "
            "transitions, not one."
        ),
    },
    "FIG-BO2-03": {
        "caption": (
            "The full scheduling panel: 418 cells, none missing, none duplicated. The two "
            "operations that align with no snowfall event (2021-01-07, 2026-02-26) are marked "
            "separately; they are not missing data."
        ),
        "must_not_say": "Do not read “not aligned to a snowfall event” as missing data; see FIG-BO2-05.",
    },
    "FIG-BO2-05": {
        "caption": (
            "49 parking bans map to 19 plow operations. The gap is not lost data: the 19 matched "
            "bans share one ban type, and the other 30 belong to two other types."
        ),
        "must_not_say": (
            "Do not report the 61% unmatched as a data-quality problem. Parking bans and city-wide "
            "plow operations are not one-to-one."
        ),
    },
    "FIG-BO3-02": {
        "caption": (
            "2021–2022 is the heaviest of eighteen winters: 11 events and 106.2 cm, 1.6 times the "
            "runner-up. 2024–2025 has only 2 events."
        ),
        "must_not_say": (
            "Do not read fewer events as less snow: 2024–2025's two events still total 19.3 cm. Do "
            "not compare mean severity across seasons; it is normalised over all 99 events, not "
            "per season."
        ),
    },
    "FIG-BO4-00": {
        "caption": (
            "Plow-zone boundaries as WKT, one dissolved MultiPolygon per zone: 25 rows, 22 with a "
            "residential schedule and 3 without. Eight geometries were repaired with make_valid."
        ),
        "must_not_say": (
            "Do not draw the 3 zones without a schedule as zero load; they have no schedule data, "
            "not no work, and are drawn as outlines only. Do not hide the 8 repaired geometries; "
            "area_delta_pct records how much each changed."
        ),
    },
    "FIG-BO4-03": {
        "caption": (
            "The share of geocoded requests that fall inside a plow zone, from the daily "
            "out-of-pipeline audit, shown with its denominator. Upstream, 79% of requests carry no "
            "coordinates at all, so a rate without its denominator cannot be read."
        ),
        "must_not_say": (
            "Never report the percentage alone. Without a denominator, “perfect hit rate” and "
            "“only three geocoded rows in the window” look identical."
        ),
    },
    "FIG-BO6-00": {
        "caption": (
            "The three numbers for a single event × zone are raw observations, not score factors: "
            "event snowfall in cm, winter requests for that zone and event, and the scheduled shift "
            "number. All 374 scored cells are returned; choosing one is a human decision."
        ),
        "must_not_say": (
            "Do not present load_score or the three factors as these numbers; a weighted factor "
            "reads as “0.14 cm of snow” on stage. Do not call the top-ranked cells the most "
            "representative: distinctness_rank measures visual separation only."
        ),
    },
    "FIG-BO8-02": {
        "caption": (
            "57.6% of cells have no single dominant factor (RULE-BALANCED, 431 of 748). Switching "
            "model version moves only one kind of attribution: 31 cells between WEATHER-DOMINANT and "
            "BALANCED, while RANK-DOMINANT and REQUESTS-DOMINANT are identical across versions."
        ),
        "must_not_say": (
            "RULE-NO-SCHEDULE and RULE-FALLBACK fire zero times; they are interfaces for unbuilt "
            "features, not broken rules. Do not read BALANCED as “all three factors matter "
            "equally”; it means none reached the dominance threshold."
        ),
    },
}

REPO_BLOB = "https://github.com/huzhi-zhao/urban-ops-intelligence-platform/blob/main/"

# Columns whose values are map geometry; the table view does not print them.
GEOMETRY_COLUMNS = frozenset({"geometry_wkt"})

MAP_HEIGHT = 900.0
MIN_STEP_PX = 1.5


def english_copy(fig_id: str) -> dict[str, str]:
    source = ENGLISH_CAPTIONS.get(fig_id) or COPY.get(fig_id)
    if source is None:
        raise KeyError(f"No English copy for {fig_id}")
    # The page renders severity with its own "How not to read this" treatment; the
    # 🔴 markers in the shared translation table are for the internal HTML renderer.
    cleaned = {key: value.replace("\U0001F534", "").strip() for key, value in source.items()}
    return {"title": TITLES[fig_id], **cleaned}


def _state(path: Path, payload: dict[str, Any]) -> str:
    certification = json.dumps(payload.get("certification") or {}).upper()
    return "sample" if "SAMPLE" in certification or "fixtures" in path.parts else "frozen"


def build_catalogue(source: Path) -> list[dict[str, Any]]:
    catalogue = []
    for figure in load_figures():
        fig_id = figure.fig_id
        entry: dict[str, Any] = {
            "id": fig_id,
            "bo": figure.header["bo"],
            "carrier": figure.header["carrier"],
            "schema": figure.header["schema"],
            "chapter": CHAPTERS[figure.header["bo"]],
            "role": (
                "lookup" if fig_id in LOOKUP
                else "explanatory" if fig_id in EXPLANATORY
                else "core"
            ),
            "source_sql": f"sql/presentation/{figure.path.name}",
            "source_url": f"{REPO_BLOB}sql/presentation/{figure.path.name}",
            "sql": figure.sql,
            "copy": english_copy(fig_id),
            "state": "missing",
        }
        path = source / f"{fig_id}.json"
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("fig_id") != fig_id:
                raise ValueError(f"Mismatched fig_id in {path}")
            columns, rows = payload["columns"], payload["rows"]
            if len(set(columns)) != len(columns) or any(len(r) != len(columns) for r in rows):
                raise ValueError(f"Invalid table shape in {path}")
            keep = [i for i, c in enumerate(columns) if c not in GEOMETRY_COLUMNS]
            entry.update(
                state=_state(path, payload),
                frozen_at=payload.get("frozen_at"),
                certification=payload.get("certification") or {},
                columns=[columns[i] for i in keep],
                rows=[[row[i] for i in keep] for row in rows],
            )
        catalogue.append(entry)
    return catalogue


def _path(rings: list[list[tuple[float, float]]], project: Projector) -> str:
    parts = []
    for ring in rings:
        points = [project(lon, lat) for lon, lat in ring]
        kept = [points[0]]
        for x, y in points[1:]:
            if math.hypot(x - kept[-1][0], y - kept[-1][1]) >= MIN_STEP_PX:
                kept.append((x, y))
        if len(kept) < 3:
            continue
        parts.append("M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in kept) + "Z")
    return "".join(parts)


def build_zone_map(source: Path) -> dict[str, Any] | None:
    geometry_path = source / "FIG-BO4-00.json"
    rank_path = source / "FIG-BO2-01.json"
    if not (geometry_path.exists() and rank_path.exists()):
        return None
    geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
    rank = json.loads(rank_path.read_text(encoding="utf-8"))
    gx = {c: i for i, c in enumerate(geometry["columns"])}
    rx = {c: i for i, c in enumerate(rank["columns"])}
    mean_shift = {r[rx["plow_zone"]]: r[rx["mean_shift"]] for r in rank["rows"]}

    parsed = [(row, wkt_to_rings(row[gx["geometry_wkt"]])) for row in geometry["rows"]]
    window = window_of(ring for _, rings in parsed for ring in rings).pad(0.02)
    width, height = window.canvas(MAP_HEIGHT)
    project = Projector(window, width, height)

    zones = []
    for row, rings in parsed:
        zone = row[gx["plow_zone"]]
        scheduled = bool(row[gx["has_plow_schedule"]])
        ax, ay = project(*anchor_of(row[gx["geometry_wkt"]]))
        zones.append({
            "zone": zone,
            "scheduled": scheduled,
            # A zone without a schedule has no position to show; never default it.
            "mean_shift": mean_shift.get(zone) if scheduled else None,
            "parts": len([r for r in rings if len(r) >= 3]),
            "anchor": [round(ax, 1), round(ay, 1)],
            "d": _path(rings, project),
        })
    return {
        "width": round(width, 1),
        "height": round(height, 1),
        "sources": ["FIG-BO4-00", "FIG-BO2-01"],
        "zones": zones,
    }


def build_lookup(source: Path) -> dict[str, Any] | None:
    """Per-zone records for the zone page, or None when either export is absent.

    Both figures are required and neither substitutes for the other: the profile
    answers "where has my zone sat", the transitions answer "does that repeat".
    Half the page is not a smaller page — a reader given only the first would
    read a mean as a prediction, which is the one thing FIG-BO2-06's
    `must_not_say` rules out.
    """
    profile_path = source / f"{PROFILE_FIG}.json"
    transition_path = source / f"{TRANSITION_FIG}.json"
    if not (profile_path.exists() and transition_path.exists()):
        return None
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    transition = json.loads(transition_path.read_text(encoding="utf-8"))
    for payload, expected in ((profile, PROFILE_FIG), (transition, TRANSITION_FIG)):
        if payload.get("fig_id") != expected:
            raise ValueError(f"{expected} export carries fig_id {payload.get('fig_id')!r}")
    try:
        return build_context(profile, transition)
    except LookupError as error:
        raise ValueError(f"zone lookup: {error}") from error


def build_portfolio_data(source: Path, output: Path) -> list[dict[str, Any]]:
    output.mkdir(parents=True, exist_ok=True)
    catalogue = build_catalogue(source)
    (output / "evidence.json").write_text(json.dumps(catalogue, ensure_ascii=False), "utf-8")
    for name, payload in (("zones.json", build_zone_map(source)),
                          ("lookup.json", build_lookup(source))):
        path = output / name
        # An absent export removes the file rather than leaving the last build's
        # copy behind: a stale lookup.json would be indistinguishable from a
        # fresh one in the browser, and the page has no way to tell.
        if payload is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(json.dumps(payload), "utf-8")
    return catalogue


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("var/presentation/outputjson"))
    parser.add_argument("--out", type=Path, default=Path("dashboard/public/data"))
    args = parser.parse_args()
    catalogue = build_portfolio_data(args.source, args.out)
    states = {s: sum(e["state"] == s for e in catalogue) for s in ("frozen", "sample", "missing")}
    print(f"portfolio data: {len(catalogue)} queries · {states}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
