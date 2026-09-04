"""Render a frozen `var/presentation/<fig_id>.json` payload into a
self-contained ECharts HTML page.

    uv run python -m scripts.presentation.render_html var/presentation/FIG-BO2-01.json
    uv run python -m scripts.presentation.render_html var/presentation/*.json --out var/presentation/html

See design/20260903-presentation-figure-rendering.md §3.1/§3.2 for why this
step exists at all: `scripts.eda.run --json` freezes results to JSON and stops
there, and the conference cannot depend on a live Trino connection (C3 there,
C7 in the parent design). "Self-contained" is not a suggestion — the ECharts
runtime is vendored at `scripts/presentation/vendor/echarts.min.js` and
inlined verbatim, and the data is inlined as a JS literal. No
`<script src="http...">`, no `fetch()`. A page that needs the venue's wifi is
not a page for the venue.

🔴 `FIGURE_FAMILY` mirrors the design doc's §3.3 decision table — it is that
table's executable form, not a second, independent decision. Only three
families are implemented so far (the three main-storyline figures the deck
had already fully specified); the rest are deliberately left undone rather
than guessed at, per the design doc's O4.
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
}

NOT_YET_IMPLEMENTED = (
    "FIG-BO1-02",  # box: single-row 5-number summary
    "FIG-BO3-01",  # timeline_scatter: 99 events
    "FIG-BO3-03",  # dot_plot: dual-anchor lag
    "FIG-BO4-01",  # heatmap: 25x15 area-weight matrix (map is the primary form)
    "FIG-BO4-02",  # ranked_bar: design §3.2 raised switching this to superset,
                    # user decided 2026-09-03 to keep it echarts (launch §5) —
                    # just not built yet, same as the rest of this tuple
    "FIG-BO6-01",  # heatmap: 59x22 load panel, split by score_status
    "FIG-BO6-02",  # box: 3 factors side by side
    "FIG-BO8-01",  # diverging_histogram: rank_delta by model_version
)
# Two fig_ids carry `carrier: echarts` but never reach this generic pipeline,
# on purpose — see design §3.3:
#   FIG-BO2-01b / FIG-BO4-01b  reuse another fig's SQL (design C4-d), so they
#     are not separate fig_ids at all.
#   FIG-BO1-03  is rendered two other ways instead: a hand-built single-event
#     choropleth on the main slide, and a native pptxgenjs bar chart
#     (baseline/v1/nomonth MAE) in the appendix. Neither is a generic
#     chart family this module should own.
OUT_OF_SCOPE = ("FIG-BO1-03",)


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


def build_ranked_bar_whisker(payload: dict[str, Any], spec: dict[str, Any]) -> dict:
    """22-ish categories, one mean + a [min, max] range each. ECharts'
    boxplot series expresses this natively (five numbers: low, q1, median,
    q3, high) without a hand-written renderItem — we degenerate q1=median=q3
    to the mean, which draws exactly "a tick at the mean with whiskers to the
    range", the shape the deck actually asked for (design §3.3, FIG-BO2-01:
    "横向排序条形图 + min/max 须")."""
    rows = sorted(_rows_as_dicts(payload), key=lambda r: r[spec["value"]])
    categories = [r[spec["category"]] for r in rows]
    highlight = set(spec.get("highlight", ()))
    data = []
    for r in rows:
        mean_v = r[spec["value"]]
        low, high = r[spec["low"]], r[spec["high"]]
        color = AMBER if r[spec["category"]] in highlight else MUTED
        data.append(
            {
                "value": [low, mean_v, mean_v, mean_v, high],
                "itemStyle": {"color": color, "borderColor": color},
            }
        )
    return {
        "title": {"text": spec.get("value_label", spec["value"]), "left": "center", "textStyle": {"fontSize": 13}},
        "grid": {"left": 70, "right": 30, "top": 40, "bottom": 30},
        "xAxis": {"type": "value", "name": spec.get("value_label", "")},
        "yAxis": {"type": "category", "data": categories, "name": "plow zone"},
        "series": [{"type": "boxplot", "data": data}],
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


FAMILY_BUILDERS: dict[str, Callable[[dict[str, Any], dict[str, Any]], dict]] = {
    "ranked_bar_whisker": build_ranked_bar_whisker,
    "slope": build_slope,
    "scatter_fit": build_scatter_fit,
}


# ---------------------------------------------------------------------------
# HTML shell
# ---------------------------------------------------------------------------

_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8">
<title>{fig_id}</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", sans-serif; margin: 0; padding: 28px;
         background: #ffffff; color: #12293F; }}
  h1 {{ font-size: 16px; margin: 0 0 4px; }}
  #chart {{ width: 960px; height: 600px; max-width: 100%; }}
  .caption {{ max-width: 960px; margin-top: 14px; font-size: 14px; line-height: 1.55; color: #2E3B45; }}
  .must-not-say {{ max-width: 960px; margin-top: 8px; font-size: 12.5px; line-height: 1.5; color: #A34A0C; }}
  .provenance {{ max-width: 960px; margin-top: 14px; font-size: 11px; color: #8A97A0;
                 border-top: 1px solid #E3EDF4; padding-top: 8px; }}
</style>
</head>
<body>
<h1>{fig_id}</h1>
<div id="chart"></div>
<p class="caption">{caption}</p>
<p class="must-not-say">&#128721; must not read as: {must_not_say}</p>
<p class="provenance">{provenance}</p>
<script>
{echarts_js}
</script>
<script>
const option = {option_json};
const chart = echarts.init(document.getElementById('chart'));
chart.setOption(option);
window.addEventListener('resize', function () {{ chart.resize(); }});
</script>
</body></html>
"""


def render_html(payload: dict[str, Any], option: dict[str, Any], echarts_js: str) -> str:
    header = payload["header"]
    certification = payload.get("certification", {})
    provenance = (
        f"source: {payload.get('source_sql', '?')} &middot; "
        f"frozen_at: {payload.get('frozen_at', '?')} &middot; "
        f"certification: {certification.get('status', 'unknown')} "
        f"({certification.get('run_id', '?')})"
    )
    return _TEMPLATE.format(
        fig_id=html.escape(payload["fig_id"]),
        caption=html.escape(header.get("caption", "")),
        must_not_say=html.escape(header.get("must_not_say", "")),
        provenance=provenance,
        echarts_js=echarts_js,
        option_json=json.dumps(option, ensure_ascii=False),
    )


def render_figure(payload: dict[str, Any], echarts_js: str) -> str:
    fig_id = payload["fig_id"]
    spec = FIGURE_SPEC.get(fig_id)
    if spec is None:
        if fig_id in NOT_YET_IMPLEMENTED:
            _die(f"{fig_id}: chart family decided (design §3.3) but not yet implemented — see NOT_YET_IMPLEMENTED")
        if fig_id in OUT_OF_SCOPE:
            _die(f"{fig_id}: rendered by hand-built slide art or a native pptxgenjs chart, not this pipeline")
        carrier = payload.get("header", {}).get("carrier")
        if carrier == "superset":
            _die(f"{fig_id}: carrier is superset, not echarts — build it as a Superset chart instead")
        _die(f"{fig_id}: not in FIGURE_SPEC, NOT_YET_IMPLEMENTED or OUT_OF_SCOPE — is this a new fig_id?")
    builder = FAMILY_BUILDERS[spec["family"]]
    option = builder(payload, spec)
    return render_html(payload, option, echarts_js)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("json_paths", nargs="+", type=Path, help="var/presentation/<fig_id>.json files")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
