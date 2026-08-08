"""Unit tests for the BO-3 follow-up probe's hypothesis tests.

Each function here encodes a claim about *why* a crew went out. The tests pin
the conditions that make each claim measurable — in particular the temperature
pairing, without which "it was windy" and "it rained" would both fire on
ordinary spring weather and explain nothing.
"""

from __future__ import annotations

from datetime import date

from scripts.analysis.plow_without_snowfall import (
    Operation,
    accumulation_signature,
    contrast,
    drift_evidence,
    freezing_rain_evidence,
    number,
    snowpack,
    verdicts,
)


def day(
    snow: float = 0.0, rain: float = 0.0, wind: float = 0.0,
    gust: float = 0.0, tmax: float = -10.0,
) -> dict:
    return {
        "snowfall_sum": snow, "rain_sum": rain, "windspeed_10m_max": wind,
        "windgusts_10m_max": gust, "temperature_2m_max": tmax,
    }


# ── field access ─────────────────────────────────────────────────────────────


def test_a_missing_measurement_reads_as_zero_not_as_a_crash() -> None:
    # Open-Meteo omits a variable for days it has no value for. A probe that
    # raised there would report nothing at all for an operation rather than
    # reporting the fields it does have.
    assert number({}, "windspeed_10m_max") == 0.0
    assert number({"rain_sum": None}, "rain_sum") == 0.0


# ── drift ────────────────────────────────────────────────────────────────────


def test_wind_over_a_thawed_surface_is_not_a_drift_day() -> None:
    # This is the condition that keeps the drift hypothesis falsifiable. A gale
    # in April moves no snow, and counting it would make every operation look
    # drift-driven.
    _, _, days = drift_evidence([day(wind=60.0, tmax=8.0)])
    assert days == 0


def test_strong_wind_below_freezing_counts_as_a_drift_day() -> None:
    _, _, days = drift_evidence([day(wind=45.0, tmax=-12.0)])
    assert days == 1


def test_peak_wind_is_reported_even_on_days_that_do_not_count() -> None:
    # The peak is descriptive, the day count is the test. Suppressing the peak
    # for warm days would hide the evidence that the wind was there at all.
    wind, gust, days = drift_evidence([day(wind=70.0, gust=95.0, tmax=10.0)])
    assert (wind, gust, days) == (70.0, 95.0, 0)


# ── freezing rain ────────────────────────────────────────────────────────────


def test_rain_above_freezing_is_just_rain() -> None:
    total, days = freezing_rain_evidence([day(rain=12.0, tmax=6.0)])
    assert (total, days) == (0.0, 0)


def test_rain_onto_a_sub_zero_surface_is_counted_as_icing() -> None:
    total, days = freezing_rain_evidence([day(rain=4.0, tmax=-2.0)])
    assert (total, days) == (4.0, 1)


def test_a_trace_of_rain_does_not_count_as_an_icing_event() -> None:
    total, days = freezing_rain_evidence([day(rain=0.1, tmax=-2.0)])
    assert (total, days) == (0.0, 0)


# ── snowpack ─────────────────────────────────────────────────────────────────


def test_snowpack_accumulates_the_whole_window() -> None:
    assert snowpack([day(snow=4.0), day(snow=2.5), day()]) == 6.5


# ── contrast ─────────────────────────────────────────────────────────────────


def operation(started: date, aligned: bool, wind: float) -> Operation:
    return Operation(
        ban_id=started.isoformat(), started=started, aligned=aligned,
        snow_14d_cm=0.0, snow_max_day_cm=0.0, snow_pack_21d_cm=0.0,
        wind_max_kmh=wind, gust_max_kmh=wind, drift_days=0,
        freezing_rain_mm=0.0, freezing_rain_days=0,
        snow_any_zone_14d_cm=0.0, snow_any_zone_max_day_cm=0.0, best_zone=None,
    )


def test_contrast_reports_both_groups_so_a_number_can_be_compared() -> None:
    # A single number for the four unaligned operations means nothing on its
    # own; the probe's conclusion rests entirely on the aligned column being
    # there to compare against.
    rows = [
        operation(date(2019, 2, 10), aligned=False, wind=55.0),
        operation(date(2020, 1, 5), aligned=True, wind=20.0),
        operation(date(2020, 2, 5), aligned=True, wind=24.0),
    ]
    result = contrast(rows)["wind_max_kmh"]
    assert result["unaligned"]["median"] == 55.0
    assert result["aligned"]["median"] == 22.0


def comparison(**medians: tuple[float, float]) -> dict:
    """A contrast table with the given (unaligned, aligned) medians."""
    return {
        field: {"unaligned": {"median": low}, "aligned": {"median": high}}
        for field, (low, high) in medians.items()
    }


def test_a_hypothesis_is_refuted_when_the_unaligned_group_has_less_of_it() -> None:
    table = comparison(
        drift_days=(0.5, 2.0), freezing_rain_mm=(0.0, 0.0),
        snow_any_zone_max_day_cm=(2.0, 7.0),
    )
    assert all(not block["separates"] for block in verdicts(table).values())


def test_a_hypothesis_separates_only_when_the_unaligned_group_has_more() -> None:
    table = comparison(
        drift_days=(6.0, 2.0), freezing_rain_mm=(0.0, 0.0),
        snow_any_zone_max_day_cm=(2.0, 7.0),
    )
    assert verdicts(table)["drift"]["separates"] is True


# ── accumulation signature ───────────────────────────────────────────────────


def test_accumulation_holds_when_the_total_survives_but_the_peak_does_not() -> None:
    # The signature of snow that arrived without a peak day: a daily-threshold
    # event definition cannot see it, and no choice of threshold makes it
    # visible — which is why this is a finding about BO-3's definition rather
    # than about the weather.
    table = comparison(snow_pack_21d_cm=(15.0, 20.0), snow_max_day_cm=(1.6, 6.4))
    result = accumulation_signature(table)
    assert result["holds"] is True
    assert result["total_over_peak"] == 3.0


def test_accumulation_is_refuted_when_total_and_peak_fall_together() -> None:
    # Both down by the same factor means the unaligned operations simply had
    # less snow — an ordinary shortfall, not a definition problem.
    table = comparison(snow_pack_21d_cm=(10.0, 20.0), snow_max_day_cm=(3.2, 6.4))
    assert accumulation_signature(table)["holds"] is False


def test_contrast_reports_none_rather_than_zero_for_an_empty_group() -> None:
    # If every operation aligned, the unaligned column has no median. Reporting
    # 0 there would read as "the unaligned ones were calm" — a claim about data
    # that does not exist.
    rows = [operation(date(2020, 1, 5), aligned=True, wind=20.0)]
    assert contrast(rows)["wind_max_kmh"]["unaligned"]["median"] is None
