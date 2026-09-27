"""Batch-A gates on the demand-and-plan panel (FIG-BO8-03, ADR 0015).

The fixture is a synthetic panel with the production shape — 59 events x 22
zones, 17 events carrying a published shift, 7 holdout events of which one is
planned — so each test breaks exactly one property and checks the gate names it.
"""

from __future__ import annotations

import copy

import pytest

from scripts.presentation.demand_plan import (
    DEMAND_PLAN_FIG,
    PanelGateError,
    check_panel,
    gate_failures,
)
from scripts.presentation.portfolio import LOOKUP

COLUMNS = [
    "model_version", "snowfall_event_id", "fit_role", "plow_zone",
    "address_count", "predicted_count", "actual_count", "shift_number",
]
VERSION = "m1-test"
ZONES = [f"Z{i:02d}" for i in range(22)]


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
