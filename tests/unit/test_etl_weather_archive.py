"""Unit tests for spark/jobs/etl_weather_archive.py's path construction.

Pure string/date arithmetic — no SparkSession is started here.
"""

from __future__ import annotations

from datetime import date

from spark.jobs.etl_weather_archive import _bronze_month_prefixes

BUCKET = "uoip"
BASE = f"s3a://{BUCKET}/bronze/raw/SRC-Open-Meteo/weather_archive"


def test_returns_one_prefix_per_month_not_per_day():
    """The whole point of the month layout: a 366-day window costs 12 paths,
    not 366. Spark calls exists() on each one before reading anything.
    """
    prefixes = _bronze_month_prefixes(BUCKET, date(2024, 1, 1), date(2025, 1, 1))

    assert len(prefixes) == 12
    assert prefixes[0] == f"{BASE}/2024-01/"
    assert prefixes[-1] == f"{BASE}/2024-12/"


def test_includes_the_month_containing_a_mid_month_start():
    """`start` is not month-aligned, and the days before it in that same folder
    are trimmed by the window filter in run(), not by dropping the folder.
    """
    prefixes = _bronze_month_prefixes(BUCKET, date(2024, 3, 17), date(2024, 4, 2))

    assert prefixes == [f"{BASE}/2024-03/", f"{BASE}/2024-04/"]


def test_a_window_inside_one_month_yields_one_prefix():
    prefixes = _bronze_month_prefixes(BUCKET, date(2024, 3, 2), date(2024, 3, 9))

    assert prefixes == [f"{BASE}/2024-03/"]


def test_end_is_exclusive_at_a_month_boundary():
    """[Mar 1, Apr 1) must not reach into April — end is exclusive, and pulling
    an extra folder would fail the run whenever that month has no Bronze yet.
    """
    prefixes = _bronze_month_prefixes(BUCKET, date(2024, 3, 1), date(2024, 4, 1))

    assert prefixes == [f"{BASE}/2024-03/"]


def test_rolls_over_february_in_a_leap_year():
    """The month step goes via day 28 + 4 days, which has to land in March for
    a 29-day February as well as a 28-day one.
    """
    assert _bronze_month_prefixes(BUCKET, date(2024, 2, 1), date(2024, 3, 2)) == [
        f"{BASE}/2024-02/",
        f"{BASE}/2024-03/",
    ]
    assert _bronze_month_prefixes(BUCKET, date(2023, 2, 1), date(2023, 3, 2)) == [
        f"{BASE}/2023-02/",
        f"{BASE}/2023-03/",
    ]


def test_spans_a_year_boundary():
    prefixes = _bronze_month_prefixes(BUCKET, date(2023, 12, 15), date(2024, 1, 16))

    assert prefixes == [f"{BASE}/2023-12/", f"{BASE}/2024-01/"]


def test_an_eighteen_year_backfill_stays_in_the_low_hundreds():
    """The regression this replaced: one path per day issued ~6,800 HEAD
    requests up front, any one of which could abort the job.
    """
    prefixes = _bronze_month_prefixes(BUCKET, date(2008, 1, 1), date(2026, 9, 1))

    assert len(prefixes) == 224
    assert len(set(prefixes)) == len(prefixes)
