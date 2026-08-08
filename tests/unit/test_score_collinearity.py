"""Unit tests for the BO-6 collinearity probe's pure functions.

The probe's conclusion is a set of correlation coefficients, and a coefficient
is only as trustworthy as the factor construction underneath it. What is tested
here is that construction: normalisation, the severity composite's direction,
the NULL discipline on a missing rank, and the variance split that decides
whether a factor can reorder anything at all.
"""

from __future__ import annotations

from datetime import date

from scripts.analysis.score_collinearity import (
    Cell,
    build_cells,
    normalise,
    rank_factor,
    rank_influence,
    severity,
    variance_decomposition,
)
from scripts.analysis.snowfall_events import Event


def cell(event: int, zone: str, requests: int = 0, snow: float = 0.0,
         cold: float = 0.0, rank: float | None = None) -> Cell:
    return Cell(
        event=event, zone=zone, start=date(2020, 1, 1), requests=requests,
        snow_cm=snow, cold_c=cold, rank=rank,
    )


# ── normalisation ────────────────────────────────────────────────────────────


def test_normalise_maps_the_range_onto_zero_to_one() -> None:
    assert normalise([2.0, 4.0, 6.0]) == [0.0, 0.5, 1.0]


def test_a_constant_factor_normalises_to_zero_not_to_a_midpoint() -> None:
    # A constant factor contributes nothing to any ranking. Mapping it to 0.5
    # would let it add a fixed offset to every score and look like it was doing
    # something — the same misreading that made a zero-variance shift_end look
    # like a completion time.
    assert normalise([7.0, 7.0, 7.0]) == [0.0, 0.0, 0.0]


# ── severity ─────────────────────────────────────────────────────────────────


def test_colder_minimum_temperature_raises_severity() -> None:
    cells = [cell(0, "A", cold=-5.0), cell(0, "B", cold=-30.0)]
    # Snow is constant here, so the composite is the cold half alone.
    values = severity(cells, snow_weight=0.0)
    assert values[1] > values[0]


def test_temperatures_above_freezing_do_not_go_negative() -> None:
    # Clamping at zero matters: without it a mild day would subtract from
    # severity and a warm event could score below an event with no weather at
    # all.
    cells = [cell(0, "A", cold=5.0), cell(0, "B", cold=0.0), cell(0, "C", cold=-10.0)]
    values = severity(cells, snow_weight=0.0)
    assert values[0] == values[1] == 0.0
    assert values[2] > 0.0


def test_snow_weight_selects_which_half_of_the_composite_is_read() -> None:
    cells = [cell(0, "A", snow=0.0, cold=-30.0), cell(0, "B", snow=40.0, cold=0.0)]
    assert severity(cells, snow_weight=1.0) == [0.0, 1.0]
    assert severity(cells, snow_weight=0.0) == [1.0, 0.0]


# ── rank factor ──────────────────────────────────────────────────────────────


def test_missing_rank_stays_none_and_does_not_become_zero() -> None:
    # BO-6 is explicit that an event with no plow operation has a NULL supply
    # factor. Filling it with 0 would read "no operation" as "plowed first",
    # which is exactly the inference ADR 0008 retired the gap factor over.
    values = rank_factor([cell(0, "A", rank=1.0), cell(0, "B"), cell(0, "C", rank=5.0)])
    assert values == [0.0, None, 1.0]


def test_rank_factor_is_all_none_when_no_event_was_ever_plowed() -> None:
    assert rank_factor([cell(0, "A"), cell(0, "B")]) == [None, None]


# ── variance decomposition ───────────────────────────────────────────────────


def test_a_factor_constant_within_each_event_has_no_within_event_variance() -> None:
    # This is the weather factor's actual shape: one value per event, shared by
    # every zone. It can shift the score level but cannot reorder zones inside
    # an event, so no weight makes it affect a recommendation.
    cells = [cell(0, "A"), cell(0, "B"), cell(1, "A"), cell(1, "B")]
    result = variance_decomposition(cells, [1.0, 1.0, 5.0, 5.0])
    assert result["within_event"] == 0.0
    assert result["within_event_share"] == 0.0


def test_a_factor_that_only_varies_between_zones_is_entirely_within_event() -> None:
    cells = [cell(0, "A"), cell(0, "B"), cell(1, "A"), cell(1, "B")]
    result = variance_decomposition(cells, [1.0, 5.0, 1.0, 5.0])
    assert result["between_event"] == 0.0
    assert result["within_event_share"] == 1.0


# ── rank influence ───────────────────────────────────────────────────────────


def test_rank_reorders_zones_when_it_disagrees_with_the_other_factors() -> None:
    # Without rank, A leads B by 0.40 x 0.2 = 0.08. Scheduling B last adds 0.30
    # to it, which is more than enough to overturn that lead — so the 0.30
    # weight is buying a genuinely different recommendation, not a tie-break.
    cells = [cell(0, "A"), cell(0, "B")]
    factors = {"requests": [1.0, 0.8], "weather": [0.0, 0.0], "rank": [0.0, 1.0]}
    result = rank_influence(cells, factors, {"requests": 0.40, "weather": 0.30, "rank": 0.30})
    assert result["events_where_order_changes"] == 1


def test_rank_agreeing_with_the_other_factors_leaves_the_order_alone() -> None:
    cells = [cell(0, "A"), cell(0, "B")]
    factors = {"requests": [0.0, 1.0], "weather": [0.0, 0.0], "rank": [0.0, 1.0]}
    result = rank_influence(cells, factors, {"requests": 0.40, "weather": 0.30, "rank": 0.30})
    assert result["events_where_order_changes"] == 0


# ── cell construction ────────────────────────────────────────────────────────


def event(start: int, end: int) -> Event:
    return Event(
        start=date(2020, 1, start), end=date(2020, 1, end),
        days=end - start + 1, total_cm=10.0, peak_cm=10.0,
    )


def weather_series(*days: int) -> dict[str, dict[date, tuple[float, float]]]:
    return {"A": {date(2020, 1, d): (2.0, -10.0) for d in days}}


def test_a_plow_operation_within_the_lag_is_attached_to_the_event() -> None:
    cells = build_cells(
        [event(1, 2)], {}, weather_series(1, 2),
        {date(2020, 1, 5): {"A": 3.0}}, ["A"], lag_days=7,
    )
    assert cells[0].rank == 3.0


def test_a_plow_operation_beyond_the_lag_leaves_the_rank_unset() -> None:
    # Plowing three weeks after the snow stopped is a different operation, and
    # attaching it would manufacture a supply signal for an event that had none.
    cells = build_cells(
        [event(1, 2)], {}, weather_series(1, 2),
        {date(2020, 1, 25): {"A": 3.0}}, ["A"], lag_days=7,
    )
    assert cells[0].rank is None


def test_requests_are_summed_over_every_day_of_the_event() -> None:
    counts = {(date(2020, 1, 1), "A"): 4, (date(2020, 1, 2), "A"): 6}
    cells = build_cells([event(1, 2)], counts, weather_series(1, 2), {}, ["A"], lag_days=7)
    assert cells[0].requests == 10


def test_event_cold_is_the_coldest_day_not_the_average() -> None:
    series = {"A": {date(2020, 1, 1): (2.0, -5.0), date(2020, 1, 2): (2.0, -25.0)}}
    cells = build_cells([event(1, 2)], {}, series, {}, ["A"], lag_days=7)
    assert cells[0].cold_c == -25.0
