"""Unit tests for R11 question 2 scoring — the §5.1 registration made executable."""

from __future__ import annotations

import datetime as dt

import pytest

from scripts.models import outlook_evaluate as ev

D = dt.date


def _e(eid: str, start: dt.date, end: dt.date | None = None, cm: float = 5.0) -> ev.Event:
    return ev.Event(eid, start, end or start, cm)


REAL = _e("SNOW-20251218", D(2025, 12, 18), D(2025, 12, 19), 10.0)


def test_lead_k_reads_the_issue_k_days_before_the_start():
    outlooks = {D(2025, 12, 15): [_e("F", D(2025, 12, 19))]}

    [row] = ev.match_events([REAL], outlooks, first_month=11, leads=(3,))

    assert row.issue_date == "2025-12-15" and row.matched


@pytest.mark.parametrize("start,end,hit", [
    (D(2025, 12, 17), D(2025, 12, 17), False),  # the day before
    (D(2025, 12, 17), D(2025, 12, 18), True),   # touches the start
    (D(2025, 12, 19), D(2025, 12, 22), True),   # touches the end
    (D(2025, 12, 20), D(2025, 12, 21), False),  # the day after
])
def test_overlap_is_closed_on_both_ends(start, end, hit):
    assert ev.overlaps(_e("F", start, end), REAL) is hit


def test_an_issue_without_a_run_is_not_eligible():
    assert ev.match_events([REAL], {}, first_month=11, leads=(3,)) == []


def test_a_run_with_no_event_is_eligible_and_unmatched():
    [row] = ev.match_events([REAL], {D(2025, 12, 15): []}, first_month=11, leads=(3,))

    assert row.matched is False and row.forecast_cm is None


def test_every_overlapping_forecast_event_is_summed():
    outlooks = {D(2025, 12, 15): [_e("F1", D(2025, 12, 18), cm=3.0),
                                  _e("F2", D(2025, 12, 19), cm=4.0),
                                  _e("F3", D(2025, 12, 21), cm=9.0)]}

    [row] = ev.match_events([REAL], outlooks, first_month=11, leads=(3,))

    assert row.forecast_cm == 7.0 and row.error_cm == -3.0


def test_the_hit_rate_is_pooled_from_counts_across_seasons():
    rows = [
        ev.Match("A", "2024-2025", 3, "x", True, 1.0, 1.0),
        ev.Match("B", "2025-2026", 3, "x", False, None, 1.0),
        ev.Match("C", "2025-2026", 3, "x", False, None, 1.0),
        ev.Match("D", "2025-2026", 3, "x", False, None, 1.0),
    ]

    pooled = ev.summarise(rows)

    assert pooled[3]["hit_rate"] == 0.25  # not the mean of 100% and 0%


@pytest.mark.parametrize("matched,status", [(5, "pass"), (4, "fail")])
def test_the_registered_criterion_is_lead_3_at_half(matched, status):
    rows = [ev.Match(str(i), "s", 3, "x", i < matched, 1.0 if i < matched else None, 1.0) for i in range(10)]

    assert ev.verdict(ev.summarise(rows))["status"] == status
    assert (ev.CRITERION_LEAD, ev.CRITERION_HIT_RATE) == (3, 0.50)


def test_no_lead_3_cell_is_not_a_pass():
    assert ev.verdict({})["status"] == "no_data"


def test_an_outlook_csv_yields_one_event_per_id():
    text = (
        "issue_date,outlook_event_id,event_start,event_end,plow_zone,total_snowfall_cm\n"
        "2025-12-15,OUTLOOK-2025-12-15-2025-12-18,2025-12-18,2025-12-19,A,7.5\n"
        "2025-12-15,OUTLOOK-2025-12-15-2025-12-18,2025-12-18,2025-12-19,B,7.5\n"
    )

    assert ev.outlook_events(text) == [
        ev.Event("OUTLOOK-2025-12-15-2025-12-18", D(2025, 12, 18), D(2025, 12, 19), 7.5)]


def test_an_empty_outlook_csv_has_no_events():
    assert ev.outlook_events("issue_date,outlook_event_id,event_start,event_end\n") == []
