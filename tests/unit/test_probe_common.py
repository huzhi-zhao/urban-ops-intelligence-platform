"""Unit tests for the shared metric-probe helpers.

Pure functions only — no network. The probes themselves hit live APIs by
design and are therefore not unit-tested; what is testable is the arithmetic
and the window slicing they all depend on.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from scripts.analysis._probe_common import _cache_path, iso_days, spearman, winters

POINT = {"latitude": 49.895, "longitude": -97.138}


@pytest.mark.parametrize(
    ("xs", "ys", "expected"),
    [
        ([1.0, 2.0, 3.0], [10.0, 20.0, 30.0], 1.0),
        ([1.0, 2.0, 3.0], [30.0, 20.0, 10.0], -1.0),
        # Monotone but wildly non-linear: Pearson would not give 1.0 here.
        ([1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 4.0, 1000.0], 1.0),
    ],
)
def test_spearman_measures_order_not_magnitude(
    xs: list[float], ys: list[float], expected: float,
) -> None:
    assert spearman(xs, ys) == pytest.approx(expected)


def test_spearman_averages_tied_ranks() -> None:
    # Ties must share the mean rank; assigning them arbitrary distinct ranks
    # would invent an ordering the data does not contain.
    assert spearman([1.0, 1.0, 2.0], [5.0, 5.0, 9.0]) == pytest.approx(1.0)


def test_winters_run_november_to_may_exclusive() -> None:
    seasons = winters(2015, 2017)
    assert seasons == [
        (date(2015, 11, 1), date(2016, 5, 1)),
        (date(2016, 11, 1), date(2017, 5, 1)),
        (date(2017, 11, 1), date(2018, 5, 1)),
    ]


def test_winter_window_spans_the_new_year() -> None:
    # The point of a season window: a mid-winter event is not cut in two by a
    # calendar-year boundary.
    start, end = winters(2015, 2015)[0]
    assert start < date(2016, 1, 15) < end


def test_settled_window_is_cached() -> None:
    path = _cache_path(POINT, date(2015, 11, 1), date(2016, 5, 1))
    assert path is not None
    assert "2015-11-01_2016-05-01" in path.name


def test_open_window_is_not_cached() -> None:
    # The archive backfills with a lag. Caching the current season would freeze
    # a partial winter into every later run and shrink its event count.
    today = date.today()
    assert _cache_path(POINT, date(today.year, 11, 1), today + timedelta(days=90)) is None


def test_a_window_ending_days_ago_is_still_treated_as_open() -> None:
    today = date.today()
    assert _cache_path(POINT, today - timedelta(days=30), today - timedelta(days=1)) is None


def test_cache_key_separates_two_points() -> None:
    # The BO-2 counterfactual queries one point per zone over identical windows;
    # a key that ignored the coordinates would serve every zone the first one.
    window = (date(2015, 11, 1), date(2016, 5, 1))
    a = _cache_path(POINT, *window)
    b = _cache_path({"latitude": 49.9, "longitude": -97.2}, *window)
    assert a != b


def test_iso_days_is_half_open() -> None:
    assert iso_days(date(2026, 2, 27), date(2026, 3, 2)) == [
        "2026-02-27", "2026-02-28", "2026-03-01",
    ]
