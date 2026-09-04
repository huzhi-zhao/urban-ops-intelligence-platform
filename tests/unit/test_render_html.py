"""`scripts/presentation/render_html.py` — see design/20260903-presentation-figure-rendering.md.

Two things this must prove that a visual spot-check cannot: (1) the module's
fig_id -> chart-family table stays in sync with the real `carrier: echarts`
figures in `sql/presentation/` as that set changes, and (2) every rendered
page is genuinely self-contained (C3 there) — no external references can
creep back in through a copy-pasted `<script src=...>` or a stray `fetch()`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.eda.run import PRESENTATION_DIR, load_figures
from scripts.presentation.render_html import (
    FAMILY_BUILDERS,
    FIGURE_SPEC,
    NOT_YET_IMPLEMENTED,
    OUT_OF_SCOPE,
    VENDOR_JS,
    load_payload,
    render_figure,
)

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures" / "presentation"


def _echarts_carrier_fig_ids() -> set[str]:
    return {f.fig_id for f in load_figures(PRESENTATION_DIR) if f.carrier == "echarts"}


def test_vendor_js_is_present_and_not_a_stub():
    # A missing or truncated vendor file fails every render silently-ish
    # (echarts.init throws inside the page, not at build time) — catch it here.
    assert VENDOR_JS.exists(), "run: curl the pinned ECharts build into scripts/presentation/vendor/"
    text = VENDOR_JS.read_text(encoding="utf-8")
    assert len(text) > 500_000, "vendored file looks truncated, not a real ECharts UMD build"
    assert "Apache" in text[:2000]


def test_every_carrier_echarts_figure_is_accounted_for():
    """Every real fig_id with `carrier: echarts` must appear in exactly one of
    FIGURE_SPEC (implemented) / NOT_YET_IMPLEMENTED (decided, not built) /
    OUT_OF_SCOPE (deliberately never built here) — never in none, never in
    two. This is the test that catches a new fig_*.sql file landing without
    anyone updating this module."""
    real = _echarts_carrier_fig_ids()
    buckets = [set(FIGURE_SPEC), set(NOT_YET_IMPLEMENTED), set(OUT_OF_SCOPE)]
    covered = set().union(*buckets)
    assert real == covered, f"missing: {real - covered} · stale (no longer echarts): {covered - real}"
    overlaps = buckets[0] & buckets[1] | buckets[0] & buckets[2] | buckets[1] & buckets[2]
    assert not overlaps, f"a fig_id is in more than one bucket: {overlaps}"


def test_every_figure_spec_family_has_a_builder():
    for fig_id, spec in FIGURE_SPEC.items():
        assert spec["family"] in FAMILY_BUILDERS, f"{fig_id}: family {spec['family']!r} has no builder"


@pytest.mark.parametrize("fig_id", sorted(FIGURE_SPEC))
def test_render_is_self_contained(fig_id: str):
    """No resource the page loads at view-time may reach the network (C3).
    The vendored ECharts file's own Apache licence header legitimately
    contains "http://www.apache.org/..." as plain-text comment — that is not
    a resource load, so this checks for load *sites* (`src=`, `<link`,
    `fetch(`), not for the substring "http" anywhere in the file."""
    payload = load_payload(FIXTURES / f"{fig_id}.json")
    echarts_js = VENDOR_JS.read_text(encoding="utf-8")
    out = render_figure(payload, echarts_js)
    assert "<script src" not in out
    assert "<link " not in out
    assert "fetch(" not in out
    assert "importScripts(" not in out
    assert "echarts.init(" in out
    assert "echarts.setOption" not in out  # sanity: we call chart.setOption, not a nonexistent API


@pytest.mark.parametrize("fig_id", sorted(FIGURE_SPEC))
def test_render_escapes_caption_and_must_not_say(fig_id: str, tmp_path: Path):
    payload = load_payload(FIXTURES / f"{fig_id}.json")
    payload["header"] = dict(payload["header"])
    payload["header"]["caption"] = "<script>alert(1)</script> injected caption"
    echarts_js = VENDOR_JS.read_text(encoding="utf-8")
    out = render_figure(payload, echarts_js)
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out


@pytest.mark.parametrize("fig_id", sorted(FIGURE_SPEC))
def test_render_embeds_provenance(fig_id: str):
    payload = load_payload(FIXTURES / f"{fig_id}.json")
    echarts_js = VENDOR_JS.read_text(encoding="utf-8")
    out = render_figure(payload, echarts_js)
    assert payload["source_sql"] in out
    assert payload["certification"]["status"] in out


def test_render_figure_refuses_a_superset_carrier_fig(tmp_path: Path):
    payload = {
        "fig_id": "FIG-BO2-05",
        "source_sql": "sql/presentation/fig_bo2_05_ban_event_join.sql",
        "frozen_at": "2026-09-03T00:00:00+00:00",
        "certification": {"status": "certified", "run_id": "x"},
        "header": {"carrier": "superset", "caption": "c", "must_not_say": "m"},
        "columns": ["a"],
        "rows": [[1]],
    }
    with pytest.raises(SystemExit, match="superset"):
        render_figure(payload, "/* stub */")


def test_load_payload_rejects_a_malformed_file(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"fig_id": "FIG-X"}), encoding="utf-8")
    with pytest.raises(SystemExit):
        load_payload(bad)
