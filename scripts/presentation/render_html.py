"""Render a frozen `var/presentation/<fig_id>.json` payload into a
self-contained ECharts HTML page.

    uv run python -m scripts.presentation.render_html var/presentation/FIG-BO2-01.json
    uv run python -m scripts.presentation.render_html var/presentation/*.json --out var/presentation/html

See design/20260903-presentation-figure-rendering.md §3.1/§3.2 for why this
step exists at all: `scripts.eda.run --json` freezes results to JSON and stops
there, and the conference cannot depend on a live Trino connection (C3 there,
C7 in the parent design). "Self-contained" is not a suggestion — the ECharts
runtime is vendored at `scripts/presentation/vendor/echarts.min.js` (ECharts
5.6.0) and inlined verbatim, and the data is inlined as a JS literal. No
`<script src="http...">`, no `fetch()`. A page that needs the venue's wifi is
not a page for the venue.

🔴 `FIGURE_SPEC` mirrors the design doc's §3.3 decision table — it is that
table's executable form, not a second, independent decision. Every `echarts`
carrier figure decided in §3.3 is implemented; `OUT_OF_SCOPE` is the only
exclusion, and it is rendered two other ways instead (design §3.3 note).

🔴 Captions and `must_not_say` text are authored in Chinese in the SQL headers
(they are the analyst's working language) but this page's audience reads
English, so `ENGLISH_CAPTIONS` carries a hand-checked translation per fig_id.
It translates, it does not rephrase or soften — a translation that drops a
number or a warning is worse than the Chinese original a non-reader can at
least tell they don't understand.
"""

from __future__ import annotations

import argparse
import html
import json
import statistics
from collections.abc import Callable
from pathlib import Path
from typing import Any

VENDOR_JS = Path(__file__).resolve().parent / "vendor" / "echarts.min.js"
DEFAULT_OUT_DIR = Path("var/presentation/html")

# ---------------------------------------------------------------------------
# fig_id -> (chart family, column mapping). See design §3.3 for the rationale
# behind each family choice; this dict is that table, not a second opinion.
# ---------------------------------------------------------------------------
FIGURE_SPEC: dict[str, dict[str, Any]] = {
    "FIG-BO2-01": {
        "family": "ranked_bar_whisker",
        "category": "plow_zone",
        "value": "mean_shift",
        "low": "min_shift",
        "high": "max_shift",
        "highlight": ("S", "C"),
        "value_label": "mean scheduled shift",
    },
    "FIG-BO2-02": {
        "family": "slope",
        "category": "plow_zone",
        "early": "mean_early",
        "late": "mean_late",
        "highlight": ("V", "M"),
        "early_label": "first 9 operations",
        "late_label": "last 10 operations",
    },
    "FIG-BO2-04": {
        "family": "scatter_fit",
        "x": "address_count",
        "series": {
            "all 19 operations": "mean_shift_all",
            "since 2021 (11 operations)": "mean_shift_since_2021",
        },
        "category": "plow_zone",
        "x_label": "address count",
        "y_label": "mean scheduled shift",
    },
    "FIG-BO1-02": {
        "family": "box_single",
        "q1": "p25",
        "median": "median",
        "q3": "p75",
        "high": "p95",
        "outlier": "max_count",
        "low_literal": 0.0,
        "category_label": "1,298 grid cells",
        "value_label": "winter service requests per (event, zone) cell",
        "default_width": 900,
        "default_height": 400,
    },
    "FIG-BO3-01": {
        "family": "timeline_scatter",
        "x": "start_date",
        "y": "total_snowfall_cm",
        "accum_flag": "accum_flag",
        "no_request_flag": "has_no_winter_request",
        "id_col": "snowfall_event_id",
        "y_label": "total snowfall (cm)",
        "default_width": 1100,
        "default_height": 550,
    },
    "FIG-BO3-03": {
        "family": "dot_plot",
        "category": "first_shift_date",
        "aligned": "is_aligned",
        "end_col": "days_from_event_end",
        "start_col": "days_from_event_start",
        "default_width": 960,
        "default_height": 680,
    },
    "FIG-BO4-01": {
        "family": "heatmap",
        "x": "ward",
        "y": "plow_zone",
        "value": "request_share",
        "highlight_flag": "is_dominant",
        "default_width": 1000,
        "default_height": 700,
    },
    "FIG-BO4-02": {
        "family": "ranked_bar",
        "category": "plow_zone",
        "value": "dominant_share",
        "value_label": "dominant ward's share of the zone's winter requests",
        "category_label": "plow zone",
        "threshold": 0.5,
        "default_width": 960,
        "default_height": 680,
    },
    "FIG-BO3-00": {
        # Slide 20's shape. Slide 21 reshapes it through its slot: same query,
        # a different candidate window and a different chart.
        "family": "daily_bars",
        "kind_col": "window_kind",
        "rank_col": "window_rank",
        "kind": "ambiguous_bursts",
        "rank": 1,
        "day_index": "day_index",
        "value": "snowfall_cm",
        "cumulative": "running_total_cm",
        "value_label": "daily snowfall (cm) \u2014 fourteen consecutive days",
        "default_width": 900,
        "default_height": 460,
    },
    "FIG-BO6-01": {
        "family": "heatmap_split",
        "x": "plow_zone",
        "y": "snowfall_event_id",
        "event_date": "event_start_date",
        "value": "load_score",
        "split": "score_status",
        "statuses": (
            ("scored", "3-factor (scored)"),
            ("partial_no_rank", "2-factor (partial_no_rank)"),
        ),
        "default_width": 1200,
        "default_height": 850,
    },
    "FIG-BO6-02": {
        "family": "box_multi",
        "category": "factor",
        "low": "min_contribution",
        "q1": "p25",
        "median": "median",
        "q3": "p75",
        "high": "max_contribution",
        "value_label": "weighted contribution to load score",
        "default_width": 800,
        "default_height": 400,
    },
    "FIG-BO8-01": {
        "family": "diverging_histogram",
        "facet": "model_version",
        "x": "rank_delta",
        "y": "cells",
        "default_width": 1100,
        "default_height": 550,
    },
}

# Written for the final deck's map / daily-snowfall / single-case slots, but
# no frozen JSON exists yet — they have never been run against production.
# Rendering code follows the data, not the other way round.
NOT_YET_IMPLEMENTED: tuple[str, ...] = (
    "FIG-BO4-00",  # deck slide 18/29 — zone geometry, needs a map family
    "FIG-BO6-00",  # deck slide 24 — the three raw values for one frozen case
)
# Two fig_ids carry `carrier: echarts` but never reach this generic pipeline,
# on purpose — see design §3.3:
#   FIG-BO2-01b / FIG-BO4-01b  reuse another fig's SQL (design C4-d), so they
#     are not separate fig_ids at all.
#   FIG-BO1-03  is rendered two other ways instead: a hand-built single-event
#     choropleth on the main slide, and a native pptxgenjs bar chart
#     (baseline/v1/nomonth MAE) in the appendix. Neither is a generic
#     chart family this module should own.
OUT_OF_SCOPE = (
    "FIG-BO1-03",
    # Slide 43 draws these six bars as a native pptx chart shape. The SQL
    # exists so the numbers on that shape are checkable against a frozen
    # export rather than against a launch record from a month earlier; the
    # drawing is the deck's, not this pipeline's.
    "FIG-BO1-04",
)

# ---------------------------------------------------------------------------
# A fourth table, deliberately kept out of the three-bucket accounting above.
#
# Those three buckets partition the `carrier: echarts` figures and a unit test
# asserts they equal that set exactly — so a `carrier: superset` fig_id cannot
# be added to any of them without breaking the thing that catches a new
# fig_*.sql landing unnoticed.
#
# 🔴 `carrier` answers "which chart shape", not "where it runs". The venue's
# wifi cannot be depended on (C3), so a Superset chart the deck puts on a slide
# still needs a static offline page. This table is the explicit, per-figure
# record of that decision: being listed here is a human saying "this one is
# also drawn offline", which is why `render_figure` still refuses every other
# superset payload rather than inferring the intent from the presence of a
# chart family.
# ---------------------------------------------------------------------------

OFFLINE_SUPERSET_SPEC: dict[str, dict[str, Any]] = {
    # Final deck slide 41 names two figures on one card and only the panel was
    # rendered; this is the companion. Fills launch leftover L6 on the repo
    # side — the deck's own ECHARTS label on a superset-carrier fig stays a
    # deck-side drift and is not "fixed" by editing the SQL header.
    "FIG-BO6-03": {
        "family": "faceted_level_bars",
        "facet": "score_weight_profile",
        "level": "load_level",
        "value": "cells",
        "pct": "pct_within_profile",
        "level_order": ("LOW", "MED", "HIGH", "CRITICAL"),
        "facets": (
            ("full_3factor", "full_3factor \u00b7 374 scored cells"),
            ("demand_weather_only", "demand_weather_only \u00b7 924 partial cells"),
        ),
        "facet_notes": {
            "full_3factor": "CRITICAL at 75.0 \u00b7 observed max 90.51",
            "demand_weather_only": "CRITICAL at 52.5 \u00b7 observed max 50.27, no cell has reached it",
        },
        "value_label": "cells per load_level \u2014 two scales, two coordinate systems",
        "default_width": 1000,
        "default_height": 480,
    },
}


def _die(msg: str) -> None:  # pragma: no cover - argparse error path
    raise SystemExit(msg)


