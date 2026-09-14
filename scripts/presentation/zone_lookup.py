"""Render the two `carrier: lookup` figures into one self-contained page that
answers a resident's own two questions about their plow zone.

    uv run python -m scripts.presentation.zone_lookup \
        var/presentation/FIG-BO2-06.json var/presentation/FIG-BO2-07.json
    uv run python -m scripts.presentation.zone_lookup --index

Why this is a separate renderer from `render_html.py`: that module draws deck
slides — one payload, one chart, one PowerPoint box, sized in inches. This page
has no slide, draws no chart, and is read by someone choosing their own zone
from a menu. Sharing the module would mean a `SLIDE_SLOTS` entry for a page
that is not in the deck.

🔴 **Every number on the page is computed here, at build time, from the frozen
payloads.** The page's JavaScript only swaps which pre-computed record is
visible. Nothing is recomputed in the browser and nothing is fetched: the page
works from a file:// URL with the wifi off, same constraint as the figures
(design 20260903 §3.1, C7).

🔴 **Ratios are computed after summing counts, never averaged across zones.**
`fig_bo2_07` deliberately emits transition *counts* at
(plow_zone, prev_shift, next_shift) and no percentages, for the reason
`.claude/rules/gold-sql.md` R3 gives: a mean of per-zone hit rates weights a
zone with 18 transitions the same as the whole city, and returns a plausible
wrong number.

🔴 **This page is not a zone-status lookup, and its wording has to keep saying
so.** BO §0.1 rules out "除雪状态查询地图、分区查询 app、实时进度看板" because the
City already ships Know Your Zone and a near-real-time clearing map, which
answer *current state for one address*. This page answers neither: it reports a
zone's position across the 19 completed operations and the backtested hit rate
of one rule. It must never carry the City tool's name, must never imply it
knows about the operation happening now, and must never be described as
telling anyone whether their street has been cleared.

🔴 **The three no-schedule zones are rendered as an answer, not as an empty
card.** B/D · X · Downtown carry 6.02% of the city's addresses and are absent
from the 22-zone rank panel by construction (ADR 0010 §5 Q5). A lookup that
silently returned nothing for them would read as a bug to the one reader who
most needs to be told the data does not cover them.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path
from typing import Any

PROFILE_FIG = "FIG-BO2-06"
TRANSITION_FIG = "FIG-BO2-07"
DEFAULT_OUT_DIR = Path("var/presentation/html")
PAGE_NAME = "zone-lookup.html"
INDEX_NAME = "index.html"
SHIFTS = (1, 2, 3, 4, 5)


class LookupError(ValueError):
    """A payload is missing, is the wrong figure, or lost a column."""


def _rows_as_dicts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    columns = payload["columns"]
    return [dict(zip(columns, row, strict=True)) for row in payload["rows"]]


def load_payload(path: Path, expected_fig_id: str) -> dict[str, Any]:
    """Read one `scripts.eda.run --json` export and refuse anything else.

    The check is on `fig_id`, not on the filename: two payloads in the wrong
    order would otherwise build a page whose every number is silently drawn
    from the wrong query.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LookupError(f"{path}: not found — run `make eda-export` first") from exc
    for key in ("fig_id", "columns", "rows", "header"):
        if key not in payload:
            raise LookupError(f"{path}: missing '{key}' — not a scripts.eda.run --json export")
    if payload["fig_id"] != expected_fig_id:
        raise LookupError(f"{path}: holds {payload['fig_id']}, expected {expected_fig_id}")
    return payload


