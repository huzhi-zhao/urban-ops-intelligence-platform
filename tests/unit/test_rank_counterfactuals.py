"""Unit tests for the BO-2 counterfactual probe's pure functions.

The probe's job is to *fail to explain* the scheduling rank, so the tests focus
on the ways a counterfactual could report a false exoneration: an undefined
correlation dressed up as "no relationship", ties read as disagreement, and a
drift comparison over zones present in only one half.
"""

from __future__ import annotations

from scripts.analysis.rank_counterfactuals import (
    drift_counterfactual,
    event_order,
    linkage_check,
    mean_shift_by_zone,
    semantics_check,
    snowfall_counterfactual,
)


def row(event: str, zone: str, shift: float, start: str) -> dict:
    return {
        "snow_ban_id": event,
        "plow_zone": zone,
        "shift_number": str(shift),
        "shift_start": start,
    }


# ── snowfall counterfactual ──────────────────────────────────────────────────


def test_zero_variance_snowfall_reports_no_correlation_at_all() -> None:
    # This is the ADR 0008 failure mode in miniature: a constant column can
    # neither support nor refute anything, and printing r = 0.0 would read as
    # "measured, no relationship".
    result = snowfall_counterfactual(
        {"A": 1.0, "B": 3.0}, {"A": 900.0, "B": 900.0},
    )
    assert result["distinct_snowfall_totals"] == 1
    assert result["correlation_rank_vs_snowfall"] is None
    assert result["spread_cm"] == 0.0


def test_varying_snowfall_produces_a_correlation() -> None:
    result = snowfall_counterfactual(
        {"A": 1.0, "B": 2.0, "C": 3.0}, {"A": 100.0, "B": 200.0, "C": 300.0},
    )
    assert result["correlation_rank_vs_snowfall"] == 1.0


def test_snowfall_counterfactual_only_compares_zones_present_in_both() -> None:
    result = snowfall_counterfactual(
        {"A": 1.0, "B": 2.0}, {"A": 100.0, "B": 200.0, "Unscheduled": 150.0},
    )
    assert result["zones_compared"] == 2


# ── shift_number semantics ───────────────────────────────────────────────────


def test_shift_number_agreeing_with_time_is_consistent() -> None:
    rows = [
        row("e1", "A", 1, "2020-01-01T06:00:00"),
        row("e1", "B", 2, "2020-01-02T06:00:00"),
        row("e1", "C", 3, "2020-01-03T06:00:00"),
    ]
    result = semantics_check(rows)
    assert result["events_where_shift_number_matches_time"] == 1
    assert result["events_where_it_does_not"] == []


def test_zones_sharing_a_shift_number_share_a_start_and_are_not_a_conflict() -> None:
    # Several zones are plowed in the same batch. Ties must not be scored as
    # disagreement, or every real event would look inconsistent.
    rows = [
        row("e1", "A", 1, "2020-01-01T06:00:00"),
        row("e1", "B", 1, "2020-01-01T06:00:00"),
        row("e1", "C", 2, "2020-01-02T06:00:00"),
    ]
    assert semantics_check(rows)["events_where_shift_number_matches_time"] == 1


def test_shift_number_running_against_time_is_flagged() -> None:
    rows = [
        row("e1", "A", 1, "2020-01-03T06:00:00"),
        row("e1", "B", 2, "2020-01-02T06:00:00"),
        row("e1", "C", 3, "2020-01-01T06:00:00"),
    ]
    result = semantics_check(rows)
    assert result["events_where_shift_number_matches_time"] == 0
    assert result["events_where_it_does_not"][0]["event"] == "e1"


def test_an_event_with_one_shift_value_is_counted_separately() -> None:
    # Nothing to order, so it is neither evidence for nor against the reading.
    rows = [row("e1", "A", 1, "2020-01-01T06:00:00"), row("e1", "B", 1, "2020-01-01T06:00:00")]
    result = semantics_check(rows)
    assert result["events_with_one_shift_value"] == 1
    assert result["events_where_shift_number_matches_time"] == 0


# ── rank and drift ───────────────────────────────────────────────────────────


def test_mean_shift_averages_across_events() -> None:
    rows = [
        row("e1", "A", 1, "2020-01-01T06:00:00"),
        row("e2", "A", 3, "2021-01-01T06:00:00"),
    ]
    assert mean_shift_by_zone(rows) == {"A": 2.0}


def test_events_are_ordered_by_their_first_shift() -> None:
    rows = [
        row("late", "A", 1, "2021-01-01T06:00:00"),
        row("early", "A", 1, "2020-01-01T06:00:00"),
        row("early", "B", 2, "2020-01-02T06:00:00"),
    ]
    assert event_order(rows) == ["early", "late"]


def _four_events(late_zone_v: float) -> list[dict]:
    rows = []
    for index, event in enumerate(["e1", "e2", "e3", "e4"]):
        shift_v = 1.0 if index < 2 else late_zone_v
        rows.append(row(event, "A", 1.0, f"202{index}-01-01T06:00:00"))
        rows.append(row(event, "B", 2.0, f"202{index}-01-02T06:00:00"))
        rows.append(row(event, "V", shift_v, f"202{index}-01-03T06:00:00"))
    return rows


def test_an_unchanged_schedule_reports_a_stable_rank() -> None:
    assert drift_counterfactual(_four_events(1.0))["spearman_early_vs_late"] == 1.0


def test_a_zone_that_moved_lowers_the_rank_agreement() -> None:
    drift = drift_counterfactual(_four_events(5.0))
    assert drift["spearman_early_vs_late"] < 1.0
    assert drift["largest_moves"][0]["zone"] == "V"
    assert drift["largest_moves"][0]["delta"] == 4.0


def test_drift_reports_zones_missing_from_one_half() -> None:
    rows = _four_events(1.0)
    rows.append(row("e4", "NEW", 3.0, "2023-01-04T06:00:00"))
    drift = drift_counterfactual(rows)
    assert drift["zones_late_only"] == ["NEW"]
    assert "NEW" not in {m["zone"] for m in drift["largest_moves"]}


def test_too_few_events_reports_no_drift_number() -> None:
    assert drift_counterfactual([row("e1", "A", 1, "2020-01-01T06:00:00")])[
        "spearman_early_vs_late"
    ] is None


# ── linkage ──────────────────────────────────────────────────────────────────


def test_linkage_separates_bans_without_shifts_from_dangling_references() -> None:
    rows = [row("b1", "A", 1, "2020-01-01T06:00:00"), row("ghost", "A", 1, "2020-02-01T06:00:00")]
    link = linkage_check(rows, {"b1", "b2", "b3"})
    # b2/b3 having no shifts is expected — most bans are not full plow events.
    assert link["bans_with_schedule"] == 1
    assert link["ban_coverage"] == round(1 / 3, 4)
    # A shift row pointing at a non-existent ban is not expected.
    assert link["schedule_events_with_no_matching_ban"] == ["ghost"]
