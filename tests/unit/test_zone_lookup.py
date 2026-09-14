"""The zone-lookup page is checked for the things a rendered page hides.

Every number on it is computed at build time, so a wrong fold is invisible in
the HTML — it just reads as a different, plausible answer. These tests pin the
three folds that are easy to get wrong and impossible to spot afterwards: the
ratio is taken after summing, the ranking covers only the zones that carry a
schedule, and a zone with no schedule gets an answer rather than an empty card.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.presentation.zone_lookup import (
    PROFILE_FIG,
    TRANSITION_FIG,
    LookupError,
    build_context,
    build_index,
    build_records,
    load_payload,
    render_page,
    summarise_transitions,
)

PROFILE_COLUMNS = [
    "plow_zone", "has_plow_schedule", "address_count", "operations", "mean_shift",
    "min_shift", "max_shift", "mean_early", "mean_late", "shift_1_count", "shift_2_count",
    "shift_3_count", "shift_4_count", "shift_5_count", "recent_operations",
    "recent_top2_count", "last_shift",
]


def _profile_row(zone: str, mean: float | None, *, scheduled: bool = True) -> list:
    if mean is None:
        return [zone, scheduled, 2590, 0, None, None, None, None, None, 0, 0, 0, 0, 0, 0, 0, None]
    return [zone, scheduled, 9000, 19, mean, 1, 3, mean, mean, 5, 9, 5, 0, 0, 11, 7, 2]


def _profile(rows: list[list]) -> dict:
    return {
        "fig_id": PROFILE_FIG,
        "columns": PROFILE_COLUMNS,
        "rows": rows,
        "header": {},
        "source_sql": "sql/presentation/fig_bo2_06_zone_rank_profile.sql",
        "frozen_at": "2026-09-14T00:00:00+00:00",
        "certification": {"status": "certified", "run_id": "r1"},
    }


def _transitions(rows: list[list]) -> dict:
    return {
        "fig_id": TRANSITION_FIG,
        "columns": ["plow_zone", "prev_shift", "next_shift", "transitions"],
        "rows": rows,
        "header": {},
        "source_sql": "sql/presentation/fig_bo2_07_rank_persistence.sql",
        "frozen_at": "2026-09-14T00:00:00+00:00",
        "certification": {"status": "certified", "run_id": "r1"},
    }


def test_exact_and_within_one_are_counted_from_the_transition_grid() -> None:
    summary = summarise_transitions(
        [
            {"plow_zone": "A", "prev_shift": 1, "next_shift": 1, "transitions": 6},
            {"plow_zone": "A", "prev_shift": 1, "next_shift": 2, "transitions": 3},
            {"plow_zone": "A", "prev_shift": 1, "next_shift": 4, "transitions": 1},
        ]
    )
    assert summary["per_zone"]["A"] == {"transitions": 10, "exact": 6, "within1": 9}
    assert summary["city"]["transitions"] == 10


def test_the_city_rate_is_a_ratio_of_sums_not_a_mean_of_zone_rates() -> None:
    """🔴 The defect this pins: averaging per-zone percentages weights a zone
    with 1 transition the same as one with 99 (gold-sql.md R3). Here zone A is
    1/1 correct and zone B is 0/99, so the two readings are 50.0% and 1.0%."""
    context = build_context(
        _profile([_profile_row("A", 1.0), _profile_row("B", 2.0)]),
        _transitions(
            [
                ["A", 1, 1, 1],
                ["B", 1, 3, 99],
            ]
        ),
    )
    assert context["city"]["exact_pct"] == 1.0


def test_the_ranking_covers_only_the_zones_that_carry_a_schedule() -> None:
    records = build_records(
        [
            {**dict(zip(PROFILE_COLUMNS, _profile_row("A", 1.2), strict=True))},
            {**dict(zip(PROFILE_COLUMNS, _profile_row("C", 3.4), strict=True))},
            {**dict(zip(PROFILE_COLUMNS, _profile_row("X", None, scheduled=False), strict=True))},
        ],
        {"per_zone": {}},
    )
    by_zone = {r["plow_zone"]: r for r in records}
    assert (by_zone["A"]["position"], by_zone["A"]["of_zones"]) == (1, 2)
    assert (by_zone["C"]["position"], by_zone["C"]["of_zones"]) == (2, 2)
    # Never "3rd of 3": X was never in the ranking, so a position would be a
    # claim the panel does not make.
    assert by_zone["X"]["position"] is None


def test_a_zone_with_no_schedule_is_told_so_by_name() -> None:
    context = build_context(
        _profile([_profile_row("A", 1.2), _profile_row("X", None, scheduled=False)]),
        _transitions([["A", 1, 1, 18]]),
    )
    assert context["city"]["no_schedule_zones"] == ["X"]
    page = render_page(context)
    assert "no residential plowing schedule" in page
    assert "X" in page


def test_the_address_share_of_the_unscheduled_zones_keeps_two_decimals() -> None:
    # Ledger C2-11 and ADR 0008 both state 6.02%; rounding to 6.0% would make
    # the page disagree with them by eye.
    context = build_context(
        _profile([_profile_row("A", 1.2), _profile_row("X", None, scheduled=False)]),
        _transitions([["A", 1, 1, 18]]),
    )
    share = context["city"]["no_schedule_address_pct"]
    assert share == round(100 * 2590 / (9000 + 2590), 2)


def test_the_page_makes_no_network_request() -> None:
    """C7: the venue's wifi is not a dependency. No <script src>, no fetch."""
    context = build_context(
        _profile([_profile_row("A", 1.2)]), _transitions([["A", 1, 1, 18]])
    )
    page = render_page(context)
    assert "script src" not in page
    assert "fetch(" not in page
    assert "http://" not in page and "https://" not in page