def summarise_transitions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold the (zone, prev, next) counts into per-zone and city-wide totals.

    Returned counts only. The page turns them into percentages once, at the
    point of display, so that a city-wide rate is a ratio of city-wide sums.
    """
    per_zone: dict[str, dict[str, int]] = {}
    city = {"transitions": 0, "exact": 0, "within1": 0}
    next_marginal = dict.fromkeys(SHIFTS, 0)
    after_first = {"transitions": 0, "shift_sum": 0}
    after_late = {"transitions": 0, "shift_sum": 0}
    for row in rows:
        zone = row["plow_zone"]
        prev_shift, next_shift = int(row["prev_shift"]), int(row["next_shift"])
        count = int(row["transitions"])
        bucket = per_zone.setdefault(zone, {"transitions": 0, "exact": 0, "within1": 0})
        bucket["transitions"] += count
        city["transitions"] += count
        if prev_shift == next_shift:
            bucket["exact"] += count
            city["exact"] += count
        if abs(prev_shift - next_shift) <= 1:
            bucket["within1"] += count
            city["within1"] += count
        next_marginal[next_shift] = next_marginal.get(next_shift, 0) + count
        # The rotation check: if the city rotated its zones, an operation spent
        # in shift 1 would tend to be followed by a late one. These two sums
        # are what says whether it does, and they are reported as the two means
        # rather than as a verdict word.
        target = after_first if prev_shift == 1 else (after_late if prev_shift >= 4 else None)
        if target is not None:
            target["transitions"] += count
            target["shift_sum"] += count * next_shift
    # The comparator: the single fixed shift that would have scored best under
    # the same +-1 rule. Without it, "83% within one shift" cannot be read —
    # most cells sit in shifts 1-3, so a constant guess already scores highly.
    best_fixed = max(
        SHIFTS,
        key=lambda s: sum(c for shift, c in next_marginal.items() if abs(shift - s) <= 1),
    )
    city["best_fixed_shift"] = best_fixed
    city["best_fixed_within1"] = sum(
        c for shift, c in next_marginal.items() if abs(shift - best_fixed) <= 1
    )
    return {
        "per_zone": per_zone,
        "city": city,
        "after_first": after_first,
        "after_late": after_late,
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if not denominator else round(100.0 * numerator / denominator, 1)


def _mean(total: int, count: int) -> float | None:
    return None if not count else round(total / count, 2)


def build_records(
    profile_rows: list[dict[str, Any]], transitions: dict[str, Any]
) -> list[dict[str, Any]]:
    """One display record per plow zone, in the profile query's own order.

    The average-rank position is computed over the zones that actually carry a
    schedule, so a zone is never told it is "22nd of 25" against three zones
    that were never in the ranking.
    """
    scheduled = [r for r in profile_rows if int(r["operations"] or 0) > 0]
    order = sorted(scheduled, key=lambda r: (float(r["mean_shift"]), r["plow_zone"]))
    position = {r["plow_zone"]: i + 1 for i, r in enumerate(order)}
    per_zone = transitions["per_zone"]
    records = []
    for row in profile_rows:
        zone = row["plow_zone"]
        operations = int(row["operations"] or 0)
        counts = per_zone.get(zone, {"transitions": 0, "exact": 0, "within1": 0})
        records.append(
            {
                "plow_zone": zone,
                "has_plow_schedule": bool(row["has_plow_schedule"]),
                "address_count": int(row["address_count"]),
                "operations": operations,
                "mean_shift": row["mean_shift"],
                "min_shift": row["min_shift"],
                "max_shift": row["max_shift"],
                "mean_early": row["mean_early"],
                "mean_late": row["mean_late"],
                "last_shift": row["last_shift"],
                "distribution": [int(row[f"shift_{s}_count"] or 0) for s in SHIFTS],
                "recent_operations": int(row["recent_operations"] or 0),
                "recent_top2_count": int(row["recent_top2_count"] or 0),
                "position": position.get(zone),
                "of_zones": len(order),
                "transitions": counts["transitions"],
                "exact": counts["exact"],
                "within1": counts["within1"],
                "exact_pct": _ratio(counts["exact"], counts["transitions"]),
                "within1_pct": _ratio(counts["within1"], counts["transitions"]),
            }
        )
    return records


def build_context(
    profile: dict[str, Any], transition: dict[str, Any]
) -> dict[str, Any]:
    """Everything the page needs, as plain JSON-safe data."""
    profile_rows = _rows_as_dicts(profile)
    if not profile_rows:
        raise LookupError(f"{PROFILE_FIG} froze zero rows — nothing to look up")
    transitions = summarise_transitions(_rows_as_dicts(transition))
    city = transitions["city"]
    records = build_records(profile_rows, transitions)
    no_schedule = [r for r in records if not r["has_plow_schedule"]]
    total_addresses = sum(r["address_count"] for r in records)
    return {
        "zones": records,
        "city": {
            "transitions": city["transitions"],
            "exact_pct": _ratio(city["exact"], city["transitions"]),
            "within1_pct": _ratio(city["within1"], city["transitions"]),
            "best_fixed_shift": city["best_fixed_shift"],
            "best_fixed_within1_pct": _ratio(city["best_fixed_within1"], city["transitions"]),
            "mean_after_first": _mean(
                transitions["after_first"]["shift_sum"], transitions["after_first"]["transitions"]
            ),
            "mean_after_late": _mean(
                transitions["after_late"]["shift_sum"], transitions["after_late"]["transitions"]
            ),
            "zones_total": len(records),
            "zones_scheduled": sum(1 for r in records if r["has_plow_schedule"]),
            "no_schedule_zones": [r["plow_zone"] for r in no_schedule],
            # Two decimals here and only here: this is the number ADR 0008 and
            # ledger C2-11 both state as 6.02%, and rounding it to 6.0% would
            # make the page and the ledger disagree by eye.
            "no_schedule_address_pct": None
            if not total_addresses
            else round(100.0 * sum(r["address_count"] for r in no_schedule) / total_addresses, 2),
        },
        "provenance": {
            "profile": {
                "source_sql": profile.get("source_sql", "?"),
                "frozen_at": profile.get("frozen_at", "?"),
                "certification": (profile.get("certification") or {}).get("status", "unknown"),
            },
            "transition": {
                "source_sql": transition.get("source_sql", "?"),
                "frozen_at": transition.get("frozen_at", "?"),
                "certification": (transition.get("certification") or {}).get("status", "unknown"),
            },
        },
    }


# ---------------------------------------------------------------------------
# Page shell
#
# No ECharts here, and that is deliberate rather than a shortcut: the only
# graphic is a five-bar distribution, which CSS draws exactly. Vendoring a
# 1 MB charting runtime into a page whose job is to answer two sentences would
# make it slower to open than to read.
# ---------------------------------------------------------------------------

_PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Where your zone sits in the order &middot; UOIP Winnipeg</title>
<style>
  :root { --ink:#12293F; --label:#6E8091; --steel:#2E6E8E; --amber:#DE7A16;
          --hairline:#E3EDF4; --warn:#A34A0C; }
  body { font-family:-apple-system,"Segoe UI",Helvetica,Arial,sans-serif; margin:0;
         padding:28px; background:#fff; color:var(--ink); -webkit-font-smoothing:antialiased; }
  .wrap { max-width:860px; margin:0 auto; }
  h1 { font-size:12px; margin:0 0 6px; font-weight:600; letter-spacing:.09em;
       text-transform:uppercase; color:var(--label); }
  h2 { font-size:22px; margin:0 0 18px; font-weight:600; }
  .picker { display:flex; align-items:center; gap:10px; flex-wrap:wrap;
            border:1px solid var(--hairline); border-radius:8px; padding:14px 16px; }
  .picker label { font-size:13px; color:var(--label); }
  select { font-size:16px; padding:6px 10px; border:1px solid #C7D6E0; border-radius:6px;
           color:var(--ink); background:#fff; min-width:230px; }
  .card { border:1px solid var(--hairline); border-radius:8px; padding:18px 20px; margin-top:16px; }
  .card h3 { font-size:16px; margin:0 0 4px; font-weight:600; }
  .card .q { font-size:12px; color:var(--label); margin:0 0 14px; }
  .headline { font-size:19px; line-height:1.45; margin:0 0 14px; }
  .headline strong { color:var(--steel); }
  .facts { font-size:14px; line-height:1.7; color:#2E3B45; margin:0; }
  .facts .k { color:var(--label); }
  .dist { margin:14px 0 4px; }
  .dist .row { display:flex; align-items:center; gap:10px; font-size:13px; margin-bottom:4px; }
  .dist .name { width:74px; color:var(--label); text-align:right; }
  .dist .bar { height:14px; background:var(--steel); border-radius:3px; min-width:2px; }
  .dist .bar.zero { background:var(--hairline); }
  .dist .n { color:#2E3B45; }
  .empty { font-size:16px; line-height:1.6; color:var(--warn); margin:0; }
  .caveats { margin-top:22px; border-top:1px solid var(--hairline); padding-top:14px; }
  .caveats h4 { font-size:12px; letter-spacing:.06em; text-transform:uppercase;
                color:var(--warn); margin:0 0 8px; }
  .caveats ul { margin:0; padding-left:18px; font-size:13px; line-height:1.65; color:#42525E; }
  .provenance { margin-top:18px; border-top:1px solid var(--hairline); padding-top:8px;
                font-size:11px; color:#8A97A0; line-height:1.6; }
  a { color:var(--steel); }
</style>
</head>
<body><div class="wrap">
<h1>Urban Operations Intelligence Platform &middot; Winnipeg</h1>
<h2>Where your zone sits in the order</h2>

<div class="picker">
  <label for="zone">Plow zone</label>
  <select id="zone"></select>
  <span class="q" id="zoneMeta"></span>
</div>

<div class="card">
  <h3>Why is my street cleared later than my friend&rsquo;s?</h3>
  <p class="q">Answered by looking the schedule up, not by predicting it.</p>
  <div id="q1"></div>
</div>

<div class="card">
  <h3>Will my zone be earlier next time?</h3>
  <p class="q">Answered as a hit rate for one rule, backtested operation by operation.</p>
  <div id="q2"></div>
</div>

<div class="caveats">
  <h4>&#128721; What this page is not</h4>
  <ul>
    <li><strong>It is a plan, not a record.</strong> Every number is the published plowing
        schedule. The city does not publish when a zone was actually finished, so nothing here
        measures how long you waited.</li>
    <li><strong>The unit is a zone, not a street.</strong> There are __ZONES_TOTAL__ zones for the
        whole city; streets inside one zone share one answer.</li>
    <li><strong>Residential streets only.</strong> These operations are the residential
        (&ldquo;know your zone&rdquo;) parking bans. Main routes run on a different priority
        system that this data does not describe.</li>
    <li><strong>__NO_SCHEDULE_COUNT__ zones have no schedule at all</strong> (__NO_SCHEDULE_LIST__),
        covering __NO_SCHEDULE_PCT__% of the city&rsquo;s addresses. For those, this page says so
        instead of guessing.</li>
    <li><strong>It says nothing about right now.</strong> This is a retrospective look at
        completed operations. For whether your street has been cleared during the operation
        happening today, the City&rsquo;s own Know Your Zone tool and clearing-progress map are
        the answer &mdash; this page does not duplicate them.</li>
    <li><strong>Being later is not the same as being treated unfairly</strong>, and being earlier
        is not a reward. Almost every zone has been in the first shift at some point.</li>
  </ul>
</div>

<p class="provenance">__PROVENANCE__</p>
</div>
<script>
const DATA = __DATA__;
const CITY = DATA.city;
const byZone = {};
DATA.zones.forEach(function (z) { byZone[z.plow_zone] = z; });

function pct(v) { return v === null ? "—" : v.toFixed(1) + "%"; }
function ord(n) {
  var s = ["th", "st", "nd", "rd"], v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

function distribution(z) {
  var max = Math.max.apply(null, z.distribution) || 1;
  var rows = z.distribution.map(function (n, i) {
    var width = Math.round(300 * n / max);
    return '<div class="row"><span class="name">shift ' + (i + 1) + '</span>' +
      '<span class="bar' + (n === 0 ? ' zero' : '') + '" style="width:' +
      (n === 0 ? 2 : width) + 'px"></span>' +
      '<span class="n">' + n + (n === 1 ? " time" : " times") + '</span></div>';
  }).join("");
  return '<div class="dist">' + rows + '</div>';
}

function drift(z) {
  if (z.mean_early === null || z.mean_late === null) { return ""; }
  var delta = z.mean_late - z.mean_early;
  var word = Math.abs(delta) < 0.005 ? "unchanged" :
    (delta > 0 ? "later by " + delta.toFixed(2) + " of a shift"
               : "earlier by " + Math.abs(delta).toFixed(2) + " of a shift");
  return '<span class="k">Trend</span> first 9 operations ' + z.mean_early.toFixed(2) +
    ' &rarr; last 10 ' + z.mean_late.toFixed(2) + ' (' + word + ')<br>';
}

function renderQ1(z) {
  if (!z.has_plow_schedule || z.operations === 0) {
    return '<p class="empty">Zone ' + z.plow_zone + ' is not on the residential plowing ' +
      'schedule at all, so this data cannot tell you when it is cleared. It holds ' +
      z.address_count.toLocaleString() + ' addresses.</p>';
  }
  return '<p class="headline">Zone ' + z.plow_zone + ' is scheduled <strong>' + ord(z.position) +
    ' of ' + z.of_zones + '</strong> on average &mdash; mean shift <strong>' +
    z.mean_shift.toFixed(2) + '</strong> across ' + z.operations + ' city-wide operations.</p>' +
    distribution(z) +
    '<p class="facts"><span class="k">Range</span> earliest shift ' + z.min_shift +
    ', latest shift ' + z.max_shift + '<br>' + drift(z) +
    '<span class="k">Recently</span> in the ' + z.recent_operations +
    ' most recent operations it was in the first two shifts ' + z.recent_top2_count +
    ' times' + (z.last_shift === null ? "" :
      '; last time it was shift ' + z.last_shift) + '</p>';
}

function renderQ2(z) {
  if (!z.has_plow_schedule || z.transitions === 0) {
    return '<p class="empty">No schedule means no sequence to backtest. This page has no ' +
      'answer for zone ' + z.plow_zone + '.</p>';
  }
  return '<p class="headline">The only rule this data supports is <strong>&ldquo;next time ' +
    'looks like last time&rdquo;</strong>. For zone ' + z.plow_zone + ' it landed on the exact ' +
    'shift <strong>' + z.exact + ' of ' + z.transitions + '</strong> times (' + pct(z.exact_pct) +
    '), and within one shift <strong>' + z.within1 + ' of ' + z.transitions + '</strong> (' +
    pct(z.within1_pct) + ').</p>' +
    '<p class="facts"><span class="k">Whole city</span> ' + CITY.transitions +
    ' transitions: exact ' + pct(CITY.exact_pct) + ', within one shift ' +
    pct(CITY.within1_pct) + '<br>' +
    '<span class="k">For comparison</span> guessing shift ' + CITY.best_fixed_shift +
    ' every time, for every zone, would have been within one shift ' +
    pct(CITY.best_fixed_within1_pct) + ' of the time<br>' +
    rotation();
}

// Shift 4 and 5 are rare (44 of 418 cells), so the "after a late shift" mean
// can rest on very few transitions or none. Saying so beats printing "null".
function rotation() {
  if (CITY.mean_after_first === null || CITY.mean_after_late === null) {
    return '<span class="k">Is the city rotating?</span> too few transitions out of shift 4 ' +
      'or 5 to say.</p>';
  }
  return '<span class="k">Is the city rotating?</span> after an operation in shift 1 the next ' +
    'one averages ' + CITY.mean_after_first.toFixed(2) + '; after shift 4 or 5 it averages ' +
    CITY.mean_after_late.toFixed(2) + '. Rotation would put the larger number first.</p>';
}

function show(zone) {
  var z = byZone[zone];
  document.getElementById("zoneMeta").textContent =
    z.address_count.toLocaleString() + " addresses" +
    (z.has_plow_schedule ? "" : " · no residential plowing schedule");
  document.getElementById("q1").innerHTML = renderQ1(z);
  document.getElementById("q2").innerHTML = renderQ2(z);
}

var select = document.getElementById("zone");
DATA.zones.slice().sort(function (a, b) {
  return a.plow_zone.localeCompare(b.plow_zone);
}).forEach(function (z) {
  var option = document.createElement("option");
  option.value = z.plow_zone;
  option.textContent = "Zone " + z.plow_zone + (z.has_plow_schedule ? "" : " (no schedule)");
  select.appendChild(option);
});
select.addEventListener("change", function () { show(select.value); });
show(select.value);
</script>
</body></html>
"""


