"""Unit tests for scripts.presentation.demand_plan_diagnostics (synthetic inputs)."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from scripts.presentation import demand_plan_diagnostics as dx

VERSION = "m1-test"
COLUMNS = [
    "model_version", "snowfall_event_id", "fit_role", "plow_zone", "address_count",
    "predicted_count", "actual_count", "shift_number",
]
ZONES = ["A", "B", "C", "D", "E", "F"]


def _panel() -> dict:
    rows = []
    for event, role, plan in (("E0", "in_sample", True), ("E1", "holdout", True),
                              ("E2", "holdout", False)):
        for i, zone in enumerate(ZONES):
            # Point estimate falls with the zone index, so A ranks first.
            rows.append([VERSION, event, role, zone, 1000, 60.0 - 10 * i, 10,
                         (i % 5) + 1 if plan else None])
    return {"columns": COLUMNS, "rows": rows}


def _summary(low: float = 5.0, high: float = 20.0) -> dict:
    cells = []
    for event in ("E0", "E1", "E2"):
        for i, zone in enumerate(ZONES):
            cells.append({
                "snowfall_event_id": event, "plow_zone": zone,
                "prediction_low": low, "prediction_high": high,
                "point_rank": i + 1,
                "top_k_probabilities": {"2": 0.9 - 0.1 * i},
            })
    return {"model_version": VERSION, "review_prompt": {"top_k": 2, "minimum_shift": 4},
            "cells": cells}


def test_coverage_counts_only_holdout_cells_and_splits_the_misses() -> None:
    covered = dx.coverage_by_event(_panel(), _summary(), VERSION)
    assert [e["snowfall_event_id"] for e in covered] == ["E1", "E2"]
    assert covered[0] == {"snowfall_event_id": "E1", "has_plan": True, "n_cells": 6,
                          "covered": 6, "above": 0, "below": 0}
    below = dx.coverage_by_event(_panel(), _summary(low=11, high=30), VERSION)
    assert below[0]["below"] == 6 and below[0]["covered"] == 0
    above = dx.coverage_by_event(_panel(), _summary(low=0, high=9), VERSION)
    assert above[0]["above"] == 6


def test_direction_counts_mirror_the_prompt_rule() -> None:
    # Shifts cycle 1..5 over A..F: A=1 B=2 C=3 D=4 E=5 F=1. Top 2 = A,B (early);
    # bottom 2 = E (shift 5), F (shift 1). Mirror of shift >= 4 is shift <= 2.
    [event] = dx.direction_counts(_panel(), _summary(), VERSION)
    assert event["snowfall_event_id"] == "E1"
    assert event["forward"]["zones"] == []
    assert event["reverse"]["zones"] == ["F"]
    assert event["reverse"]["rule"] == "rank > 4 and shift <= 2"


def test_stability_ceiling_is_the_best_zone_per_holdout_event() -> None:
    ceiling = dx.stability_ceiling(_panel(), _summary(), VERSION)
    assert [c["snowfall_event_id"] for c in ceiling] == ["E1", "E2"]
    assert ceiling[0]["max_top_k_share"] == pytest.approx(0.9)


def test_a_summary_for_another_version_is_refused() -> None:
    summary = _summary()
    summary["model_version"] = "other"
    with pytest.raises(ValueError, match="belongs to"):
        dx.coverage_by_event(_panel(), summary, VERSION)


def test_rank_flips_are_matched_against_the_coefficient_sign(tmp_path: Path) -> None:
    # Replica 1 keeps the point order; replica 2 reverses it and has a negative term.
    with (tmp_path / "bootstrap_predictions.csv").open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["replicate_id", "snowfall_event_id", "plow_zone", "predicted_mean"])
        for i, zone in enumerate(ZONES):
            writer.writerow([1, "E1", zone, 60 - 10 * i])
            writer.writerow([2, "E1", zone, 10 + 10 * i])
    with (tmp_path / "bootstrap_coefficients.csv").open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["replicate_id", "term", "estimate"])
        writer.writerows([[1, "lag", 0.5], [2, "lag", -0.5], [1, "other", -1], [2, "other", 1]])
    flip = dx.rank_flip_vs_coefficient(_panel(), tmp_path, VERSION, "E1", "lag")
    assert flip["replicates"] == 2
    assert flip["inverted_order"] == 1
    assert flip["term_negative"] == 1
    assert flip["overlap"] == 1
    assert flip["term_positive_share"] == pytest.approx(0.5)
