"""Unit tests for the L2 DQ baseline collector (offline — no Trino)."""

from __future__ import annotations

import pytest

from scripts.gold import dq_baseline
from scripts.gold.build_gold import TABLES


def _report(name: str, rows: int, nulls: dict[str, int]) -> dq_baseline.TableReport:
    r = dq_baseline.TableReport(name, "dims")
    r.rows = rows
    r.nulls = nulls
    return r


def test_a_zero_row_table_reports_no_all_null_columns() -> None:
    """Every column of an empty table is trivially all-NULL; saying so is noise.

    The empty table itself is what matters, and main() reports that separately.
    """
    r = _report("t", 0, {"a": 0, "b": 0})
    assert r.all_null == []
    assert r.partial_null == []


def test_rates_are_computed_from_counts_not_averaged() -> None:
    r = _report("t", 400, {"a": 100})
    assert r.rate(100) == pytest.approx(25.0)


def test_render_separates_partial_and_fully_null_columns() -> None:
    r = _report("fact_parking_ban", 49, {"ban_id": 0, "plow_zone": 30, "note": 49})
    out = dq_baseline.render([r])
    assert "| `fact_parking_ban` | dims | 49 | 3 | 1 | 1 |" in out
    assert "| `fact_parking_ban` | `plow_zone` | 30 | 61.22% |" in out
    assert "🔴 全空" in out


def test_render_says_so_when_nothing_is_null() -> None:
    out = dq_baseline.render([_report("dim_channel", 15, {"channel_id": 0})])
    assert "所有列空值率均为 0%" in out


def test_only_matches_the_same_stages_the_builder_uses() -> None:
    stages = {t.stage for t in TABLES}
    assert stages == {"seeds", "dims", "facts"}