def render_page(context: dict[str, Any]) -> str:
    city = context["city"]
    provenance = (
        f"source: {html.escape(context['provenance']['profile']['source_sql'])} &middot; "
        f"{html.escape(context['provenance']['transition']['source_sql'])}<br>"
        f"frozen_at: {html.escape(context['provenance']['profile']['frozen_at'])} &middot; "
        f"gold certification: "
        f"{html.escape(context['provenance']['profile']['certification'])}<br>"
        "Every figure on this page is frozen at build time; the page makes no network request."
    )
    no_schedule = city["no_schedule_zones"]
    replacements = {
        "__DATA__": json.dumps(context, ensure_ascii=False),
        "__PROVENANCE__": provenance,
        "__ZONES_TOTAL__": str(city["zones_total"]),
        "__NO_SCHEDULE_COUNT__": str(len(no_schedule)),
        "__NO_SCHEDULE_LIST__": html.escape(", ".join(no_schedule)) or "none",
        "__NO_SCHEDULE_PCT__": "" if city["no_schedule_address_pct"] is None
        else f"{city['no_schedule_address_pct']:.2f}",
    }
    page = _PAGE
    for token, value in replacements.items():
        page = page.replace(token, value)
    return page


# ---------------------------------------------------------------------------
# Index: the entry point a reader actually lands on
# ---------------------------------------------------------------------------

