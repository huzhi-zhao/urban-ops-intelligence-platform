"""Batch-A gates on the demand-and-plan panel (FIG-BO8-03, ADR 0015).

The fixture is a synthetic panel with the production shape — 59 events x 22
zones, 17 events carrying a published shift, 7 holdout events of which one is
planned — so each test breaks exactly one property and checks the gate names it.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from scripts.presentation.demand_plan import (
    DEMAND_PLAN_FIG,
    ESTIMATE_RANGE_PENDING,
    ForecastVersionError,
    PanelGateError,
    build_demand_plan,
    check_panel,
    choose_version,
    gate_failures,
)
from scripts.presentation.portfolio import LOOKUP

COLUMNS = [
    "model_version", "snowfall_event_id", "fit_role", "plow_zone",
    "address_count", "predicted_count", "actual_count", "shift_number",
    "event_start_date", "event_end_date", "total_snowfall_cm", "snow_season",
    "event_rule_version", "plow_event_id", "first_shift_start_utc",
    "forecast_source_max_ingest_date", "address_count_snapshot_date",
]
VERSION = "m1-test"
ZONES = [f"Z{i:02d}" for i in range(22)]
ZONE_PAGE = Path(__file__).resolve().parents[2] / "dashboard" / "src" / "zone.jsx"


def _panel() -> dict:
    rows = []
    for e in range(59):
        event = f"SNOW-{e:03d}"
        holdout = e >= 52  # last 7 events
        # 16 planned in-sample events plus exactly one planned holdout event.
        planned = e < 16 or e == 52
        for z, zone in enumerate(ZONES):
            rows.append([
                VERSION, event, "holdout" if holdout else "in_sample", zone,
                1000 + z, 5.0, 4, (z % 5) + 1 if planned else None,
                f"2020-01-{1 + e % 28:02d}" if not holdout else f"2025-12-{e - 40:02d}",
                f"2020-01-{1 + e % 28:02d}" if not holdout else f"2025-12-{e - 40:02d}",
                3.14159, "2025-2026" if holdout else "2019-2020", "v1",
                f"OP-{e}" if planned else None,
                "2025-12-21 13:00:00" if planned else None,
                "2026-08-22", "2026-08-17",
            ])
    return {"fig_id": DEMAND_PLAN_FIG, "columns": COLUMNS, "rows": rows}


def test_the_production_shape_passes() -> None:
    assert gate_failures(_panel(), VERSION) == []
    check_panel(_panel(), VERSION)


def test_versions_are_gated_separately() -> None:
    panel = _panel()
    other = copy.deepcopy(panel["rows"][:10])
    for row in other:
        row[0] = "m1-other"
    panel["rows"].extend(other)
    assert gate_failures(panel, VERSION) == []
    assert any("cells: 10" in f for f in gate_failures(panel, "m1-other"))


def test_an_unknown_version_is_refused() -> None:
    assert gate_failures(_panel(), "nope") == ["no rows for model_version 'nope'"]


def test_a_duplicated_cell_is_named() -> None:
    panel = _panel()
    panel["rows"][1][3] = panel["rows"][0][3]
    assert any(f.startswith("distinct (event, zone)") for f in gate_failures(panel, VERSION))


def test_losing_a_published_shift_is_caught_twice() -> None:
    # One zone of a planned event loses its shift: the planned count drops and
    # the event now mixes planned and unplanned zones — the fan-out/join fault.
    panel = _panel()
    panel["rows"][0][7] = None
    failures = gate_failures(panel, VERSION)
    assert any("cells with a published shift: 373" in f for f in failures)
    assert any("only some zones" in f for f in failures)


def test_the_holdout_split_is_gated() -> None:
    panel = _panel()
    for row in panel["rows"]:
        if row[1] == "SNOW-052":
            row[2] = "in_sample"
    failures = gate_failures(panel, VERSION)
    assert any("holdout cells: 132" in f for f in failures)
    assert any("holdout cells with a published shift: 0" in f for f in failures)


def test_an_unknown_fit_role_is_refused() -> None:
    panel = _panel()
    panel["rows"][0][2] = "rolling"
    assert any("unknown fit_role" in f for f in gate_failures(panel, VERSION))


def test_a_missing_denominator_is_refused() -> None:
    panel = _panel()
    panel["rows"][0][4] = None
    assert any("address_count" in f for f in gate_failures(panel, VERSION))


def test_the_wrong_figure_is_refused() -> None:
    panel = _panel()
    panel["fig_id"] = "FIG-BO8-01"
    with pytest.raises(PanelGateError, match="expected FIG-BO8-03"):
        check_panel(panel, VERSION)


def test_the_panel_feeds_the_zone_lookup_role() -> None:
    # It drives a section of #/zone (design §3.8), so it is counted with the
    # lookup queries, not with the core findings.
    assert DEMAND_PLAN_FIG in LOOKUP


# ── The #/zone fold (batch C skeleton) ────────────────────────────────────────


def _two_versions() -> dict:
    panel = _panel()
    other = copy.deepcopy(panel["rows"])
    for row in other:
        row[0] = "m1-nomonth"
    panel["rows"].extend(other)
    return panel


def test_several_versions_need_an_explicit_choice() -> None:
    with pytest.raises(ForecastVersionError, match="name one"):
        choose_version(_two_versions(), None)
    assert choose_version(_two_versions(), VERSION) == VERSION
    assert choose_version(_panel(), None) == VERSION


def test_an_unknown_requested_version_is_refused() -> None:
    with pytest.raises(ForecastVersionError, match="not in"):
        choose_version(_panel(), "m1-typo")


def test_the_fold_refuses_a_panel_the_gates_reject() -> None:
    panel = _panel()
    panel["rows"].pop()
    with pytest.raises(PanelGateError):
        build_demand_plan(panel, VERSION)


def test_no_point_estimate_leaves_the_fold_without_its_range() -> None:
    # dashboard §10.2 ②: the value is dropped, not merely hidden by the page.
    folded = build_demand_plan(_panel(), VERSION)
    cells = [c for z in folded["zones"].values() for c in z["cells"].values()]
    assert len(cells) == 1298
    assert all(c["estimate"] is None for c in cells)
    assert all(c["estimate_status"] == ESTIMATE_RANGE_PENDING for c in cells)
    assert all(c["prompt"] is None for c in cells)
    assert "5.0" not in json.dumps(folded["zones"]), "a predicted value leaked into the page data"


def test_the_section_opens_on_the_holdout_event_with_a_plan() -> None:
    folded = build_demand_plan(_panel(), VERSION)
    assert folded["default_event"] == "SNOW-052"
    event = next(e for e in folded["events"] if e["snowfall_event_id"] == "SNOW-052")
    assert event["fit_role"] == "holdout" and event["has_plan"]
    assert folded["counts"] == {"events": 59, "events_with_plan": 17, "zones": 22}


def test_an_event_without_an_operation_carries_no_shift() -> None:
    folded = build_demand_plan(_panel(), VERSION)
    event = next(e for e in folded["events"] if e["snowfall_event_id"] == "SNOW-030")
    assert not event["has_plan"] and event["operation_start"] is None
    assert folded["zones"]["Z00"]["cells"]["SNOW-030"]["shift_number"] is None


def _rendered_text(page: str) -> str:
    return re.sub(r"//[^\n]*|/\*.*?\*/", "", page, flags=re.S)


def test_the_section_reads_only_keys_the_fold_emits() -> None:
    folded = build_demand_plan(_panel(), VERSION)
    page = ZONE_PAGE.read_text(encoding="utf-8")
    event_keys = set(folded["events"][0])
    cell_keys = set(next(iter(folded["zones"]["Z00"]["cells"].values())))
    for name, keys in (("section", set(folded)), ("snowfall", event_keys),
                       ("cell", cell_keys), ("row", cell_keys)):
        used = set(re.findall(rf"\b{name}\.([a-z_][a-z0-9_]*)", page))
        # `estimate.point/low/high` are read off `cell.estimate`, not `cell`.
        missing = sorted(used - keys - {"estimate"} if name in ("cell", "row") else used - keys)
        assert not missing, f"the page reads {name} keys the fold never emits: {missing}"


def test_the_section_never_calls_a_backtest_a_forecast_or_dates_an_update() -> None:
    # ADR 0015 §4: the estimate is a backtest on archived weather, and the
    # schedule source has no update field. Both words stay off the page.
    text = _rendered_text(ZONE_PAGE.read_text(encoding="utf-8")).lower()
    assert "forecast" not in text
    assert "updated" not in text
