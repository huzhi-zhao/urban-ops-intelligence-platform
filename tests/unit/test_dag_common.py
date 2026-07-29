"""Unit tests for _dag_common helpers (get_yesterday, get_last_month)."""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

# Put dags/ on path so _dag_common can be imported without airflow installed
DAGS_DIR = Path(__file__).parent.parent.parent / "dags"
if str(DAGS_DIR) not in sys.path:
    sys.path.insert(0, str(DAGS_DIR))

pytest.importorskip("airflow", reason="apache-airflow not installed; skipping DAG common tests")


def _ctx(dt_str: str) -> dict:
    """Build a minimal Airflow context dict with data_interval_start."""
    dt = datetime.fromisoformat(dt_str).replace(tzinfo=UTC)
    return {"data_interval_start": dt}


# ── get_yesterday ─────────────────────────────────────────────────────────────

def test_get_yesterday_normal_day():
    from _dag_common import get_yesterday
    # data_interval_start = Jun 16 06:00 → DAG processes Jun 16 data (ran on Jun 17)
    ctx = _ctx("2026-06-16T06:00:00")
    assert get_yesterday(ctx) == date(2026, 6, 16)


def test_get_yesterday_month_boundary():
    from _dag_common import get_yesterday
    # data_interval_start = May 31 06:00 → DAG processes May 31 data
    ctx = _ctx("2026-05-31T06:00:00")
    assert get_yesterday(ctx) == date(2026, 5, 31)


def test_get_yesterday_year_boundary():
    from _dag_common import get_yesterday
    # data_interval_start = Dec 31 06:00 → DAG processes Dec 31 data
    ctx = _ctx("2025-12-31T06:00:00")
    assert get_yesterday(ctx) == date(2025, 12, 31)


# ── get_last_month ────────────────────────────────────────────────────────────

def test_get_last_month_normal():
    from _dag_common import get_last_month
    ctx = _ctx("2026-06-01T06:00:00")
    start, end = get_last_month(ctx)
    assert start == date(2026, 6, 1)
    assert end == date(2026, 7, 1)


def test_get_last_month_december():
    from _dag_common import get_last_month
    # Run on 2026-12-01 → last month = December 2026
    ctx = _ctx("2026-12-01T06:00:00")
    start, end = get_last_month(ctx)
    assert start == date(2026, 12, 1)
    assert end == date(2027, 1, 1)


def test_get_last_month_january():
    from _dag_common import get_last_month
    # Run on 2026-01-01 → last month = January 2026
    ctx = _ctx("2026-01-01T06:00:00")
    start, end = get_last_month(ctx)
    assert start == date(2026, 1, 1)
    assert end == date(2026, 2, 1)


def test_get_last_month_window_is_one_month():
    """end - start should always be exactly one calendar month."""
    from _dag_common import get_last_month
    for month in range(1, 13):
        ctx = _ctx(f"2026-{month:02d}-01T06:00:00")
        start, end = get_last_month(ctx)
        assert start.day == 1
        assert end.day == 1
        assert (end.year * 12 + end.month) - (start.year * 12 + start.month) == 1