_INDEX = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>UOIP &middot; Winnipeg winter operations</title>
<style>
  body { font-family:-apple-system,"Segoe UI",Helvetica,Arial,sans-serif; margin:0; padding:28px;
         background:#fff; color:#12293F; -webkit-font-smoothing:antialiased; }
  .wrap { max-width:860px; margin:0 auto; }
  h1 { font-size:12px; margin:0 0 6px; font-weight:600; letter-spacing:.09em;
       text-transform:uppercase; color:#6E8091; }
  h2 { font-size:22px; margin:0 0 20px; font-weight:600; }
  h3 { font-size:12px; letter-spacing:.06em; text-transform:uppercase; color:#6E8091;
       margin:26px 0 10px; }
  .lead { display:block; border:1px solid #2E6E8E; border-radius:8px; padding:18px 20px;
          text-decoration:none; color:inherit; }
  .lead .t { font-size:18px; font-weight:600; color:#2E6E8E; }
  .lead .d { font-size:13.5px; line-height:1.6; color:#42525E; margin-top:6px; }
  ul { margin:0; padding-left:18px; }
  li { font-size:14px; line-height:1.9; }
  a { color:#2E6E8E; }
  .note { font-size:12px; color:#8A97A0; margin-top:24px; border-top:1px solid #E3EDF4;
          padding-top:10px; line-height:1.6; }
</style>
</head>
<body><div class="wrap">
<h1>Urban Operations Intelligence Platform &middot; Winnipeg</h1>
<h2>Winter operations &mdash; what the open data says</h2>

<a class="lead" href="__LOOKUP__">
  <span class="t">Look up your plow zone &rarr;</span>
  <span class="d">Pick your plow zone and get the two answers the data actually supports:
  where it sits in the scheduled order across every city-wide operation since 2015, and how
  often &ldquo;next time looks like last time&rdquo; has held.</span>
</a>

<h3>Figures</h3>
__FIGURES__

<p class="note">Every page here is self-contained: the numbers were frozen from the Gold tables at
build time and no page makes a network request. __NOTE__</p>
</div>
</body></html>
"""


def build_index(out_dir: Path) -> str:
    """List whatever pages are in the directory, with the lookup as the entry.

    Derived from the directory rather than from a hand-kept list, for the
    reason `scripts/eda/run.py` gives about its own catalogue: a second list
    beside the files is how an entry for a page that no longer exists survives.
    """
    pages = sorted(
        path.name
        for path in out_dir.glob("*.html")
        if path.name not in (INDEX_NAME, PAGE_NAME)
    )
    if pages:
        items = "\n".join(
            f'  <li><a href="{html.escape(name)}">{html.escape(name[:-5])}</a></li>'
            for name in pages
        )
        figures = f"<ul>\n{items}\n</ul>"
        note = f"{len(pages)} figure pages listed."
    else:
        figures = (
            '<p class="note">No figure pages rendered yet &mdash; run '
            '<code>scripts.presentation.render_html</code> first.</p>'
        )
        note = ""
    return _INDEX.replace("__LOOKUP__", PAGE_NAME).replace("__FIGURES__", figures).replace(
        "__NOTE__", note
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payloads", nargs="*", type=Path, help="the two frozen JSON exports")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--index",
        action="store_true",
        help="also write index.html listing every page in --out",
    )
    return parser


def _pick(payloads: list[Path], fig_id: str) -> Path:
    named = [p for p in payloads if fig_id in p.name]
    if len(named) == 1:
        return named[0]
    raise LookupError(
        f"cannot tell which payload holds {fig_id} — pass var/presentation/{fig_id}.json"
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    try:
        if args.payloads:
            profile = load_payload(_pick(args.payloads, PROFILE_FIG), PROFILE_FIG)
            transition = load_payload(_pick(args.payloads, TRANSITION_FIG), TRANSITION_FIG)
            page = args.out / PAGE_NAME
            page.write_text(render_page(build_context(profile, transition)), encoding="utf-8")
            print(f"wrote {page}")
        elif not args.index:
            print("nothing to do: pass the two payloads, or --index", file=sys.stderr)
            return 2
        if args.index:
            index = args.out / INDEX_NAME
            index.write_text(build_index(args.out), encoding="utf-8")
            print(f"wrote {index}")
    except LookupError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