def test_a_payload_for_the_wrong_figure_is_refused(tmp_path: Path) -> None:
    """Two payloads passed in the wrong order would otherwise build a page
    whose every number comes from the wrong query, without raising."""
    path = tmp_path / "FIG-BO2-06.json"
    path.write_text(json.dumps(_transitions([])), encoding="utf-8")
    with pytest.raises(LookupError):
        load_payload(path, PROFILE_FIG)


def test_a_missing_payload_names_the_command_that_makes_it(tmp_path: Path) -> None:
    with pytest.raises(LookupError, match="eda-export"):
        load_payload(tmp_path / "absent.json", PROFILE_FIG)


def test_an_empty_profile_is_refused_rather_than_rendered_blank() -> None:
    with pytest.raises(LookupError):
        build_context(_profile([]), _transitions([]))


def test_the_index_is_derived_from_the_directory(tmp_path: Path) -> None:
    (tmp_path / "slide-11-zone-rank-spread.html").write_text("x", encoding="utf-8")
    (tmp_path / "zone-lookup.html").write_text("x", encoding="utf-8")
    index = build_index(tmp_path)
    assert "slide-11-zone-rank-spread.html" in index
    assert "zone-lookup.html" in index
    # The lookup is the entry, never a row in the figure list.
    assert index.count("zone-lookup.html") == 1


def test_the_page_does_not_present_itself_as_the_city_status_tool() -> None:
    """BO §0.1 rules out a zone-status lookup because the City ships one.

    This page stays on the other side of that line — retrospective order, not
    current state — and the line is only real if the wording holds it. A title
    borrowing the City tool's name is how a retrospective page gets read as a
    status board.
    """
    context = build_context(
        _profile([_profile_row("A", 1.2)]), _transitions([["A", 1, 1, 18]])
    )
    page = render_page(context)
    title = page.split("<title>")[1].split("</title>")[0]
    assert "Know Your Zone" not in title
    # It must still point at the City's tool rather than pretend it does the job.
    assert "Know Your Zone tool" in page