def load_payload(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    for key in ("fig_id", "header", "columns", "rows"):
        if key not in payload:
            _die(f"{path}: missing '{key}' — not a scripts.eda.run --json export")
    return payload


def _rows_as_dicts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    columns = payload["columns"]
    return [dict(zip(columns, row, strict=True)) for row in payload["rows"]]


def _linear_fit(points: list[tuple[float, float]]) -> tuple[float, float]:
    """Ordinary least squares, slope and intercept. Used only for the fitted
    line ECharts draws on top of a scatter — the correlation coefficients
    themselves are the pipeline's numbers (fig_bo2_04's `CORR(...)` column),
    this is display geometry, not a second measurement."""
    mean_x = statistics.fmean(p[0] for p in points)
    mean_y = statistics.fmean(p[1] for p in points)
    num = sum((x - mean_x) * (y - mean_y) for x, y in points)
    den = sum((x - mean_x) ** 2 for x, y in points)
    if den == 0:
        return 0.0, mean_y
    slope = num / den
    return slope, mean_y - slope * mean_x


# ---------------------------------------------------------------------------
# Chart family builders: payload -> ECharts `option` (a plain, JSON-safe dict)
# ---------------------------------------------------------------------------

AMBER = "#DE7A16"
STEEL = "#2E6E8E"
MUTED = "#B9C6CE"

INK = "#12293F"          # deck headline navy
LABEL = "#6E8091"        # deck's own muted label grey
HAIRLINE = "#E3EDF4"     # deck's rule colour

# ---------------------------------------------------------------------------
# One ECharts theme for every figure, registered once per page.
#
# The default ECharts look — axis lines and ticks on every side, solid grey
# gridlines, 12px labels — reads as a工程 drawing rather than as something
# authored, and eleven pages of it look eleven different kinds of unfinished.
# Putting it here rather than in each builder means a builder only says what
# the data means (which bar is the accent, which cell is dominant) and never
# what it looks like.
#
# 🔴 It is deliberately LIGHT. The deck's own ground is near-white with navy
# type, so a dark chart lands on the slide as a hole. The Grafana dashboards
# this was compared against are dark because they are read on a screen in a
# dark room, which is a different medium, not a better taste.
#
# 🔴 And there is no value-encoding colour ramp, however good Grafana's
# green-to-yellow bars look. Slide 11's own card says it: "Colour must carry
# no value judgement — no red-for-bad. One hue, two saturations." A ramp keyed
# to magnitude states that a high load score is a bad thing, which is exactly
# the reading BO-6's launch record forbids. The gradients below run within one
# hue and encode nothing.
# ---------------------------------------------------------------------------

THEME_NAME = "uoip"


def _gradient(color: str, *, horizontal: bool = False) -> dict[str, Any]:
    """A one-hue wash from 82% to full opacity of the same colour. Decorative
    depth only — both stops are the same hue, so nothing about a bar's colour
    changes with its value."""
    return {
        "type": "linear",
        "x": 0,
        "y": 0,
        "x2": 1 if horizontal else 0,
        "y2": 0 if horizontal else 1,
        "colorStops": [
            {"offset": 0, "color": color + "D1"},
            {"offset": 1, "color": color},
        ],
    }


def _axis_theme(*, grid: bool) -> dict[str, Any]:
    return {
        "axisLine": {"show": False},
        "axisTick": {"show": False},
        "axisLabel": {"color": LABEL, "fontSize": 12},
        "nameTextStyle": {"color": LABEL, "fontSize": 12, "fontWeight": "normal"},
        "splitLine": {"show": grid, "lineStyle": {"color": HAIRLINE, "type": "dashed", "width": 1}},
    }


DECK_THEME: dict[str, Any] = {
    "color": [STEEL, AMBER, "#5B9BB8", "#E9A45C", MUTED],
    "backgroundColor": "transparent",
    # 🔴 No entry animation. Not a taste call: the PNG button reads the canvas
    # on click, and a click landing inside the ~1 s bar-grow exports a
    # half-drawn chart — a picture that is wrong in a way that looks
    # deliberate. (It also cost a diagnostic round here: screenshots taken
    # mid-animation read as a broken layout.)
    "animation": False,
    "textStyle": {
        "fontFamily": '-apple-system, "Segoe UI", Helvetica, Arial, sans-serif',
        "color": INK,
    },
    "title": {"textStyle": {"color": INK, "fontSize": 15, "fontWeight": 600}},
    # A value axis carries the gridlines; a category axis does not, or the two
    # sets cross into graph paper.
    "valueAxis": _axis_theme(grid=True),
    "categoryAxis": _axis_theme(grid=False),
    "logAxis": _axis_theme(grid=True),
    "timeAxis": _axis_theme(grid=True),
    "bar": {"itemStyle": {"borderRadius": [0, 3, 3, 0]}},
    "legend": {"textStyle": {"color": LABEL, "fontSize": 12}, "itemGap": 18},
    "tooltip": {
        "backgroundColor": "rgba(18,41,63,0.94)",
        "borderWidth": 0,
        "textStyle": {"color": "#FFFFFF", "fontSize": 12},
        "axisPointer": {"lineStyle": {"color": MUTED}, "crossStyle": {"color": MUTED}},
    },
}


def _wash_bar_fills(option: dict[str, Any]) -> dict[str, Any]:
    """Give every bar a one-hue vertical wash instead of a flat fill.

    Done as a pass over the finished option rather than inside each builder
    because it is purely cosmetic: a builder decides *which* bar is the accent
    (that is a statement about the data), and this decides what a fill looks
    like. Horizontal bars get a horizontal wash so the gradient runs along the
    bar rather than across its thickness.

    It only ever rewrites a fill it can see is a plain colour string, so a
    builder that has already said something more specific keeps it.
    """
    for series in option.get("series", []):
        if series.get("type") != "bar":
            continue
        horizontal = option_is_horizontal(option, series)
        for item in series.get("data", []):
            if not isinstance(item, dict):
                continue
            style = item.get("itemStyle")
            if isinstance(style, dict) and isinstance(style.get("color"), str):
                if style["color"] == "transparent":
                    continue  # the invisible spacer bar in a range strip
                style["color"] = _gradient(style["color"], horizontal=horizontal)
    return option


def option_is_horizontal(option: dict[str, Any], series: dict[str, Any]) -> bool:
    """True when this series' bars run left-to-right (a category y axis)."""
    y_axis = option.get("yAxis")
    if isinstance(y_axis, list):
        idx = series.get("yAxisIndex", 0)
        y_axis = y_axis[idx] if idx < len(y_axis) else {}
    return isinstance(y_axis, dict) and y_axis.get("type") == "category"


def _centre_bottom_axis_names(option: dict[str, Any]) -> dict[str, Any]:
    """Move every x-axis `name` under the middle of its axis and make room.

    ECharts parks an axis name at the axis's far end by default, where it
    overhangs the grid and is clipped by the canvas edge — on these pages
    "address count" reached the slide as "ad" and "dominant share" as "do",
    which reads as a broken render rather than as a label. Centring it is the
    only placement that cannot be clipped by the right-hand edge.

    y-axis names are left alone: they sit above the axis at the top left,
    where nothing crowds them, and centring one would rotate it into the tick
    labels.
    """
    axes = option.get("xAxis")
    axes = axes if isinstance(axes, list) else [axes]
    named = False
    for axis in axes:
        if isinstance(axis, dict) and axis.get("name"):
            axis.setdefault("nameLocation", "middle")
            axis.setdefault("nameGap", 30)
            named = True
    if not named:
        return option
    grids = option.get("grid")
    for grid in grids if isinstance(grids, list) else [grids]:
        if isinstance(grid, dict) and isinstance(grid.get("bottom"), int | float):
            grid["bottom"] = max(grid["bottom"], 52)
    return option


def build_ranked_bar_whisker(payload: dict[str, Any], spec: dict[str, Any]) -> dict:
    """22-ish categories, one mean + a [min, max] range each — the deck's
    "横向排序条形图 + min/max 须" (design §3.3, FIG-BO2-01).

    🔴 The mean is a real bar and the whisker is a `markLine`, NOT a boxplot
    with q1=median=q3 collapsed onto the mean. The five-number series makes
    that the easy spelling and it was the original one, but a zero-height box
    IS a line: the figure came out as pure hairlines with the ranking invisible
    in it. It also invites a boxplot reading — quartiles that were never
    computed. `markLine` draws in data coordinates without joining the bar
    layout, so the bar keeps its body and the range still gets drawn.
    """
    rows = sorted(_rows_as_dicts(payload), key=lambda r: r[spec["value"]])
    categories = [r[spec["category"]] for r in rows]
    highlight = set(spec.get("highlight", ()))
    bars: list[dict[str, Any]] = []
    whiskers: list[list[dict[str, Any]]] = []
    for r in rows:
        cat = r[spec["category"]]
        bars.append(
            {"value": r[spec["value"]], "itemStyle": {"color": AMBER if cat in highlight else STEEL}}
        )
        whiskers.append(
            [{"coord": [r[spec["low"]], cat]}, {"coord": [r[spec["high"]], cat]}]
        )
    return {
        "title": {
            "text": spec.get("value_label", spec["value"]),
            "left": "center",
            "textStyle": {"fontSize": 15},
        },
        "grid": {"left": 76, "right": 34, "top": 48, "bottom": 46},
        "xAxis": {
            "type": "value",
            "name": spec.get("value_label", ""),
            "nameLocation": "middle",
            "nameGap": 28,
        },
        "yAxis": {"type": "category", "data": categories, "name": "plow zone"},
        "series": [
            {
                "type": "bar",
                "data": bars,
                "barWidth": "58%",
                "markLine": {
                    "silent": True,
                    "symbol": ["rect", "rect"],
                    "symbolSize": [2, 9],
                    "lineStyle": {"color": INK, "width": 1.2, "type": "solid"},
                    "label": {"show": False},
                    "emphasis": {"disabled": True},
                    "data": whiskers,
                },
            }
        ],
        "tooltip": {"trigger": "item"},
    }


def build_slope(payload: dict[str, Any], spec: dict[str, Any]) -> dict:
    """One line per category connecting an 'earlier' value to a 'later'
    value — the standard slope-chart form for a paired before/after
    (design §3.3, FIG-BO2-02: "斜率图，仅 2 条高亮")."""
    rows = _rows_as_dicts(payload)
    highlight = set(spec.get("highlight", ()))
    early_label, late_label = spec["early_label"], spec["late_label"]
    series = []
    for r in rows:
        cat = r[spec["category"]]
        is_hl = cat in highlight
        series.append(
            {
                "name": cat,
                "type": "line",
                "data": [r[spec["early"]], r[spec["late"]]],
                "symbolSize": 6 if is_hl else 4,
                "lineStyle": {"color": AMBER if is_hl else MUTED, "width": 2.5 if is_hl else 1},
                "itemStyle": {"color": AMBER if is_hl else MUTED},
                "label": {"show": is_hl, "formatter": cat, "position": "right", "fontWeight": "bold"},
                "z": 10 if is_hl else 1,
            }
        )
    return {
        "grid": {"left": 60, "right": 60, "top": 30, "bottom": 30},
        "xAxis": {"type": "category", "data": [early_label, late_label], "boundaryGap": True},
        "yAxis": {"type": "value", "name": "mean scheduled shift", "inverse": False},
        "series": series,
        "tooltip": {"trigger": "item"},
        "legend": {"show": False},
    }


def build_scatter_fit(payload: dict[str, Any], spec: dict[str, Any]) -> dict:
    """Two scatter series over the same x, each with its own OLS trend line
    (design §3.3, FIG-BO2-04: "散点图 + 拟合线，2 个系列")."""
    rows = _rows_as_dicts(payload)
    colors = [STEEL, AMBER]
    series: list[dict[str, Any]] = []
    xs = [r[spec["x"]] for r in rows]
    x_range = (min(xs), max(xs))
    for (label, y_col), color in zip(spec["series"].items(), colors, strict=False):
        pts = [(r[spec["x"]], r[y_col]) for r in rows if r.get(y_col) is not None]
        series.append(
            {
                "name": label,
                "type": "scatter",
                "data": [[x, y] for x, y in pts],
                "itemStyle": {"color": color},
                "symbolSize": 9,
            }
        )
        if pts:
            slope, intercept = _linear_fit(pts)
            series.append(
                {
                    "name": f"{label} (fit)",
                    "type": "line",
                    "data": [
                        [x_range[0], slope * x_range[0] + intercept],
                        [x_range[1], slope * x_range[1] + intercept],
                    ],
                    "lineStyle": {"color": color, "type": "dashed", "width": 1.5},
                    "showSymbol": False,
                    "tooltip": {"show": False},
                }
            )
    return {
        "grid": {"left": 70, "right": 30, "top": 40, "bottom": 50},
        "xAxis": {"type": "value", "name": spec.get("x_label", spec["x"])},
        "yAxis": {"type": "value", "name": spec.get("y_label", "")},
        "series": series,
        "tooltip": {"trigger": "item"},
        "legend": {"top": 0, "data": list(spec["series"].keys())},
    }


def build_box_single(payload: dict[str, Any], spec: dict[str, Any]) -> dict:
    """One row, five-number summary with an explicit low anchor (design
    §3.3, FIG-BO1-02: box / percentile strip). The mean is deliberately
    never plotted — the header says it "has no meaning on this
    distribution"; only the max is marked, as an outlier next to the box,
    never shown by itself."""
    row = _rows_as_dicts(payload)[0]
    low = spec.get("low_literal", 0.0)
    q1, median, q3, high = row[spec["q1"]], row[spec["median"]], row[spec["q3"]], row[spec["high"]]
    label = spec.get("category_label", payload["fig_id"])
    series: list[dict[str, Any]] = [
        {
            "type": "boxplot",
            "data": [{"value": [low, q1, median, q3, high], "itemStyle": {"color": STEEL, "borderColor": STEEL}}],
        }
    ]
    outlier_col = spec.get("outlier")
    if outlier_col:
        outlier_value = row[outlier_col]
        series.append(
            {
                "type": "scatter",
                "data": [[outlier_value, 0]],
                "symbolSize": 10,
                "itemStyle": {"color": AMBER},
                "label": {"show": True, "formatter": f"max = {outlier_value}", "position": "top"},
                "tooltip": {"show": True},
            }
        )
    return {
        "grid": {"left": 90, "right": 40, "top": 40, "bottom": 30},
        "xAxis": {"type": "value", "name": spec.get("value_label", "")},
        "yAxis": {"type": "category", "data": [label]},
        "series": series,
        "tooltip": {"trigger": "item"},
    }


def build_box_multi(payload: dict[str, Any], spec: dict[str, Any]) -> dict:
    """One box per category from real five-number columns (design §3.3,
    FIG-BO6-02: "箱线图 3 个箱体并排") — unlike FIG-BO2-01's degenerate
    whisker, q1/q3 here are genuine quartiles from the SQL, not the mean
    repeated three times."""
    rows = _rows_as_dicts(payload)
    categories = [r[spec["category"]] for r in rows]
    data = [
        {
            "value": [r[spec["low"]], r[spec["q1"]], r[spec["median"]], r[spec["q3"]], r[spec["high"]]],
            "itemStyle": {"color": STEEL, "borderColor": STEEL},
        }
        for r in rows
    ]
    return {
        "grid": {"left": 90, "right": 40, "top": 30, "bottom": 30},
        "xAxis": {"type": "value", "name": spec.get("value_label", "")},
        "yAxis": {"type": "category", "data": categories},
        "series": [{"type": "boxplot", "data": data}],
        "tooltip": {"trigger": "item"},
    }


def build_timeline_scatter(payload: dict[str, Any], spec: dict[str, Any]) -> dict:
    """One point per snowfall event, x = start date, y = total snowfall
    (design §3.3, FIG-BO3-01). Symbol/color encode the two caveats the
    caption makes: a hollow marker means `has_no_winter_request` (thin
    311 coverage, not "no problem"); a lighter fill means `accum_flag`
    (qualifies only via the rolling-sum criterion, no single day crossed
    the threshold). The two are not mutually exclusive."""
    rows = _rows_as_dicts(payload)
    points = []
    for r in rows:
        no_request = bool(r[spec["no_request_flag"]])
        accum = bool(r[spec["accum_flag"]])
        points.append(
            {
                "value": [r[spec["x"]], r[spec["y"]]],
                "symbol": "emptyCircle" if no_request else "circle",
                "symbolSize": 8,
                "itemStyle": {
                    "color": "rgba(222,122,22,0.45)" if accum else STEEL,
                    "borderColor": AMBER if accum else STEEL,
                    "borderWidth": 1,
                },
                "name": r[spec.get("id_col", "snowfall_event_id")],
            }
        )
    return {
        "grid": {"left": 70, "right": 30, "top": 30, "bottom": 50},
        "xAxis": {"type": "time", "name": "event start date"},
        "yAxis": {"type": "value", "name": spec.get("y_label", "total snowfall (cm)")},
        "series": [{"type": "scatter", "data": points}],
        "tooltip": {"trigger": "item", "formatter": "{b}<br/>{c}"},
    }


def build_dot_plot(payload: dict[str, Any], spec: dict[str, Any]) -> dict:
    """Dual-anchor lag dot plot (design §3.3, FIG-BO3-03) — both anchors
    (days from event end, days from event start) must appear on the same
    chart, per the header's own anchor discipline; showing only one invites
    reading it as "the" response lag. Unaligned events keep their row, with
    no dots and a label suffix, rather than being dropped silently."""
    rows = _rows_as_dicts(payload)
    categories, end_data, start_data = [], [], []
    for r in rows:
        aligned = bool(r[spec["aligned"]])
        categories.append(r[spec["category"]] + ("" if aligned else " (unaligned)"))
        end_data.append(r[spec["end_col"]])
        start_data.append(r[spec["start_col"]])
    return {
        "grid": {"left": 140, "right": 30, "top": 40, "bottom": 30},
        "xAxis": {
            "type": "value",
            "name": "days relative to anchor",
            "axisLine": {"onZero": False},
        },
        "yAxis": {"type": "category", "data": categories, "name": "plow event (first shift date)"},
        "series": [
            {
                "name": "days from event end",
                "type": "scatter",
                "data": end_data,
                "symbolSize": 10,
                "itemStyle": {"color": AMBER},
                "markLine": {
                    "silent": True,
                    "symbol": "none",
                    "lineStyle": {"type": "dashed", "color": MUTED},
                    "data": [{"xAxis": 0}],
                },
            },
            {
                "name": "days from event start",
                "type": "scatter",
                "data": start_data,
                "symbol": "diamond",
                "symbolSize": 7,
                "itemStyle": {"color": STEEL},
            },
        ],
        "legend": {"top": 0, "data": ["days from event end", "days from event start"]},
        "tooltip": {"trigger": "item"},
    }


def build_heatmap(payload: dict[str, Any], spec: dict[str, Any]) -> dict:
    """Zone x ward request-share matrix (design §3.3, FIG-BO4-01: heatmap, the
    non-map alternative form the deck already names). `is_dominant` cells get
    a highlighted border so the caption's "only T and N fall entirely within
    one ward" claim is directly checkable on the grid, not just asserted."""
    rows = _rows_as_dicts(payload)
    y_cats = sorted({r[spec["y"]] for r in rows})
    x_cats = sorted({r[spec["x"]] for r in rows})
    x_labels = [str(c).title() for c in x_cats]
    data = []
    for r in rows:
        xi = x_cats.index(r[spec["x"]])
        yi = y_cats.index(r[spec["y"]])
        is_dominant = bool(r.get(spec.get("highlight_flag", ""), False))
        data.append(
            {
                "value": [xi, yi, round(r[spec["value"]], 4)],
                "itemStyle": ({"borderColor": AMBER, "borderWidth": 2} if is_dominant else {}),
            }
        )
    return {
        "grid": {"left": 90, "right": 30, "top": 50, "bottom": 110},
        "xAxis": {"type": "category", "data": x_labels, "axisLabel": {"rotate": 60, "fontSize": 9}, "name": "ward"},
        "yAxis": {"type": "category", "data": y_cats, "name": "plow zone"},
        "visualMap": {
            "min": 0,
            "max": 1,
            "calculable": True,
            "orient": "horizontal",
            "left": "center",
            "top": 0,
            "inRange": {"color": ["#F4F8FB", STEEL]},
        },
        "series": [{"type": "heatmap", "data": data, "label": {"show": False}}],
        "tooltip": {"trigger": "item"},
    }


def build_ranked_bar(payload: dict[str, Any], spec: dict[str, Any]) -> dict:
    """Single sorted value per category, no whisker (design §3.3,
    FIG-BO4-02: "横向排序条形图"). Distinct from `ranked_bar_whisker`, which
    also draws a min/max range this figure's SQL doesn't provide."""
    rows = sorted(_rows_as_dicts(payload), key=lambda r: r[spec["value"]])
    categories = [r[spec["category"]] for r in rows]
    threshold = spec.get("threshold")
    data = []
    for r in rows:
        v = r[spec["value"]]
        color = AMBER if threshold is not None and v < threshold else STEEL
        data.append({"value": v, "itemStyle": {"color": color}})
    option: dict[str, Any] = {
        "title": {"text": spec.get("value_label", spec["value"]), "left": "center", "textStyle": {"fontSize": 15}},
        "grid": {"left": 90, "right": 30, "top": 40, "bottom": 30},
        "xAxis": {"type": "value", "name": spec.get("value_label", "")},
        "yAxis": {"type": "category", "data": categories, "name": spec.get("category_label", "")},
        "series": [{"type": "bar", "data": data}],
        "tooltip": {"trigger": "item"},
    }
    if threshold is not None:
        option["series"][0]["markLine"] = {
            "silent": True,
            "symbol": "none",
            "lineStyle": {"color": MUTED, "type": "dashed"},
            "data": [{"xAxis": threshold}],
        }
    return option


def build_heatmap_split(payload: dict[str, Any], spec: dict[str, Any]) -> dict:
    """Two independently-scaled heatmap panels, one per `score_status`
    (design §3.3, FIG-BO6-01 全景) — the header is explicit that the
    `scored` and `partial_no_rank` cells must never share one color scale,
    since they are computed on different point systems (three factors vs
    two). Each panel's `visualMap` is scaled to that panel's own observed
    max, not a shared or assumed ceiling."""
    rows = _rows_as_dicts(payload)
    x_cats = sorted({r[spec["x"]] for r in rows})
    y_rows = sorted({(r[spec["event_date"]], r[spec["y"]]) for r in rows})
    y_cats = [event_id for _, event_id in y_rows]
    statuses = spec["statuses"]

    grids, x_axes, y_axes, series, visual_maps = [], [], [], [], []
    for i, (status_value, label) in enumerate(statuses):
        subset = [
            r
            for r in rows
            if r[spec["split"]] == status_value and r.get(spec["value"]) is not None
        ]
        data = [
            [x_cats.index(r[spec["x"]]), y_cats.index(r[spec["y"]]), round(r[spec["value"]], 3)]
            for r in subset
        ]
        max_value = max((r[spec["value"]] for r in subset), default=1.0)
        left = "6%" if i == 0 else "55%"
        grids.append({"left": left, "width": "39%", "top": 70, "bottom": 40})
        x_axes.append(
            {"type": "category", "data": x_cats, "gridIndex": i, "axisLabel": {"rotate": 90, "fontSize": 8}}
        )
        y_axes.append(
            {"type": "category", "data": y_cats, "gridIndex": i, "axisLabel": {"fontSize": 7, "show": i == 0}}
        )
        series.append(
            {
                "name": label,
                "type": "heatmap",
                "xAxisIndex": i,
                "yAxisIndex": i,
                "data": data,
                "label": {"show": False},
            }
        )
        visual_maps.append(
            {
                "min": 0,
                "max": max_value,
                "seriesIndex": i,
                "orient": "horizontal",
                "left": left,
                "top": 25,
                "itemWidth": 10,
                "itemHeight": 90,
                "text": [label, ""],
                "textStyle": {"fontSize": 9},
                "inRange": {"color": ["#F4F8FB", STEEL if i == 0 else AMBER]},
            }
        )
    return {
        "grid": grids,
        "xAxis": x_axes,
        "yAxis": y_axes,
        "series": series,
        "visualMap": visual_maps,
        "tooltip": {"trigger": "item"},
    }


def build_diverging_histogram(payload: dict[str, Any], spec: dict[str, Any]) -> dict:
    """One faceted bar chart per `model_version` (design §3.3, FIG-BO8-01)
    — the point the header makes is that both facets look the same, which
    is only checkable if they are drawn on identical, side-by-side axes.
    Bars diverge in color by sign: negative = moved down, positive = moved
    up, zero = unchanged."""
    rows = _rows_as_dicts(payload)
    facet_col, x_col, y_col = spec["facet"], spec["x"], spec["y"]
    facets = sorted({r[facet_col] for r in rows})
    all_x = sorted({r[x_col] for r in rows})
    grids, x_axes, y_axes, series, titles = [], [], [], [], []
    for i, facet in enumerate(facets):
        by_x = {r[x_col]: r[y_col] for r in rows if r[facet_col] == facet}
        data = []
        for x in all_x:
            v = by_x.get(x, 0)
            color = MUTED if x == 0 else (AMBER if x > 0 else STEEL)
            data.append({"value": v, "itemStyle": {"color": color}})
        left = "6%" if i == 0 else "55%"
        grids.append({"left": left, "width": "39%", "top": 55, "bottom": 40})
        x_axes.append(
            {
                "type": "category",
                "data": [str(x) for x in all_x],
                "gridIndex": i,
                "name": "rank_delta",
                "axisLabel": {"fontSize": 8, "interval": 1},
            }
        )
        y_axes.append({"type": "value", "gridIndex": i, "name": "cells" if i == 0 else ""})
        series.append({"name": facet, "type": "bar", "xAxisIndex": i, "yAxisIndex": i, "data": data})
        titles.append({"text": facet, "left": left, "top": 15, "textStyle": {"fontSize": 12}})
    return {
        "grid": grids,
        "xAxis": x_axes,
        "yAxis": y_axes,
        "series": series,
        "title": titles,
        "tooltip": {"trigger": "item"},
    }


def build_range_strip(payload: dict[str, Any], spec: dict[str, Any]) -> dict:
    """The whiskers of FIG-BO2-01 with the mean removed, plus a rule at one
    x value (final deck slide 12, FIG-BO2-01b: "range strip — fastest and
    slowest shift each zone actually reached ... mark shift 1 with a vertical
    rule").

    Same query, same rows, same sort order as `ranked_bar_whisker` — the deck
    is explicit that this is not a second query. Dropping the mean is the
    whole point: the slide asks "which zones ever touched shift 1", and a mean
    tick invites reading the strip as a ranking again.

    Drawn as a transparent base bar stacked under a visible one — ECharts'
    bar series takes a single number per point, so a [min, max] pair is read
    as an (x, y) coordinate and silently draws the wrong chart. A degenerate
    boxplot is the other tempting shortcut and is worse: it still renders a
    box, and a box spanning the whole range reads as "spent most of its time
    here", which these rows cannot support."""
    rows = sorted(_rows_as_dicts(payload), key=lambda r: r[spec["value"]])
    categories = [r[spec["category"]] for r in rows]
    highlight = set(spec.get("highlight", ()))
    rule_at = spec.get("rule_at")
    base, span = [], []
    for r in rows:
        low, high = r[spec["low"]], r[spec["high"]]
        accent = r[spec["category"]] in highlight
        base.append(low)
        span.append(
            {
                "value": high - low,
                "itemStyle": {"color": AMBER if accent else STEEL, "opacity": 1.0 if accent else 0.55},
            }
        )
    option: dict[str, Any] = {
        "title": {
            "text": spec.get("value_label", ""),
            "left": "center",
            "textStyle": {"fontSize": 15},
        },
        "grid": {"left": 70, "right": 40, "top": 45, "bottom": 35},
        "xAxis": {"type": "value", "name": "scheduled shift", "min": 0, "minInterval": 1},
        "yAxis": {"type": "category", "data": categories, "name": "plow zone"},
        "series": [
            {
                "name": "base",
                "type": "bar",
                "stack": "range",
                "data": base,
                "itemStyle": {"color": "transparent"},
                "silent": True,
                "tooltip": {"show": False},
            },
            {
                "name": "min to max",
                "type": "bar",
                "stack": "range",
                "data": span,
                "barWidth": "45%",
            },
        ],
        "tooltip": {"trigger": "item"},
    }
    if rule_at is not None:
        option["series"][1]["markLine"] = {
            "silent": True,
            "symbol": "none",
            "label": {"formatter": f"shift {rule_at}", "position": "end"},
            "lineStyle": {"color": "#12293F", "type": "solid", "width": 1.5},
            "data": [{"xAxis": rule_at}],
        }
    return option


def build_faceted_level_bars(payload: dict[str, Any], spec: dict[str, Any]) -> dict:
    """One small bar chart per `score_weight_profile` (final deck slide 41's
    companion, FIG-BO6-03: "level distribution, faceted by scale — two
    coordinate systems, never a shared axis").

    \U0001F534 The two facets get their own x axis and their own y axis, and
    that is the entire point of the figure. The level names are shared but the
    rulers are not: thresholds are scaled to each profile's own ceiling, so
    `demand_weather_only`'s CRITICAL line sits at 52.5 while `full_3factor`'s
    sits at 75. Drawing them on one axis — or as a stacked/grouped bar, which
    is the same thing with extra steps — states that a CRITICAL here equals a
    CRITICAL there, which is the one claim the launch record forbids.

    Each facet's x categories are the levels that profile actually has, not a
    padded shared list. A zero-height CRITICAL bar on the partial facet would
    read as "measured, none found"; the truthful statement is that the level
    exists on that scale and no cell has reached it — 50.27 against 52.5 — so
    it is carried as the facet's own subtitle, in numbers, rather than as an
    empty bar.
    """
    rows = _rows_as_dicts(payload)
    facet_col, level_col, value_col = spec["facet"], spec["level"], spec["value"]
    order = spec["level_order"]
    facets = spec["facets"]
    notes = spec.get("facet_notes", {})

    grids, x_axes, y_axes, series, titles = [], [], [], [], []
    for i, (facet_value, label) in enumerate(facets):
        subset = sorted(
            (r for r in rows if r[facet_col] == facet_value),
            key=lambda r: order.index(r[level_col]),
        )
        levels = [r[level_col] for r in subset]
        data = [
            {
                "value": r[value_col],
                "itemStyle": {"color": AMBER if r[level_col] == order[-1] else STEEL},
                "label": {
                    "show": True,
                    "position": "top",
                    "fontSize": 9,
                    "formatter": f"{r[value_col]}  ({r[spec['pct']]}%)",
                },
            }
            for r in subset
        ]
        left = "7%" if i == 0 else "56%"
        grids.append({"left": left, "width": "37%", "top": 100, "bottom": 45})
        x_axes.append({"type": "category", "data": levels, "gridIndex": i, "axisLabel": {"fontSize": 10}})
        # No shared `max`: each facet's y axis is scaled to its own counts.
        y_axes.append(
            {
                "type": "value",
                "gridIndex": i,
                "name": "cells" if i == 0 else "",
                "nameLocation": "middle",
                "nameGap": 42,
            }
        )
        series.append({"name": label, "type": "bar", "xAxisIndex": i, "yAxisIndex": i, "data": data, "barWidth": "45%"})
        titles.append({"text": label, "left": left, "top": 30, "textStyle": {"fontSize": 13, "fontWeight": 600}})
        if facet_value in notes:
            titles.append({"text": notes[facet_value], "left": left, "top": 52, "textStyle": {"fontSize": 9, "color": "#6B7A85", "fontWeight": "normal"}})
    titles.append(
        {
            "text": spec.get("value_label", ""),
            "left": "center",
            "top": 4,
            "textStyle": {"fontSize": 15, "fontWeight": 600},
        }
    )
    return {
        "grid": grids,
        "xAxis": x_axes,
        "yAxis": y_axes,
        "series": series,
        "title": titles,
        "tooltip": {"trigger": "item"},
    }


# ---------------------------------------------------------------------------
# Composites: one deck box, two figures
#
# 🔴 Slides 39 and 41 each have ONE picture box, and its placeholder text names
# two figures — "FIG-BO4-01 + FIG-BO4-02", "FIG-BO6-01 + FIG-BO6-03". Rendering
# them as two pages produced two images for one box, which is why neither slide
# could be filled. A composite builder takes the slot's own payload plus the
# companion the slot names, and draws both into the single box.
# ---------------------------------------------------------------------------


def _companion(payload: dict[str, Any], spec: dict[str, Any]) -> list[dict[str, Any]]:
    companion = payload.get("companion")
    if companion is None:
        _die(
            f"{payload['fig_id']}: this family draws two figures and the companion "
            f"{spec.get('with_fig_id', '?')} was not supplied — pass its JSON too"
        )
    return _rows_as_dicts(companion)


def build_zone_matrix_with_dominant(payload: dict[str, Any], spec: dict[str, Any]) -> dict:
    """Slide 39's single box: the 25 x 15 request-share matrix beside the ranking
    of each zone's largest ward share.

    The two figures are indexed by the same thing — the plow zone — so they get
    ONE ordering and sit against a shared row for each zone. That is the whole
    reason this composite is not a compromise: read across a row and the bar
    says how concentrated the zone is, the matrix row says across how many
    wards. Sorting both by dominant share is what makes "the median and the
    tail are both visible" (the slot's own words) true of both halves at once.

    The right-hand axis repeats no labels: the same 25 zones are already named
    once on the left, and naming them twice in a 6.8 in box costs the matrix
    the width it needs.
    """
    matrix_rows = _rows_as_dicts(payload)
    dominant_rows = _companion(payload, spec)
    dom_col, share_col = spec["companion_category"], spec["companion_value"]
    ranked = sorted(dominant_rows, key=lambda r: r[share_col])
    y_cats = [r[dom_col] for r in ranked]

    x_cats = sorted({r[spec["x"]] for r in matrix_rows})
    x_labels = [str(c).title() for c in x_cats]
    heat = []
    for r in matrix_rows:
        if r[spec["y"]] not in y_cats:
            continue
        heat.append(
            {
                "value": [x_cats.index(r[spec["x"]]), y_cats.index(r[spec["y"]]), round(r[spec["value"]], 4)],
                "itemStyle": (
                    {"borderColor": AMBER, "borderWidth": 1.4}
                    if bool(r.get(spec.get("highlight_flag", ""), False))
                    else {}
                ),
            }
        )
    # The accent marks zones with no majority ward at all — a fact about the
    # boundary systems, not a ranking of the zones, so it is a second
    # saturation of the same family rather than a warning colour.
    bars = [
        {"value": round(r[share_col], 4), "itemStyle": {"color": AMBER if r[share_col] < 0.5 else STEEL}}
        for r in ranked
    ]
    return {
        "title": [
            {"text": spec.get("left_title", ""), "left": "8%", "top": 4, "textStyle": {"fontSize": 13}},
            {"text": spec.get("right_title", ""), "left": "64%", "top": 4, "textStyle": {"fontSize": 13}},
        ],
        "grid": [
            {"left": 62, "width": "46%", "top": 58, "bottom": 92},
            {"left": "64%", "width": "30%", "top": 58, "bottom": 92},
        ],
        "xAxis": [
            {
                "type": "category",
                "data": x_labels,
                "gridIndex": 0,
                "axisLabel": {"rotate": 55, "fontSize": 9},
            },
            {"type": "value", "gridIndex": 1, "max": 1, "axisLabel": {"fontSize": 10}},
        ],
        "yAxis": [
            {
                "type": "category",
                "data": y_cats,
                "gridIndex": 0,
                # interval 0 forces all 25 names; without it ECharts drops every
                # other zone and the matrix rows stop being addressable.
                "axisLabel": {"fontSize": 8, "interval": 0},
            },
            {"type": "category", "data": y_cats, "gridIndex": 1, "axisLabel": {"show": False}},
        ],
        "visualMap": {
            "min": 0,
            "max": 1,
            "seriesIndex": 0,
            "calculable": True,
            "orient": "horizontal",
            "left": 62,
            "bottom": 8,
            "itemWidth": 12,
            "itemHeight": 90,
            "textStyle": {"fontSize": 9},
            "inRange": {"color": ["#F4F8FB", STEEL]},
        },
        "series": [
            {
                "type": "heatmap",
                "xAxisIndex": 0,
                "yAxisIndex": 0,
                "data": heat,
                "label": {"show": False},
            },
            {
                "type": "bar",
                "xAxisIndex": 1,
                "yAxisIndex": 1,
                "data": bars,
                "barWidth": "62%",
                "markLine": {
                    "silent": True,
                    "symbol": ["none", "none"],
                    "lineStyle": {"color": LABEL, "type": "dashed", "width": 1},
                    "label": {"formatter": "half the zone", "fontSize": 9, "color": LABEL},
                    "data": [{"xAxis": 0.5}],
                },
            },
        ],
        "tooltip": {"trigger": "item"},
    }


def build_panel_with_levels(payload: dict[str, Any], spec: dict[str, Any]) -> dict:
    """Slide 41's single box: the 59 x 22 panel's two blocks on the left, the
    level distribution for those same two scales on the right.

    🔴 Four sub-charts and still no shared scale anywhere. Each block keeps a
    `visualMap` scaled to its own observed max and each facet keeps its own y
    axis, because the whole card exists to say that a CRITICAL on one ruler is
    not a CRITICAL on the other.

    The blocks' 59 event ids are not labelled. At this size they were drawing
    as unreadable 7 px text, which is a claim to precision the picture cannot
    keep: the blocks are read as texture — how much of the panel is which
    colour — and the counts beside them are where the numbers live.
    """
    rows = _rows_as_dicts(payload)
    level_rows = _companion(payload, spec)

    x_cats = sorted({r[spec["x"]] for r in rows})
    y_rows = sorted({(r[spec["event_date"]], r[spec["y"]]) for r in rows})
    y_cats = [event_id for _, event_id in y_rows]

    grids: list[dict[str, Any]] = []
    x_axes: list[dict[str, Any]] = []
    y_axes: list[dict[str, Any]] = []
    series: list[dict[str, Any]] = []
    visual_maps: list[dict[str, Any]] = []
    titles: list[dict[str, Any]] = [
        {"text": spec.get("value_label", ""), "left": "center", "top": 4, "textStyle": {"fontSize": 15}}
    ]

    for i, (status_value, label) in enumerate(spec["statuses"]):
        subset = [
            r for r in rows if r[spec["split"]] == status_value and r.get(spec["value"]) is not None
        ]
        left = "7%" if i == 0 else "30%"
        grids.append({"left": left, "width": "20%", "top": 64, "bottom": 34})
        x_axes.append(
            {"type": "category", "data": x_cats, "gridIndex": i, "axisLabel": {"rotate": 90, "fontSize": 7}}
        )
        y_axes.append(
            {"type": "category", "data": y_cats, "gridIndex": i, "axisLabel": {"show": False}}
        )
        series.append(
            {
                "name": label,
                "type": "heatmap",
                "xAxisIndex": i,
                "yAxisIndex": i,
                "data": [
                    [x_cats.index(r[spec["x"]]), y_cats.index(r[spec["y"]]), round(r[spec["value"]], 3)]
                    for r in subset
                ],
                "label": {"show": False},
            }
        )
        visual_maps.append(
            {
                "min": 0,
                "max": max((r[spec["value"]] for r in subset), default=1.0),
                "seriesIndex": i,
                "orient": "horizontal",
                "left": left,
                "top": 40,
                "itemWidth": 8,
                "itemHeight": 52,
                "text": ["", ""],
                "textStyle": {"fontSize": 8},
                "inRange": {"color": ["#F4F8FB", STEEL if i == 0 else AMBER]},
            }
        )
        titles.append(
            {"text": label, "left": left, "top": 24, "textStyle": {"fontSize": 10, "fontWeight": 600}}
        )

    facet_col, level_col, value_col = spec["facet"], spec["level"], spec["value_count"]
    order = spec["level_order"]
    for j, (facet_value, label) in enumerate(spec["facets"]):
        idx = len(spec["statuses"]) + j
        subset = sorted(
            (r for r in level_rows if r[facet_col] == facet_value),
            key=lambda r: order.index(r[level_col]),
        )
        top = 82 if j == 0 else 300
        grids.append({"left": "60%", "width": "33%", "top": top, "height": 112})
        x_axes.append(
            {
                "type": "category",
                "data": [r[level_col] for r in subset],
                "gridIndex": idx,
                "axisLabel": {"fontSize": 9},
            }
        )
        # No shared max — the two rulers are the point of the whole card.
        y_axes.append({"type": "value", "gridIndex": idx, "axisLabel": {"fontSize": 9}})
        series.append(
            {
                "name": label,
                "type": "bar",
                "xAxisIndex": idx,
                "yAxisIndex": idx,
                "barWidth": "48%",
                "data": [
                    {
                        "value": r[value_count_col],
                        "itemStyle": {"color": AMBER if r[level_col] == order[-1] else STEEL},
                        "label": {
                            "show": True,
                            "position": "top",
                            "fontSize": 8,
                            "formatter": f"{r[value_count_col]}",
                        },
                    }
                    for r in subset
                    for value_count_col in (value_col,)
                ],
            }
        )
        titles.append(
            {"text": label, "left": "60%", "top": top - 30, "textStyle": {"fontSize": 10, "fontWeight": 600}}
        )
        note = spec.get("facet_notes", {}).get(facet_value)
        if note:
            # Above the grid, not below it: below is where the level names are,
            # and "CRITICAL at 75.0" printed over "LOW  MED  HIGH" reads as a
            # label on the wrong bar.
            titles.append(
                {
                    "text": note,
                    "left": "60%",
                    "top": top - 17,
                    "textStyle": {"fontSize": 8, "color": LABEL, "fontWeight": "normal"},
                }
            )
    return {
        "title": titles,
        "grid": grids,
        "xAxis": x_axes,
        "yAxis": y_axes,
        "visualMap": visual_maps,
        "series": series,
        "tooltip": {"trigger": "item"},
    }


def _pick_window(payload: dict[str, Any], spec: dict[str, Any]) -> list[dict[str, Any]]:
    """One window out of the several FIG-BO3-00 returns, in day order.

    The query deliberately returns candidates rather than a single answer, so
    picking one is a decision made here and recorded in the slot, not something
    the SQL quietly did. Fails loudly rather than drawing a partial window: a
    13-bar chart of a 14-day window is not visibly wrong."""
    rows = [
        r
        for r in _rows_as_dicts(payload)
        if r[spec["kind_col"]] == spec["kind"] and r[spec["rank_col"]] == spec["rank"]
    ]
    if len(rows) != spec.get("expect_days", 14):
        _die(
            f"{payload['fig_id']}: window {spec['kind']} #{spec['rank']} has {len(rows)} days, "
            f"expected {spec.get('expect_days', 14)} — pick another candidate or re-run the query"
        )
    return sorted(rows, key=lambda r: r[spec["day_index"]])


def build_daily_bars(payload: dict[str, Any], spec: dict[str, Any]) -> dict:
    """Fourteen daily snowfall bars, nothing else (final deck slide 20,
    FIG-BO3-00: "Fourteen consecutive days of daily snowfall, as bars").

    \U0001F534 No threshold line and no event boundaries, by instruction. The
    slide's whole question is "is this one storm, two, or three?", and any rule
    drawn on the bars answers it before the audience has been asked — which is
    the point slide 21 then spends its time undoing. That also means the one bar
    above 3 cm must stay unmarked: it is a taller bar, not a crossing.

    The x axis is day 1..14, not dates. The deck is explicit that the audience
    never sees the dates — a real window was used so we are not drawing
    fictional weather, not so the date can be read off the chart."""
    rows = _pick_window(payload, spec)
    values = [r[spec["value"]] for r in rows]
    return {
        "title": {
            "text": spec.get("value_label", "daily snowfall (cm)"),
            "left": "center",
            "textStyle": {"fontSize": 15},
        },
        "grid": {"left": 70, "right": 40, "top": 50, "bottom": 45},
        "xAxis": {
            "type": "category",
            "data": [str(r[spec["day_index"]]) for r in rows],
            "name": "day",
            "nameLocation": "middle",
            "nameGap": 28,
        },
        "yAxis": {"type": "value", "name": "cm"},
        "series": [
            {
                "type": "bar",
                "data": [{"value": v, "itemStyle": {"color": STEEL}} for v in values],
                "barWidth": "55%",
            }
        ],
        "tooltip": {"trigger": "item"},
    }


def build_bars_then_cumulative(payload: dict[str, Any], spec: dict[str, Any]) -> dict:
    """Daily bars on the left, the same days as a running total on the right
    (final deck slide 21, FIG-BO3-00b: "Small daily bars on the left, a rising
    running total on the right").

    Two grids because the slide asks for two panels, and because the two
    quantities have different units of meaning — a daily amount and a
    cumulative one — which a twin y axis on one plot invites reading as a
    single rising quantity.

    \U0001F534 The left panel carries the single-day threshold as a rule and the
    right panel the accumulation threshold, and this is the one figure where
    drawing them is required rather than forbidden: slide 21 exists to show
    that every bar stays under the first line while the total crosses the
    second. \U0001F534 The right panel's rule is the accumulation threshold's
    *value*, not a claim that this window triggers the production rule — that
    rule measures ten days and this cumulative runs fourteen. See
    docs/dev/design/20260906-final-deck-figure-slots.md \u00a73.4."""
    rows = _pick_window(payload, spec)
    day_labels = [str(r[spec["day_index"]]) for r in rows]
    daily = [r[spec["value"]] for r in rows]
    cumulative = [r[spec["cumulative"]] for r in rows]
    daily_rule = spec.get("daily_threshold")
    accum_rule = spec.get("accum_threshold")

    bar_series: dict[str, Any] = {
        "name": "daily",
        "type": "bar",
        "xAxisIndex": 0,
        "yAxisIndex": 0,
        "data": [{"value": v, "itemStyle": {"color": MUTED}} for v in daily],
        "barWidth": "55%",
    }
    if daily_rule is not None:
        bar_series["markLine"] = {
            "silent": True,
            "symbol": "none",
            "label": {"formatter": f"single-day threshold {daily_rule} cm", "position": "insideEndTop", "fontSize": 9},
            "lineStyle": {"color": "#12293F", "type": "dashed", "width": 1.5},
            "data": [{"yAxis": daily_rule}],
        }
    line_series: dict[str, Any] = {
        "name": "running total",
        "type": "line",
        "xAxisIndex": 1,
        "yAxisIndex": 1,
        "data": cumulative,
        "symbolSize": 6,
        "lineStyle": {"color": AMBER, "width": 2.5},
        "itemStyle": {"color": AMBER},
        "areaStyle": {"color": "rgba(222,122,22,0.12)"},
    }
    if accum_rule is not None:
        line_series["markLine"] = {
            "silent": True,
            "symbol": "none",
            "label": {"formatter": f"accumulation threshold {accum_rule} cm", "position": "insideEndTop", "fontSize": 9},
            "lineStyle": {"color": "#12293F", "type": "dashed", "width": 1.5},
            "data": [{"yAxis": accum_rule}],
        }

    # The left panel's y axis is scaled to the threshold, not to the bars: the
    # bars are the unremarkable half of the story and shrinking the axis to fit
    # them would make them look tall, which is the opposite of the point.
    left_max = max(daily_rule or 0, max(daily)) * 1.15
    return {
        "title": [
            {"text": "each day, on its own", "left": "7%", "top": 18, "textStyle": {"fontSize": 13}},
            {"text": "the same days, adding up", "left": "56%", "top": 18, "textStyle": {"fontSize": 13}},
        ],
        "grid": [
            {"left": "7%", "width": "37%", "top": 55, "bottom": 45},
            {"left": "56%", "width": "37%", "top": 55, "bottom": 45},
        ],
        "xAxis": [
            {"type": "category", "data": day_labels, "gridIndex": 0, "name": "day", "nameLocation": "middle", "nameGap": 26},
            {"type": "category", "data": day_labels, "gridIndex": 1, "name": "day", "nameLocation": "middle", "nameGap": 26},
        ],
        "yAxis": [
            {
                "type": "value",
                "gridIndex": 0,
                "name": "cm / day",
                "nameLocation": "middle",
                "nameGap": 38,
                "max": round(left_max, 1),
            },
            {
                "type": "value",
                "gridIndex": 1,
                "name": "cm, cumulative",
                "nameLocation": "middle",
                "nameGap": 38,
            },
        ],
        "series": [bar_series, line_series],
        "tooltip": {"trigger": "item"},
    }


FAMILY_BUILDERS: dict[str, Callable[[dict[str, Any], dict[str, Any]], dict]] = {
    "ranked_bar_whisker": build_ranked_bar_whisker,
    "slope": build_slope,
    "scatter_fit": build_scatter_fit,
    "box_single": build_box_single,
    "box_multi": build_box_multi,
    "timeline_scatter": build_timeline_scatter,
    "dot_plot": build_dot_plot,
    "heatmap": build_heatmap,
    "ranked_bar": build_ranked_bar,
    "heatmap_split": build_heatmap_split,
    "diverging_histogram": build_diverging_histogram,
    "range_strip": build_range_strip,
    "faceted_level_bars": build_faceted_level_bars,
    "daily_bars": build_daily_bars,
    "bars_then_cumulative": build_bars_then_cumulative,
    "zone_matrix_with_dominant": build_zone_matrix_with_dominant,
    "panel_with_levels": build_panel_with_levels,
}


# ---------------------------------------------------------------------------
# Deck slots: which slide of the final deck each rendered page fills.
#
# Calibrated against UOIPDayOfData20260919.pptx (43 slides, 2026-09-06). That
# deck numbers its slides one way only — every speaker note opens "Slide N ·"
# and N is the pptx index — so there is one number here, not the two the
# earlier v2 draft needed.
#
# 🔴 A slot is not a fig_id. Slide 11 and slide 12 are the same SQL asked two
# different questions (mean-with-whiskers vs the whiskers alone), and slide 39
# puts two fig_ids on one card. `spec` overrides the fig_id's FIGURE_SPEC entry
# and `caption` / `must_not_say` override the fig_id's text — a slot that
# reshapes the chart must say so itself, or the page describes a chart that is
# not on it.
# ---------------------------------------------------------------------------

SLIDE_SLOTS: tuple[dict[str, Any], ...] = (
    {
        "slide": 11,
        "fig_id": "FIG-BO2-01",
        "slug": "zone-rank-spread",
        "deck_box_in": (7.9, 4.06),
        "spec_size": (950, 490),
        "slot": "ECHARTS · FIG-BO2-01 — horizontal ranked bar, mean scheduled shift per zone with "
        "min/max whiskers. Only S and C at full saturation; the other 20 desaturated.",
    },
    {
        "slide": 12,
        "fig_id": "FIG-BO2-01",
        "slug": "zone-rank-range-strip",
        "deck_box_in": (6.43, 4.38),
        "spec_size": (770, 530),
        "slot": "ECHARTS · FIG-BO2-01b — range strip: the whiskers alone, min → max, same 22 zones "
        "in the same order, with a rule at shift 1. Only K's strip fails to reach it.",
        # The deck's own note: "Re-uses fig_bo2_01_zone_rank_spread.sql — no
        # second query." Same rows, the mean dropped and the rule added, so the
        # slide can ask "who touched shift 1" instead of "who is earliest".
        "spec": {
            "family": "range_strip",
            "rule_at": 1,
            "highlight": ("K",),
            "value_label": "shift positions actually reached, fastest to slowest",
        },
        "caption": (
            "Every zone's fastest and slowest scheduled shift across the 19 operations. 21 of the "
            "22 zones reached shift 1 at least once — including C, the latest on average. Zone K "
            "is the single exception: its range is 2 to 3 across all 19 operations, which is a "
            "different pattern from being last."
        ),
        "must_not_say": (
            "\U0001F534 Do not read K's strip as unfair or as worst service — K is not the latest "
            "zone on average, and consistently mid-table is a different pattern from last. Do not "
            "read this as actual completion time: it is the published schedule."
        ),
    },
    {
        "slide": 13,
        "fig_id": "FIG-BO2-02",
        "slug": "rank-drift",
        "deck_box_in": (7.9, 4.06),
        "spec_size": (950, 490),
        "slot": "ECHARTS · FIG-BO2-02 — slope chart, first 9 operations → last 10. Only V "
        "(1.89 → 3.20) and M (1.78 → 2.80) in the accent colour.",
    },
    {
        "slide": 17,
        "fig_id": "FIG-BO4-01",
        "slug": "zone-ward-matrix",
        "deck_box_in": (7.6, 4.06),
        "spec_size": (910, 490),
        "slot": "ECHARTS / MAP · FIG-BO4-01 — the deck prefers the ward × plow-zone map overlay; "
        "this is the 25 × 15 area-weight heatmap it names as the non-map alternative form.",
    },
    {
        "slide": 20,
        "fig_id": "FIG-BO3-00",
        "slug": "daily-snowfall-window",
        "deck_box_in": (8.1, 3.5),
        "spec_size": (970, 420),
        "slot": "ECHARTS \u00b7 FIG-BO3-00 \u2014 fourteen consecutive days of daily snowfall as bars. "
        "No event boundaries and no threshold line: the slide asks \u201cone storm, two, or three?\u201d "
        "and any rule drawn on the bars answers it before the audience is asked.",
    },
    {
        "slide": 21,
        "fig_id": "FIG-BO3-00",
        "slug": "small-days-add-up",
        "deck_box_in": (7.5, 3.72),
        "spec_size": (900, 450),
        "slot": "ECHARTS \u00b7 FIG-BO3-00b \u2014 a different candidate window from the same query: "
        "small bars on the left, the running total on the right. Every day stays under the "
        "single-day threshold while the total crosses the accumulation threshold.",
        "spec": {
            "family": "bars_then_cumulative",
            "kind": "accumulation_only",
            "rank": 1,
            "daily_threshold": 3.0,
            "accum_threshold": 10.0,
        },
        "caption": (
            "Fourteen days from the archive in which no single day reaches the 3 cm single-day "
            "threshold \u2014 the largest is 2.66 cm \u2014 while the running total reaches 12.81 cm, "
            "crossing 10 cm on day 12. This is why a single-day rule alone misses cases the crews "
            "still have to deal with: 8 of the 99 events exist only because of the accumulation rule."
        ),
        "must_not_say": (
            "\U0001F534 Do not say this window is one of those 8 events, or that the production rule "
            "would flag it: that rule measures a trailing ten days and this window's strict "
            "ten-day total is 9.45 cm \u2014 0.55 short. The fourteen-day cumulative is what is "
            "drawn. Do not say lowering the single-day threshold would recover these cases "
            "either; that was tested and disproved. See design 20260906 \u00a73.4."
        ),
    },
    {
        "slide": 37,
        "fig_id": "FIG-BO2-04",
        "slug": "appendix-3-rank-vs-addresses",
        "deck_box_in": (6.8, 4.1),
        "spec_size": (820, 490),
        "slot": "APPENDIX 3 · ECHARTS · FIG-BO2-04 — scatter of address count against mean "
        "scheduled shift with a fitted line, two series (all 19 operations / since 2021).",
    },
    {
        "slide": 39,
        "fig_id": "FIG-BO4-01",
        "with_fig_id": "FIG-BO4-02",
        "slug": "appendix-5-zone-ward-matrix-and-dominant-share",
        "deck_box_in": (6.8, 4.1),
        "spec_size": (820, 490),
        "slot": "APPENDIX 5 \u00b7 FIG-BO4-01 + FIG-BO4-02 \u2014 one box, and the deck's own "
        "placeholder names both figures: the 25 \u00d7 15 area-weight matrix and the ranking of "
        "each zone's largest ward share, on one shared ordering of the 25 zones.",
        "spec": {
            "family": "zone_matrix_with_dominant",
            "companion_category": "plow_zone",
            "companion_value": "dominant_share",
            "left_title": "share of each zone's area, by ward",
            "right_title": "largest single ward share",
        },
        "caption": (
            "25 plow zones \u00d7 15 wards, on one shared ordering. Each matrix cell is a share of "
            "the zone's area; the bar beside it is that zone's largest single ward share. Only T "
            "and N fall entirely within one ward, 10 of 25 have no majority ward at all (the "
            "second saturation), and V spans 10 wards with a largest share of 26.0%. Median 54.0% "
            "over 25 zones, 53.5% over the 22 that carry a schedule."
        ),
        "must_not_say": (
            "\U0001F534 Do not say 'the wards are drawn badly' \u2014 the two boundary systems are on "
            "different bases (one electoral, one operational), and neither is wrong. Do not read "
            "the dominant share as 'that ward carries this much of the snow-clearing work': it is "
            "an area share, not a workload share."
        ),
    },
    {
        "slide": 41,
        "fig_id": "FIG-BO6-01",
        "with_fig_id": "FIG-BO6-03",
        "slug": "appendix-7-panel-and-level-distribution",
        "deck_box_in": (6.6, 4.1),
        "spec_size": (790, 490),
        "slot": "APPENDIX 7 \u00b7 FIG-BO6-01 + FIG-BO6-03 \u2014 one box, and the deck's own "
        "placeholder names both: the 59 \u00d7 22 panel as two separately-scaled blocks, and "
        "beside them the level distribution on those same two rulers.",
        "spec": {
            "family": "panel_with_levels",
            "facet": "score_weight_profile",
            "level": "load_level",
            "value_count": "cells",
            "level_order": ["LOW", "MED", "HIGH", "CRITICAL"],
            "facets": [
                ("full_3factor", "full_3factor \u00b7 374 scored cells"),
                ("demand_weather_only", "demand_weather_only \u00b7 924 partial cells"),
            ],
            "facet_notes": {
                "full_3factor": "CRITICAL at 75.0",
                "demand_weather_only": "CRITICAL at 52.5 \u2014 observed max 50.27",
            },
        },
        "caption": (
            "The 1,298-cell panel (59 events \u00d7 22 zones) as two blocks, and the level "
            "distribution on each block's own ruler. 924 cells have no scheduling data and are "
            "scored on the two-factor scale; the other 374 use the full three-factor scale. Four "
            "sub-charts, four scales, none of them shared."
        ),
        "must_not_say": (
            "\U0001F534 Do not compare a level across the two profiles: CRITICAL is 75.0 on the "
            "three-factor ruler and 52.5 on the two-factor one, so the two CRITICALs are not the "
            "same quantity. Do not read the two blocks' colours against each other \u2014 each is "
            "scaled to its own observed max. Do not say the partial cells can never reach "
            "CRITICAL: zero cells today is an observation, and the highest is 50.27 against "
            "52.5, a gap of 2.23."
        ),
    },
)

# Slots the final deck reserves that this pipeline does not fill, and why.
# Printed by `--slides` so a gap is visible rather than silently absent.
UNFILLED_SLOTS: tuple[tuple[int, str], ...] = (
    (5, "context map \u2014 a plain City of Winnipeg backdrop behind the three source cards. "
        "Not one of our figures at all: the deck asks for a quiet base map with no labels and no "
        "zone colouring, which no fig_*.sql produces and no Gold table holds."),
    (16, "two plain outline maps side by side, 15 wards and 25 plow zones, same extent / scale / "
         "projection. \U0001F534 The plow-zone half is covered by FIG-BO4-00; the ward half is "
         "not \u2014 no ward geometry exists anywhere in the warehouse. dim_plow_zone is the only "
         "table carrying a geometry_wkt column, and dim_admin_label holds ward names with no "
         "shape. Drawing this needs a ward-boundary source that was never ingested."),
    (18, "FIG-BO4-01b — zone V zoomed with ward boundaries running through it. A detail crop of "
         "the map, so it needs zone geometry, not a figure export."),
    (24, "FIG-BO6-01 (single event) — BLOCKED in the deck's own notes: the case must be picked "
         "from fact_winter_event_zone_load where score_status = 'scored' and frozen with its "
         "event id, zone, etl_run_id and certification status before the deck is built."),
    (29, "FIG-BO1-03 (map form) — plow-zone choropleth of estimated winter resident reports. "
         "Needs zone geometry; also the one slide whose legend wording is load-bearing "
         "(\u201cestimated winter-related resident reports\u201d, never \u201cload\u201d or \u201cpriority\u201d)."),
)

# Slots that need no HTML page from here, recorded so nobody re-derives it.
ALREADY_IN_DECK: tuple[tuple[int, str], ...] = (
    (25, "the 59 event marks (17 complete / 42 partial) are drawn natively as 59 shapes in the "
         "pptx — an earlier draft rendered them here; the deck now owns them."),
    (36, "APPENDIX 2 shift distribution — a native pptx chart shape is already on the slide; "
         "its five bars match FIG-BO2-03's frozen export value for value (checked 2026-09-08)."),
    (40, "APPENDIX 6 — the event rule and the 11-of-17 lag finding are text cards, not a chart. "
         "FIG-BO3-03 has no slot in the final deck."),
    (42, "APPENDIX 8 model MAE — a native pptx chart shape is already on the slide, and it may "
         "never appear without its three reservations; its three bars match FIG-BO1-03's frozen "
         "export (checked 2026-09-08)."),
    (43, "APPENDIX 9 request categories — a native pptx chart shape is already on the slide. "
         "FIG-BO1-04 exists so those six bars are checkable against a frozen export; the "
         "drawing stays the deck's."),
)


# ---------------------------------------------------------------------------
# English translations of the (Chinese-authored) SQL header caption /
# must_not_say text. Hand-checked per fig_id — see module docstring.
# ---------------------------------------------------------------------------

ENGLISH_CAPTIONS: dict[str, dict[str, str]] = {
    "FIG-BO2-01": {
        "caption": (
            "Average scheduling rank across 22 plow zones, over 19 city-wide plow operations "
            "since 2015-12. The two ends differ by 2.21 shifts ≈ 26 hours (shift length 12 h). "
            "The whiskers show each zone's actual fastest / slowest shift: every zone except K has "
            "been first at least once, including C, which is slowest on average."
        ),
        "must_not_say": (
            "Do not say this has “stayed the same for ten years” — this figure is a "
            "ten-year average; drift is shown in FIG-BO2-02. Do not frame rank differences as unfair "
            "— zone rank is an operational batch order, not a service-level commitment."
        ),
    },
    "FIG-BO2-02": {
        "caption": (
            "The same 19 operations split by time into the first 9 / last 10. V moved back 1.31 "
            "shifts, M moved back 1.02 shifts — both more than a full shift. Rank is not fixed; "
            "do not say it has “stayed the same for ten years.” The split is by event "
            "sequence number, not calendar date; the probe scripts.analysis.zone_schedule_rank uses "
            "--since 2021-01-01 instead — the two numbers must not be conflated."
        ),
        "must_not_say": (
            "Do not say rank has “stayed the same for ten years”; do not read the drift as "
            "evidence the scheduling rule was changed — this figure measures the outcome only, "
            "it does not identify a cause."
        ),
    },
    "FIG-BO2-04": {
        "caption": (
            "Counter-check: are later-ranked zones simply the ones with more addresses? The "
            "direction is the opposite — address count correlates positively with mean rank "
            "(r = +0.49 over the full period / +0.40 since 2021), so zones with more addresses rank "
            "later, not earlier."
        ),
        "must_not_say": (
            "Do not read the positive correlation as causal (more addresses therefore ranked later) "
            "— this figure only refutes one alternative explanation."
        ),
    },
    "FIG-BO1-02": {
        "caption": (
            "A quarter of the 1,298 grid cells are 0, the median is 2.9, and the max is 381. The "
            "mean has no meaning on this distribution."
        ),
        "must_not_say": (
            "\U0001F534 Do not describe this panel using the mean, and do not read a 0 as missing "
            "data — it means ‘that zone genuinely had no winter service requests in that "
            "event.’ Percentiles and the max must appear together; showing only the mean would "
            "present a long-tail distribution as if it had a typical value."
        ),
    },
    "FIG-BO3-01": {
        "caption": (
            "99 snowfall events, 2008-11 through 2026-04, defined by single-day ≥ 3 cm or a "
            "10-day rolling total ≥ 10 cm. The 11 hollow points had zero winter service requests "
            "in any zone — that reflects thin early-era 311 coverage, not an absence of snow. "
            "The 8 light-colored points only qualify via the rolling-accumulation criterion; no "
            "single day crossed the threshold. Three of those have under 1.5 cm total snowfall for "
            "the whole event — they are the tail end of the previous snowfall being cut off, "
            "not a new snowfall."
        ),
        "must_not_say": (
            "Do not read the hollow points as ‘those snowfalls caused no problems’ — "
            "it is thin 311 coverage, not an absence of demand. Do not state only ‘≥ 3 "
            "cm’: omitting the accumulation criterion leaves the 8 light-colored points "
            "unexplained."
        ),
    },
    "FIG-BO3-03": {
        "caption": (
            "Of 17 city-wide plow operations that can be matched to a snowfall event, 11 started "
            "before the snowfall event even ended (negative values). Plowing partway through a "
            "multi-day snowfall is normal — this is not a measure of ‘how long the response "
            "took.’ The anchor point is the event's end date; anchoring to the start date "
            "instead turns this into a different number for the same events. Of the 19 operations, "
            "2 do not match any snowfall event (2021-01-07 / 2026-02-26), marked separately — "
            "the rolling-accumulation criterion reduced the original four unmatched cases to two, "
            "which helped but is not sufficient."
        ),
        "must_not_say": (
            "\U0001F534 Do not call this axis ‘response lag’ or ‘response time.’ "
            "It is timing relative to the event's end date — most negative values mean plowing "
            "was already underway while snow was still falling. Anchoring to start_date instead "
            "produces an entirely different set of numbers, so the chart must state which anchor is "
            "in use. The two unmatched points must not be read as ‘missed plowing.’"
        ),
    },
    "FIG-BO4-01": {
        "caption": (
            "25 plow zones × 15 wards; each cell is the share of that zone's winter service "
            "requests that fall in that ward (three snow seasons, 2023-11 to 2026-05). Only zones "
            "T and N have all their requests inside one ward; V's are spread across 10. The two "
            "boundary systems are drawn on different bases — one by voting population, one by plow "
            "routes — neither is drawn wrong, but they cannot substitute for each other."
        ),
        "must_not_say": (
            "\U0001F534 Do not call the cell an area share or a geometric overlap. "
            "dim_region_crosswalk.weight is that (zone, ward) pair's winter request count over the "
            "zone's total, and it never touches ward geometry — the repository holds none. Nor say "
            "‘the wards are drawn badly’: the two boundary systems are on different bases (one "
            "electoral, one operational), and neither is wrong. The caption is about the "
            "consequence: scoring by ward would split one plow zone's workload across several wards."
        ),
    },
    "FIG-BO4-02": {
        "caption": (
            "For each plow zone, how large a share of its winter service requests falls in the "
            "single ward that takes the most. Median 54.0%, and 10 of 25 zones are under half "
            "(including 3 zones with no plow schedule). This is why scoring was unified onto plow "
            "zones (ADR 0009): scoring by ward would split one plow route's workload across "
            "several wards."
        ),
        "must_not_say": (
            "\U0001F534 Do not read the dominant share as an area share or a geometric overlap — "
            "it is that ward's winter request count over the zone's total, computed without ward "
            "geometry. Nor read it the other way, as ‘that ward carries this much of the "
            "snow-clearing work’: plowing is scheduled by zone, and a request is where a resident "
            "reported, not where the work happened."
        ),
    },
    "FIG-BO3-00": {
        "caption": (
            "Fourteen consecutive days of daily snowfall from the archive. Three bursts separated "
            "by two days with no snow at all. Nothing in the weather record says whether this is "
            "one snowfall event, two, or three \u2014 that boundary is a rule somebody has to write, "
            "and then check against what the crews actually did."
        ),
        "must_not_say": (
            "\U0001F534 Do not draw an event boundary or a threshold line on this chart. The slide's "
            "question is where one event ends, and a rule drawn on the bars answers it before the "
            "audience has been asked. The one bar above 3 cm is a taller bar, not a crossing. Do "
            "not call these fourteen days \u201can event\u201d: the event is the later rule, not "
            "something the data arrived with."
        ),
    },
    "FIG-BO6-01": {
        "caption": (
            "Scoring panel: 59 events × 22 zones = 1,298 cells. 71.2% are partial_no_rank — "
            "those 924 cells have no scheduling data and use a two-factor 0.70-point scale; only the "
            "other 374 cells use the full three-factor scale. The two must be drawn separately or on "
            "two color scales."
        ),
        "must_not_say": (
            "\U0001F534 Do not draw both profiles on the same color scale. The 374-cell and "
            "924-cell rulers are different; a shared scale would paint ‘no scheduling data’ "
            "as ‘not busy.’ Do not say the panel is out of 100 either — the weather "
            "factor peaks at 0.8978 within the scheduling era, so full_3factor's actual achievable "
            "ceiling is about 96.9."
        ),
    },
    "FIG-BO6-03": {
        "caption": (
            "Load levels under each weight profile, on two separate coordinate systems. "
            "The 924 partial cells are 88.1% LOW; the 374 fully scored cells are only 12.3% LOW "
            "and 3.5% CRITICAL. The level names are shared, the rulers are not \u2014 thresholds "
            "are scaled to each profile's own ceiling."
        ),
        "must_not_say": (
            "\U0001F534 Do not compare a level across the two profiles: CRITICAL is 75.0 on the "
            "three-factor scale and 52.5 on the two-factor one, so they are not the same quantity. "
            "Do not say the partial cells can never reach CRITICAL \u2014 zero cells today is an "
            "observation, and the highest is 50.27 against 52.5, a gap of 2.23."
        ),
    },
    "FIG-BO6-02": {
        "caption": (
            "Observed weighted ranges for the three factors: demand 0–0.40 · rank "
            "0.06–0.30, with 89.6% falling in 0.06–0.18 · weather 0.020–0.269 "
            "(= 0.30 × [0.0682, 0.8978]). The nominal weights 0.40/0.30/0.30 are not the same "
            "thing as the actual order of influence."
        ),
        "must_not_say": (
            "\U0001F534 Do not draw the nominal weights as if they were contributions. Do not draw "
            "the rank factor as five evenly-sized bands either — the 4th and 5th shift bands "
            "together are only 39 of 374 cells, the first three shifts hold 89.6%. The weather "
            "factor is a per-event constant: it sets the score level but barely affects the "
            "within-event ranking."
        ),
    },
    "FIG-BO8-01": {
        "caption": (
            "Distribution of rank_delta displacement, faceted by model_version. ① Within each "
            "event, both rank columns are a permutation of 1..22, so the sum of displacement is "
            "always 0 — ‘188 cells moved up’ necessarily pairs with ‘167 cells "
            "moved down.’ ② The deliberately-degraded nomonth version shows the same 188 "
            "cells moved up."
        ),
        "must_not_say": (
            "\U0001F534 Title says ‘displacement,’ not ‘improvement.’ "
            "rank_delta > 0 is not ‘the model beats the baseline’ — it is the position "
            "difference between two 1..22 permutations within the same event, and it sums to zero; "
            "a model with the month feature deliberately removed produces the exact same number of "
            "upward moves."
        ),
    },
}


# ---------------------------------------------------------------------------
# HTML shell
# ---------------------------------------------------------------------------

_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; margin: 0;
         padding: 28px; background: #ffffff; color: #12293F;
         -webkit-font-smoothing: antialiased; }}
  h1 {{ font-size: 12px; margin: 0 0 10px; font-weight: 600; letter-spacing: 0.09em;
       text-transform: uppercase; color: #6E8091; }}
  /* The chart's own background is painted here rather than in the theme, so the
     PNG export (which takes backgroundColor from the download handler) and the
     on-screen page cannot drift apart. */
  #chart {{ max-width: 100%; background: #ffffff; }}
  .caption {{ max-width: 960px; margin-top: 14px; font-size: 14px; line-height: 1.55; color: #2E3B45; }}
  .must-not-say {{ max-width: 960px; margin-top: 8px; font-size: 12.5px; line-height: 1.5; color: #A34A0C; }}
  .provenance {{ max-width: 960px; margin-top: 14px; font-size: 11px; color: #8A97A0;
                 border-top: 1px solid #E3EDF4; padding-top: 8px; }}
  .size-controls {{ margin-top: 10px; font-size: 12px; color: #2E3B45; }}
  .size-controls label {{ margin-right: 14px; }}
  .size-controls input {{ width: 70px; }}
  .size-controls button {{ margin-left: 6px; }}
  .size-controls .hint {{ margin-left: 12px; color: #6B7A85; }}
  .slide-banner {{ background: #12293F; color: #FFFFFF; margin: -28px -28px 18px; padding: 10px 28px;
                   font-size: 13px; }}
  .slide-banner strong {{ font-size: 15px; }}
  .slide-banner span {{ color: #B9C6CE; }}
</style>
</head>
<body>
{banner}<h1>{fig_id}</h1>
<div id="chart" style="width: {default_width}px; height: {default_height}px;"></div>
<div class="size-controls">
  <label>Width <input type="number" id="widthInput" value="{default_width}" min="200" max="4000" step="10"> px</label>
  <label>Height <input type="number" id="heightInput" value="{default_height}" min="200" max="4000" step="10"> px</label>
  <button id="applySize" type="button">Apply</button>
  <button id="savePng" type="button">Download PNG</button>
  <span class="hint">{slot_hint}</span>
</div>
<p class="caption">{caption}</p>
<p class="must-not-say">&#128721; must not read as: {must_not_say}</p>
<p class="provenance">{provenance}</p>
<script>
{echarts_js}
</script>
<script>
echarts.registerTheme('{theme_name}', {theme_json});
const option = {option_json};
const chart = echarts.init(document.getElementById('chart'), '{theme_name}');
chart.setOption(option);
window.addEventListener('resize', function () {{ chart.resize(); }});
document.getElementById('applySize').addEventListener('click', function () {{
  var chartEl = document.getElementById('chart');
  chartEl.style.width = document.getElementById('widthInput').value + 'px';
  chartEl.style.height = document.getElementById('heightInput').value + 'px';
  chart.resize();
}});
// pixelRatio 3 against an on-screen size already set to the deck box's own
// inches: the PNG lands at ~360 dpi for that box, so PowerPoint scales it
// down rather than up. A 1x export looks fine on screen and soft on a
// projector, which is only discovered in the room.
document.getElementById('savePng').addEventListener('click', function () {{
  var url = chart.getDataURL({{ type: 'png', pixelRatio: 3, backgroundColor: '#FFFFFF' }});
  var a = document.createElement('a');
  a.href = url;
  a.download = '{png_name}';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}});
</script>
</body></html>
"""


def render_html(
    payload: dict[str, Any],
    option: dict[str, Any],
    echarts_js: str,
    default_width: int = 960,
    default_height: int = 600,
    slot: dict[str, Any] | None = None,
) -> str:
    fig_id = payload["fig_id"]
    header = payload["header"]
    certification = payload.get("certification", {})
    english = ENGLISH_CAPTIONS.get(fig_id, {})
    caption = english.get("caption", header.get("caption", ""))
    must_not_say = english.get("must_not_say", header.get("must_not_say", ""))
    # A slot that reshapes the figure gets its own caption: slide 30 collapses
    # FIG-BO6-01's cells to events, so that fig_id's caption ("do not draw both
    # profiles on the same colour scale") describes a chart that is no longer
    # on the page, and the reader has no way to tell.
    if slot is not None:
        caption = slot.get("caption", caption)
        must_not_say = slot.get("must_not_say", must_not_say)
    provenance = (
        f"source: {payload.get('source_sql', '?')} &middot; "
        f"frozen_at: {payload.get('frozen_at', '?')} &middot; "
        f"certification: {certification.get('status', 'unknown')} "
        f"({certification.get('run_id', '?')})"
    )
    banner = ""
    title = fig_id
    png_name = f"{fig_id}.png"
    slot_hint = ""
    if slot is not None:
        title = f"Slide {slot['slide']} · {fig_id}"
        banner = (
            '<div class="slide-banner">'
            f"<strong>slide {slot['slide']}</strong> &middot; {html.escape(fig_id)}<br>"
            f"<span>{html.escape(slot['slot'])}</span>"
            "</div>\n"
        )
        png_name = _slide_filename(slot).replace(".html", ".png")
        box = slot.get("deck_box_in")
        if box:
            slot_hint = html.escape(
                f"deck slot {box[0]} \u00d7 {box[1]} in \u2014 PNG exports at 3\u00d7 (~360 dpi in that box)"
            )
    return _TEMPLATE.format(
        title=html.escape(title),
        banner=banner,
        fig_id=html.escape(fig_id),
        caption=html.escape(caption),
        must_not_say=html.escape(must_not_say),
        provenance=provenance,
        echarts_js=echarts_js,
        option_json=json.dumps(option, ensure_ascii=False),
        theme_name=THEME_NAME,
        theme_json=json.dumps(DECK_THEME, ensure_ascii=False),
        default_width=default_width,
        default_height=default_height,
        png_name=html.escape(png_name),
        slot_hint=slot_hint,
    )


def build_option(payload: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """The builder for this figure's family, plus the two presentation passes.

    Kept as one named function so a test can exercise exactly what the page
    gets — the passes are where the clipped axis names and the flat bar fills
    are fixed, and a test calling the raw builder would pass while the page
    still shipped both defects.
    """
    builder = FAMILY_BUILDERS[spec["family"]]
    return _centre_bottom_axis_names(_wash_bar_fills(builder(payload, spec)))


def render_figure(
    payload: dict[str, Any], echarts_js: str, slot: dict[str, Any] | None = None
) -> str:
    fig_id = payload["fig_id"]
    spec = FIGURE_SPEC.get(fig_id) or OFFLINE_SUPERSET_SPEC.get(fig_id)
    if spec is not None and slot is not None and slot.get("spec"):
        spec = {**spec, **slot["spec"]}
    if spec is None:
        if fig_id in NOT_YET_IMPLEMENTED:
            _die(f"{fig_id}: chart family decided (design §3.3) but not yet implemented — see NOT_YET_IMPLEMENTED")
        if fig_id in OUT_OF_SCOPE:
            _die(f"{fig_id}: rendered by hand-built slide art or a native pptxgenjs chart, not this pipeline")
        carrier = payload.get("header", {}).get("carrier")
        if carrier == "superset":
            _die(f"{fig_id}: carrier is superset, not echarts — build it as a Superset chart instead")
        _die(f"{fig_id}: not in FIGURE_SPEC, NOT_YET_IMPLEMENTED or OUT_OF_SCOPE — is this a new fig_id?")
    option = build_option(payload, spec)
    # A slot's on-screen size comes from the deck box it fills, so the exported
    # PNG already has that box's aspect ratio. Getting this wrong is not a
    # cosmetic problem: PowerPoint will happily stretch a mismatched image, and
    # a stretched chart misreports its own values.
    width = spec.get("default_width", 960)
    height = spec.get("default_height", 600)
    if slot is not None and slot.get("spec_size"):
        width, height = slot["spec_size"]
    return render_html(
        payload,
        option,
        echarts_js,
        default_width=width,
        default_height=height,
        slot=slot,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("json_paths", nargs="+", type=Path, help="var/presentation/<fig_id>.json files")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--slides",
        action="store_true",
        help="render one page per SLIDE_SLOTS entry, named slide-NN_<slug>.html, "
        "instead of one page per fig_id",
    )
    return parser


def _slide_filename(slot: dict[str, Any]) -> str:
    return f"slide-{slot['slide']:02d}_{slot['slug']}.html"


def render_slides(json_paths: list[Path], out_dir: Path) -> int:
    """One HTML page per deck slot. A fig_id may appear in more than one slot
    (slide 12 / slide 14 / appendix A7), so the filename is keyed on the slot,
    never on the fig_id."""
    echarts_js = VENDOR_JS.read_text(encoding="utf-8")
    out_dir.mkdir(parents=True, exist_ok=True)
    by_fig = {}
    for path in json_paths:
        payload = load_payload(path)
        by_fig[payload["fig_id"]] = payload
    written = 0
    for slot in SLIDE_SLOTS:
        payload = by_fig.get(slot["fig_id"])
        if payload is None:
            print(f"skip slide {slot['slide']}: no payload for {slot['fig_id']} among the given files")
            continue
        # A slot whose deck box names two figures carries the second one here.
        # It is attached to the payload rather than passed alongside so that a
        # builder still receives exactly one object and `build_option` keeps a
        # two-argument signature the tests can call.
        companion_id = slot.get("with_fig_id")
        if companion_id:
            companion = by_fig.get(companion_id)
            if companion is None:
                print(
                    f"skip slide {slot['slide']}: {slot['fig_id']} needs its companion "
                    f"{companion_id} in the same box and it was not among the given files"
                )
                continue
            payload = {**payload, "companion": companion}
        out_path = out_dir / _slide_filename(slot)
        out_path.write_text(render_figure(payload, echarts_js, slot=slot), encoding="utf-8")
        print(f"wrote {out_path}  ({slot['fig_id']})")
        written += 1
    for slide, reason in UNFILLED_SLOTS:
        print(f"unfilled slide {slide}: {reason}")
    for slide, reason in ALREADY_IN_DECK:
        print(f"no page needed for slide {slide}: {reason}")
    print(f"{written} of {len(SLIDE_SLOTS)} slots rendered")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.slides:
        return render_slides(args.json_paths, args.out)
    echarts_js = VENDOR_JS.read_text(encoding="utf-8")
    args.out.mkdir(parents=True, exist_ok=True)
    written = 0
    for json_path in args.json_paths:
        payload = load_payload(json_path)
        fig_id = payload["fig_id"]
        if fig_id not in FIGURE_SPEC:
            if fig_id in NOT_YET_IMPLEMENTED:
                reason = "chart family decided, not yet implemented"
            elif fig_id in OUT_OF_SCOPE:
                reason = "hand-built slide art / native pptx chart, not this pipeline"
            elif payload.get("header", {}).get("carrier") == "superset":
                reason = "carrier is superset, build it in Superset instead"
            else:
                reason = "unknown fig_id"
            print(f"skip {fig_id}: {reason}")
            continue
        out_path = args.out / f"{fig_id}.html"
        out_path.write_text(render_figure(payload, echarts_js), encoding="utf-8")
        print(f"wrote {out_path}")
        written += 1
    print(f"{written} of {len(args.json_paths)} rendered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
