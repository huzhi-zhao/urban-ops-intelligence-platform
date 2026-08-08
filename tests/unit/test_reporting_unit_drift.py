"""Unit tests for the BO-1 reporting-unit drift probe's pure functions.

The probe's conclusion is "ward is stable, neighbourhood needs a casefold", and
both halves of that come out of :func:`drift`. What is tested here is the part
that decides it: the activity threshold that keeps a straggler from a retired
label reading as "still in use", the case-variant grouping that separates a
respelling from a genuine rename, and the dedup arithmetic that distinguishes
the composite key from ``case_id`` alone.
"""

from __future__ import annotations

from scripts.analysis.reporting_unit_drift import dedup, drift

# ── drift ────────────────────────────────────────────────────────────────────


def test_an_unchanging_label_set_is_stable() -> None:
    report = drift({2020: {"A": 10, "B": 10}, 2021: {"A": 20, "B": 5}}, 0.0)
    assert report["stable"]
    assert report["changes"] == []
    assert report["labels_present_every_year"] == 2


def test_a_genuine_addition_is_reported_against_the_year_it_appears() -> None:
    report = drift({2020: {"A": 10}, 2021: {"A": 10, "B": 10}}, 0.0)
    assert report["changes"] == [{"year": 2021, "added": ["B"], "removed": []}]
    assert not report["stable"]


def test_a_label_below_the_activity_threshold_does_not_count_as_present() -> None:
    """One straggler row is not evidence the label is still in use.

    Without the threshold a retired boundary that leaks a single row keeps
    reading as active for the rest of the record, and the year it was actually
    retired never appears as a change.
    """
    per_year = {2020: {"A": 1000, "B": 1000}, 2021: {"A": 2000, "B": 1}}
    assert drift(per_year, 0.001)["changes"] == [
        {"year": 2021, "added": [], "removed": ["B"]},
    ]
    # With no threshold the same data reads as perfectly stable.
    assert drift(per_year, 0.0)["stable"]


def test_case_variants_are_grouped_and_counted_together() -> None:
    """A respelling is a different finding from a rename, and a cheaper fix."""
    report = drift({2022: {"Mcmillan": 30}, 2023: {"McMillan": 10}}, 0.0)
    assert report["case_variants"] == {"mcmillan": {"Mcmillan": 30, "McMillan": 10}}
    assert report["labels_all_time"] == 2
    assert report["labels_all_time_casefolded"] == 1


def test_a_single_spelling_is_not_reported_as_a_variant() -> None:
    report = drift({2020: {"Westwood": 5}, 2021: {"Westwood": 7}}, 0.0)
    assert report["case_variants"] == {}
    assert report["labels_all_time_casefolded"] == 1


# ── dedup ────────────────────────────────────────────────────────────────────


def key_rows(*pairs: tuple[str, str]) -> list[dict]:
    return [{"case_id": c, "interaction_id": i} for c, i in pairs]


def test_repeated_case_ids_are_distinct_rows_under_the_composite_key() -> None:
    """The row grain is an interaction. One case may carry several.

    This is the number the whole probe exists to pin down: deduplicating on
    ``case_id`` alone would delete genuine interactions, and the report has to
    say how many.
    """
    report = dedup(key_rows(("c1", "i1"), ("c1", "i2"), ("c2", "i3")))
    assert report["distinct_composite_key"] == 3
    assert report["duplicate_rows_on_composite_key"] == 0
    assert report["distinct_case_id"] == 2
    assert report["rows_lost_if_deduplicated_on_case_id"] == 1


def test_a_fully_repeated_row_is_a_real_duplicate() -> None:
    report = dedup(key_rows(("c1", "i1"), ("c1", "i1")))
    assert report["duplicate_rows_on_composite_key"] == 1


def test_an_empty_input_reports_no_share_rather_than_zero() -> None:
    """A missing number and a measured zero mean different things."""
    assert dedup([])["case_id_duplicate_share"] is None
