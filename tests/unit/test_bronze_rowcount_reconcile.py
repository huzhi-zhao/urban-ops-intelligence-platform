"""Unit tests for the Bronze↔upstream row-count reconciliation probe.

Only the verdict/summary logic is covered — the S3 walk and the Socrata call
are I/O and belong to a live run, not to the offline suite.
"""

from __future__ import annotations

from scripts.profiling.bronze_rowcount_reconcile import DayReconciliation, summarize


def _rec(day: str, bronze: int, upstream: int, recent: bool = False) -> DayReconciliation:
    return DayReconciliation(
        day=day,
        bronze_rows=bronze,
        upstream_rows=upstream,
        delta=bronze - upstream,
        within_late_arrival_window=recent,
    )


def test_equal_counts_are_clean():
    assert _rec("2019-06-07", 3585, 3585).verdict == "clean"


def test_more_rows_locally_is_excess():
    """Bronze > upstream means the same row was fetched twice."""
    assert _rec("2019-06-07", 3585, 3584).verdict == "excess"


def test_fewer_rows_locally_means_rows_were_dropped():
    """The case no local check can see: the shard is simply short."""
    assert _rec("2019-06-07", 3584, 3585).verdict == "MISSING_ROWS"


def test_recent_days_tolerate_a_mismatch_but_old_days_do_not():
    """Late-arriving 311 facts grow recent days; a 2019 shard has no such excuse.

    Without this split the check would fire every day and get muted.
    """
    assert _rec("2026-08-17", 100, 105, recent=True).verdict == "late_arrival_tolerated"
    assert _rec("2019-06-07", 100, 105, recent=False).verdict == "MISSING_ROWS"


def test_summary_counts_both_directions_and_builds_the_refetch_list():
    summary = summarize([
        _rec("2019-06-07", 3585, 3584),   # excess
        _rec("2020-06-25", 2732, 2730),   # excess
        _rec("2021-01-07", 1000, 1002),   # missing
        _rec("2022-02-02", 500, 500),     # clean
        _rec("2026-08-17", 10, 40, recent=True),  # tolerated
    ])

    assert summary["days_checked"] == 5
    assert summary["days_clean"] == 1
    assert summary["days_with_excess_rows"] == 2
    assert summary["days_with_missing_rows"] == 1
    assert summary["rows_excess_total"] == 3
    assert summary["rows_missing_total"] == 2
    assert summary["days_tolerated_as_late_arrival"] == 1
    # Both directions need re-pulling; the tolerated day does not.
    assert summary["refetch_days"] == ["2019-06-07", "2020-06-25", "2021-01-07"]
