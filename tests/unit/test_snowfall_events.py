"""Unit tests for the BO-3 snowfall-event probe's pure functions.

The probe's value rests entirely on what "an event" means, so the segmentation
rule is tested directly rather than inferred from a live run — a live run
cannot distinguish a correct N from a plausible one.
"""

from __future__ import annotations

from datetime import date

import pytest

from scripts.analysis.snowfall_events import (
    Event,
    alignment,
    gaps_between,
    segment_events,
    summarise,
    ward_panel,
    winter_type_filter,
)


def series(*pairs: tuple[int, float]) -> dict[date, float]:
    """Daily snowfall keyed by day-of-January, for readability."""
    return {date(2020, 1, day): cm for day, cm in pairs}


def test_consecutive_above_threshold_days_are_one_event() -> None:
    events = segment_events(series((1, 6.0), (2, 7.0), (3, 8.0)), 5.0, max_gap_days=0)
    assert [(e.start, e.end, e.days) for e in events] == [
        (date(2020, 1, 1), date(2020, 1, 3), 3),
    ]


def test_below_threshold_day_splits_when_no_gap_tolerated() -> None:
    events = segment_events(series((1, 6.0), (2, 0.0), (3, 6.0)), 5.0, max_gap_days=0)
    assert len(events) == 2


def test_one_quiet_day_is_bridged_when_tolerated() -> None:
    events = segment_events(series((1, 6.0), (2, 0.0), (3, 6.0)), 5.0, max_gap_days=1)
    assert len(events) == 1
    assert events[0].days == 3


def test_two_quiet_days_exceed_a_one_day_tolerance() -> None:
    events = segment_events(
        series((1, 6.0), (2, 0.0), (3, 0.0), (4, 6.0)), 5.0, max_gap_days=1,
    )
    assert len(events) == 2


def test_bridged_gap_day_does_not_add_to_the_event_total() -> None:
    # The gap day carries 4cm of snow but sits below the 5cm threshold. Letting
    # it into the total would make the threshold decide segmentation but not
    # magnitude, so an event's size would depend on sub-threshold noise.
    events = segment_events(series((1, 6.0), (2, 4.0), (3, 6.0)), 5.0, max_gap_days=1)
    assert events[0].total_cm == 12.0
    assert events[0].peak_cm == 6.0


def test_threshold_is_inclusive() -> None:
    assert len(segment_events(series((1, 5.0)), 5.0, max_gap_days=0)) == 1
    assert segment_events(series((1, 4.9)), 5.0, max_gap_days=0) == []


def test_raising_the_threshold_cannot_increase_the_event_count() -> None:
    daily = series((1, 4.0), (2, 9.0), (5, 6.0), (9, 12.0), (10, 3.0))
    counts = [len(segment_events(daily, t, max_gap_days=1)) for t in (3.0, 5.0, 8.0, 10.0)]
    assert counts == sorted(counts, reverse=True)


def test_gaps_between_drops_the_summer() -> None:
    events = [
        Event(date(2020, 1, 1), date(2020, 1, 2), 2, 10.0, 6.0),
        Event(date(2020, 1, 10), date(2020, 1, 10), 1, 6.0, 6.0),
        Event(date(2020, 12, 1), date(2020, 12, 1), 1, 6.0, 6.0),
    ]
    assert gaps_between(events) == [8]


def test_summarise_of_an_empty_series_reports_no_numbers() -> None:
    assert summarise([]) == {"n": 0, "min": None, "median": None, "mean": None, "max": None}


def test_summarise_median_of_an_even_count_averages_the_middle() -> None:
    assert summarise([1.0, 2.0, 3.0, 4.0])["median"] == 2.5


ONE_EVENT = [Event(date(2020, 1, 10), date(2020, 1, 12), 3, 18.0, 8.0)]


def test_alignment_accepts_a_mark_inside_the_event() -> None:
    result = alignment(ONE_EVENT, {"a": date(2020, 1, 11)}, lag_days=3)
    assert result["matched"] == 1
    assert result["match_rate"] == 1.0


def test_alignment_accepts_a_mark_within_the_lag_after_the_event() -> None:
    # Plowing follows the snow; requiring it strictly inside the snowfall run
    # would score a correctly-timed response as a miss.
    assert alignment(ONE_EVENT, {"a": date(2020, 1, 15)}, lag_days=3)["matched"] == 1


def test_alignment_rejects_a_mark_beyond_the_lag() -> None:
    result = alignment(ONE_EVENT, {"a": date(2020, 1, 16)}, lag_days=3)
    assert result["matched"] == 0
    assert result["unmatched"] == [{"id": "a", "date": "2020-01-16"}]


def test_alignment_rejects_a_mark_before_the_event() -> None:
    assert alignment(ONE_EVENT, {"a": date(2020, 1, 9)}, lag_days=3)["matched"] == 0


def test_ward_panel_counts_every_day_of_a_multi_day_event() -> None:
    counts = {
        (date(2020, 1, 10), "1"): 2,
        (date(2020, 1, 12), "1"): 3,
    }
    panel = ward_panel(ONE_EVENT, counts, ["1", "2"])
    assert panel["cells"] == 2
    assert panel["non_zero"] == 1
    assert panel["non_zero_rate"] == 0.5
    assert panel["requests_per_cell"]["max"] == 5


def test_ward_panel_without_events_reports_no_rate_rather_than_zero() -> None:
    # A missing rate and a rate of 0.0 mean different things; the acceptance
    # criterion is a threshold on the rate, so conflating them would let an
    # empty run read as a measured failure.
    assert ward_panel([], {}, ["1"])["non_zero_rate"] is None


def test_winter_filter_does_not_match_bare_ice() -> None:
    predicate = winter_type_filter()
    assert "'%ICE%'" not in predicate
    assert "'%ICE CONTROL%'" in predicate


@pytest.mark.parametrize("keyword", ["SNOW", "FROZEN", "PLOW", "PLOUGH", "SANDING", "WINDROW"])
def test_winter_filter_covers_every_measured_keyword(keyword: str) -> None:
    assert f"'%{keyword}%'" in winter_type_filter()
