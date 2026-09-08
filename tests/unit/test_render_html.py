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
    ALREADY_IN_DECK,
    DECK_THEME,
    ENGLISH_CAPTIONS,
    FAMILY_BUILDERS,
    FIGURE_SPEC,
    NOT_YET_IMPLEMENTED,
    OFFLINE_SUPERSET_SPEC,
    OUT_OF_SCOPE,
    SLIDE_SLOTS,
    THEME_NAME,
    UNFILLED_SLOTS,
    VENDOR_JS,
    _slide_filename,
    build_option,
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
def test_render_escapes_caption_and_must_not_say(fig_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Injects through the header, so any fig_id with a hand-checked English
    translation (`ENGLISH_CAPTIONS`) must have that translation removed for
    the duration of the test — otherwise the injected header text is never
    read and this would pass for the wrong reason."""
    monkeypatch.delitem(ENGLISH_CAPTIONS, fig_id, raising=False)
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


def test_every_slide_slot_is_renderable():
    """A slot names a real fig_id and, if it overrides the chart family, a
    family that has a builder. Without this a slot is only discovered to be
    broken at render time, the evening before the conference."""
    known = {**FIGURE_SPEC, **OFFLINE_SUPERSET_SPEC}
    for slot in SLIDE_SLOTS:
        assert slot["fig_id"] in known, f"slide {slot['slide']}: {slot['fig_id']} is not implemented"
        merged = {**known[slot["fig_id"]], **slot.get("spec", {})}
        assert merged["family"] in FAMILY_BUILDERS, f"slide {slot['slide']}: no builder for {merged['family']!r}"


def test_slide_slot_filenames_are_unique():
    """Two slots share slide 39 and three share FIG-BO4-01/BO2-01, so the filename
    keys on the slot's slug — a duplicate would silently overwrite a page and
    leave a deck slot with no chart."""
    names = [_slide_filename(s) for s in SLIDE_SLOTS]
    assert len(names) == len(set(names)), f"duplicate slide filenames: {names}"


def test_a_slide_is_never_both_filled_and_unfilled():
    filled = {s["slide"] for s in SLIDE_SLOTS}
    unfilled = {slide for slide, _reason in UNFILLED_SLOTS}
    in_deck = {slide for slide, _reason in ALREADY_IN_DECK}
    assert not (filled & unfilled), f"slide listed as both rendered and unfilled: {filled & unfilled}"
    assert not (filled & in_deck), f"slide listed as both rendered and already-in-deck: {filled & in_deck}"
    assert not (unfilled & in_deck), f"slide listed as both unfilled and already-in-deck: {unfilled & in_deck}"


def test_a_slot_that_reshapes_the_figure_carries_its_own_caption():
    """A slot overriding `family` draws a different chart from the fig_id's
    own, so inheriting that fig_id's caption would describe a chart that is
    not on the page — the failure mode the FIG-BO6-01 event grid hit."""
    for slot in SLIDE_SLOTS:
        if "family" in slot.get("spec", {}):
            assert slot.get("caption"), f"slide {slot['slide']} changes the chart family but reuses the fig's caption"
            assert slot.get("must_not_say"), f"slide {slot['slide']} changes the chart family but reuses must_not_say"


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


def test_the_offline_superset_table_holds_only_superset_carriers():
    """The point of this table is that `carrier` says which chart shape, not
    where the chart runs — an echarts-carrier fig_id listed here would be a
    figure hiding from the three-bucket accounting that keeps FIGURE_SPEC
    honest, which is the one thing this table must not become."""
    echarts = _echarts_carrier_fig_ids()
    assert not (set(OFFLINE_SUPERSET_SPEC) & echarts), (
        f"echarts-carrier figures belong in FIGURE_SPEC: {set(OFFLINE_SUPERSET_SPEC) & echarts}"
    )
    real = {f.fig_id for f in load_figures(PRESENTATION_DIR)}
    assert set(OFFLINE_SUPERSET_SPEC) <= real, f"no such fig_id: {set(OFFLINE_SUPERSET_SPEC) - real}"
    for fig_id, spec in OFFLINE_SUPERSET_SPEC.items():
        assert spec["family"] in FAMILY_BUILDERS, f"{fig_id}: family {spec['family']!r} has no builder"


def test_a_superset_figure_not_in_the_offline_table_is_still_refused():
    """The refusal is the default and stays the default: opting one figure in
    must not open the door for every superset payload."""
    payload = load_payload(FIXTURES / "FIG-BO6-03.json")
    payload["fig_id"] = "FIG-BO2-05"
    with pytest.raises(SystemExit, match="superset"):
        render_figure(payload, "/* stub */")


@pytest.mark.parametrize("fig_id", sorted(OFFLINE_SUPERSET_SPEC))
def test_offline_superset_render_is_self_contained(fig_id: str):
    payload = load_payload(FIXTURES / f"{fig_id}.json")
    out = render_figure(payload, VENDOR_JS.read_text(encoding="utf-8"))
    assert "<script src" not in out
    assert "fetch(" not in out
    assert "echarts.init(" in out


def test_the_two_load_level_scales_never_share_an_axis():
    """Slide 41's card asks for "two coordinate systems, never a shared axis",
    and a grouped bar on one axis would satisfy every other test here while
    stating exactly the comparison the launch record forbids."""
    payload = load_payload(FIXTURES / "FIG-BO6-03.json")
    spec = OFFLINE_SUPERSET_SPEC["FIG-BO6-03"]
    option = FAMILY_BUILDERS[spec["family"]](payload, spec)
    assert len(option["grid"]) == 2, "the two profiles must be drawn in two separate grids"
    assert len(option["xAxis"]) == 2 and len(option["yAxis"]) == 2
    assert {s["xAxisIndex"] for s in option["series"]} == {0, 1}
    assert {s["yAxisIndex"] for s in option["series"]} == {0, 1}
    assert not any("max" in axis for axis in option["yAxis"]), "a pinned shared max is a shared scale"


def test_every_slot_carries_the_deck_box_it_fills():
    """The exported PNG's aspect ratio comes from these numbers. PowerPoint
    stretches a mismatched image without complaint, and a stretched chart
    misreports its own values — so a slot without a measured box is a slot
    whose figure will quietly lie about its bars."""
    for slot in SLIDE_SLOTS:
        box = slot.get("deck_box_in")
        size = slot.get("spec_size")
        assert box and size, f"slide {slot['slide']}: no deck_box_in / spec_size"
        box_ratio = box[0] / box[1]
        px_ratio = size[0] / size[1]
        assert abs(box_ratio - px_ratio) < 0.05, (
            f"slide {slot['slide']}: pixel size {size} does not match deck box {box} "
            f"({px_ratio:.2f} vs {box_ratio:.2f})"
        )


# ---------------------------------------------------------------------------
# The shared theme
# ---------------------------------------------------------------------------


def test_the_theme_encodes_no_value_in_colour():
    """Slide 11's own card: "Colour must carry no value judgement — no
    red-for-bad. One hue, two saturations." A magnitude-keyed ramp is the
    single most tempting thing to copy from a dashboard and the one thing this
    deck cannot have: it would state that a high load score is a bad outcome,
    which BO-6's launch record forbids. So the theme's palette is checked to be
    a set of discrete hues, with no `visualMap` and no gradient in it."""
    assert "visualMap" not in DECK_THEME
    for entry in DECK_THEME["color"]:
        assert isinstance(entry, str) and entry.startswith("#"), (
            f"{entry!r} is not a flat colour — a gradient in the series palette is a ramp"
        )


def test_bar_washes_stay_inside_one_hue():
    """The decorative gradient exists to give a bar body, not to say anything.
    Both stops must therefore be the same hue: the moment they differ, the
    fill starts encoding the bar's own extent as a colour change."""
    from scripts.presentation.render_html import _gradient

    for horizontal in (True, False):
        stops = _gradient("#2E6E8E", horizontal=horizontal)["colorStops"]
        hues = {stop["color"][:7].upper() for stop in stops}
        assert hues == {"#2E6E8E"}, f"wash spans more than one hue: {hues}"


def test_charts_do_not_animate():
    """The PNG button reads the canvas on click. With an entry animation a
    click inside the first second exports a half-grown chart — every bar
    truthfully labelled and drawn at the wrong length."""
    assert DECK_THEME["animation"] is False


def test_the_page_registers_the_theme_before_it_is_used():
    """`echarts.init(el, name)` with a name that was never registered does not
    raise — it silently falls back to the default look, which is the CAD-sketch
    appearance this theme exists to replace."""
    payload = load_payload(FIXTURES / "FIG-BO2-01.json")
    html_text = render_figure(payload, "/* echarts */")
    register_at = html_text.index(f"echarts.registerTheme('{THEME_NAME}'")
    init_at = html_text.index(f"echarts.init(document.getElementById('chart'), '{THEME_NAME}')")
    assert register_at < init_at


def test_the_mean_and_range_figure_is_not_a_degenerate_boxplot():
    """FIG-BO2-01 has a mean and a [min, max] per zone and no quartiles. Spelt
    as a boxplot with q1=median=q3, ECharts draws a zero-height box — a line —
    so the ranking the slide is about has no body on the page, and a viewer who
    reads boxplots sees quartiles that were never computed."""
    payload = load_payload(FIXTURES / "FIG-BO2-01.json")
    spec = FIGURE_SPEC["FIG-BO2-01"]
    option = FAMILY_BUILDERS[spec["family"]](payload, spec)
    kinds = {s["type"] for s in option["series"]}
    assert "boxplot" not in kinds, "a mean is not a median and a range is not a quartile"
    assert "bar" in kinds
    assert option["series"][0]["markLine"]["data"], "the min/max range must still be drawn"


def test_no_bottom_axis_name_can_be_clipped():
    """An axis name parked at the axis end overhangs the grid and is cut off by
    the canvas edge — "address count" reached the slide as "ad". Centring is
    the only placement the right-hand edge cannot truncate."""
    for fig_id, spec in list(FIGURE_SPEC.items()) + list(OFFLINE_SUPERSET_SPEC.items()):
        path = FIXTURES / f"{fig_id}.json"
        if not path.exists():
            continue
        option = build_option(load_payload(path), spec)
        axes = option.get("xAxis")
        for axis in axes if isinstance(axes, list) else [axes]:
            if isinstance(axis, dict) and axis.get("name"):
                assert axis.get("nameLocation") == "middle", (
                    f"{fig_id}: x-axis name {axis['name']!r} is parked at the axis end"
                )


def test_a_slot_never_claims_a_deck_box_twice():
    """Slides 39 and 41 have one picture box each, and its placeholder names two
    figures. Two slots on one slide produced two images for one box, which is
    what left both slides unfillable — so a slide appears here once, and a box
    holding two figures says so with `with_fig_id`."""
    slides = [slot["slide"] for slot in SLIDE_SLOTS]
    assert len(slides) == len(set(slides)), (
        f"more than one slot claims the same slide: "
        f"{sorted({s for s in slides if slides.count(s) > 1})}"
    )


def test_a_two_figure_box_names_a_companion_that_exists():
    """`with_fig_id` is resolved against the JSON files passed on the command
    line. A typo would print a skip line and silently leave the slide empty."""
    known = set(FIGURE_SPEC) | set(OFFLINE_SUPERSET_SPEC)
    for slot in SLIDE_SLOTS:
        companion = slot.get("with_fig_id")
        if companion is None:
            continue
        assert companion in known, f"slide {slot['slide']}: unknown companion {companion}"
        assert companion != slot["fig_id"]
        assert "family" in slot.get("spec", {}), (
            f"slide {slot['slide']}: two figures in one box need a composite family"
        )


def test_a_composite_refuses_to_draw_half_of_itself():
    """Without its companion the builder must die, not quietly render one
    figure into a box the deck expects to hold two — a half-filled appendix
    looks finished."""
    slot = next(s for s in SLIDE_SLOTS if s.get("with_fig_id"))
    spec = {**(FIGURE_SPEC.get(slot["fig_id"]) or OFFLINE_SUPERSET_SPEC[slot["fig_id"]]), **slot["spec"]}
    payload = load_payload(FIXTURES / f"{slot['fig_id']}.json")
    with pytest.raises(SystemExit):
        build_option(payload, spec)


def test_the_two_load_level_rulers_stay_apart_in_the_composite():
    """The composite adds two more sub-charts to slide 41 and the constraint
    does not weaken: four charts, four scales, no shared axis and no shared
    colour ramp."""
    slot = next(s for s in SLIDE_SLOTS if s["slide"] == 41)
    spec = {**OFFLINE_SUPERSET_SPEC.get(slot["fig_id"], FIGURE_SPEC.get(slot["fig_id"], {})), **slot["spec"]}
    payload = {
        **load_payload(FIXTURES / "FIG-BO6-01.json"),
        "companion": load_payload(FIXTURES / "FIG-BO6-03.json"),
    }
    option = build_option(payload, spec)
    assert len(option["grid"]) == 4
    assert {s["xAxisIndex"] for s in option["series"]} == {0, 1, 2, 3}
    assert {s["yAxisIndex"] for s in option["series"]} == {0, 1, 2, 3}
    maxima = {vm["max"] for vm in option["visualMap"]}
    assert len(maxima) == 2, "the two blocks must not share a colour ceiling"
    assert not any("max" in axis for axis in option["yAxis"]), "a pinned shared max is a shared scale"
