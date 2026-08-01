"""
Unit tests for ``scripts.backfill.bulk``.

The primary contract is the **slicing**: bulk takes a ``[start, end)``
window and cuts it into N documents (days for daily sources, months for
monthly sources, one piece for static), then dispatches each slice to
the atomic ``BackfillFacade`` method.

Multi-threading is a secondary knob — the tests cover the ``max_workers=1``
serial path and a smoke check of ``max_workers>1`` (without asserting
strict ordering, since ``as_completed`` returns in completion order).
"""

from __future__ import annotations

import sys
import time
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.backfill import bulk  # noqa: E402
from scripts.backfill.bulk import (  # noqa: E402
    BulkResult,
    _daterange,
    _monthrange,
    backfill_daily_window,
    backfill_monthly_window,
    backfill_static,
    fetch_daily_window,
    fetch_static,
)

# ── _daterange slicing ────────────────────────────────────────────────────────


def test_daterange_one_day():
    """[D, D+1) → exactly one day."""
    assert _daterange(date(2026, 6, 13), date(2026, 6, 14)) == [date(2026, 6, 13)]


def test_daterange_one_week():
    days = _daterange(date(2026, 6, 8), date(2026, 6, 15))
    assert days == [
        date(2026, 6, 8), date(2026, 6, 9), date(2026, 6, 10),
        date(2026, 6, 11), date(2026, 6, 12), date(2026, 6, 13),
        date(2026, 6, 14),
    ]
    assert len(days) == 7


def test_daterange_crosses_month_boundary():
    days = _daterange(date(2026, 4, 28), date(2026, 5, 2))
    assert days == [
        date(2026, 4, 28), date(2026, 4, 29), date(2026, 4, 30),
        date(2026, 5, 1),
    ]


def test_daterange_crosses_year_boundary():
    days = _daterange(date(2025, 12, 30), date(2026, 1, 2))
    assert days == [
        date(2025, 12, 30), date(2025, 12, 31),
        date(2026, 1, 1),
    ]


def test_daterange_leap_year_february():
    """2028 is a leap year; Feb has 29 days."""
    days = _daterange(date(2028, 2, 27), date(2028, 3, 1))
    assert days == [date(2028, 2, 27), date(2028, 2, 28), date(2028, 2, 29)]


def test_daterange_empty_when_start_equals_end():
    assert _daterange(date(2026, 6, 1), date(2026, 6, 1)) == []


def test_daterange_empty_when_end_before_start():
    assert _daterange(date(2026, 6, 5), date(2026, 6, 1)) == []


# ── _monthrange slicing ───────────────────────────────────────────────────────


def test_monthrange_one_month():
    months = _monthrange(date(2026, 6, 1), date(2026, 7, 1))
    assert months == [date(2026, 6, 1)]


def test_monthrange_normalizes_to_first_of_month():
    """start=Mar 15, end=May 5 → 3 months (Mar/Apr/May 1st)."""
    months = _monthrange(date(2026, 3, 15), date(2026, 5, 5))
    assert months == [date(2026, 3, 1), date(2026, 4, 1), date(2026, 5, 1)]


def test_monthrange_crosses_year_boundary():
    months = _monthrange(date(2025, 11, 1), date(2026, 2, 1))
    assert months == [
        date(2025, 11, 1), date(2025, 12, 1),
        date(2026, 1, 1),
    ]


def test_monthrange_full_year():
    months = _monthrange(date(2026, 1, 1), date(2027, 1, 1))
    assert len(months) == 12
    assert months[0] == date(2026, 1, 1)
    assert months[-1] == date(2026, 12, 1)


def test_monthrange_empty_when_start_equals_end():
    assert _monthrange(date(2026, 6, 1), date(2026, 6, 1)) == []


def test_monthrange_empty_when_end_before_start():
    assert _monthrange(date(2026, 6, 1), date(2026, 5, 1)) == []


# ── backfill_daily_window: slicing + dispatch ────────────────────────────────


def test_backfill_daily_window_calls_upload_day_for_each_day():
    """5-day window → 5 calls to facade.upload_day, in order."""
    fake_facade = MagicMock()
    fake_facade.upload_day.side_effect = lambda day, dataset_name=None: [
        SimpleNamespace(record_count=10, filename=f"data_{day}.json", dataset_name="nyc_311")
    ]
    with patch.object(bulk, "BackfillFacade", return_value=fake_facade):
        results = backfill_daily_window(
            "SRC-NYC-311",
            start=date(2026, 6, 1),
            end=date(2026, 6, 6),  # 5 days
            bucket="bkt",
            max_workers=1,  # serial for deterministic order
        )

    assert len(results) == 5
    assert [r.document for r in results] == [
        date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3),
        date(2026, 6, 4), date(2026, 6, 5),
    ]
    for r in results:
        assert r.status == "ok"
        assert r.manifest_count == 1
        assert r.error is None
    assert fake_facade.upload_day.call_count == 5


def test_backfill_daily_window_empty_window_returns_empty():
    fake_facade = MagicMock()
    with patch.object(bulk, "BackfillFacade", return_value=fake_facade):
        results = backfill_daily_window(
            "SRC-NYC-311",
            start=date(2026, 6, 1), end=date(2026, 6, 1),
            bucket="bkt",
        )
    assert results == []
    fake_facade.upload_day.assert_not_called()


def test_backfill_daily_window_continues_after_partial_failure():
    """One day's facade call raises BackfillError; the rest still complete."""
    from ingestion.backfill import BackfillError

    fake_facade = MagicMock()
    def _upload_day(day, dataset_name=None):
        if day == date(2026, 6, 3):
            raise BackfillError("Socrata down", source_id="SRC-NYC-311",
                                dataset_name="nyc_311", phase="fetch")
        return [SimpleNamespace(record_count=1, filename=f"data_{day}.json", dataset_name="nyc_311")]

    fake_facade.upload_day.side_effect = _upload_day
    with patch.object(bulk, "BackfillFacade", return_value=fake_facade):
        results = backfill_daily_window(
            "SRC-NYC-311",
            start=date(2026, 6, 1), end=date(2026, 6, 5),
            bucket="bkt", max_workers=1,
        )

    assert len(results) == 4
    statuses = sorted(r.status for r in results)
    assert statuses == ["failed", "ok", "ok", "ok"]
    failed = next(r for r in results if r.status == "failed")
    assert failed.document == date(2026, 6, 3)
    assert "Socrata down" in failed.error


def test_backfill_daily_window_max_workers_1_runs_serially():
    """With max_workers=1, the work is performed sequentially."""
    fake_facade = MagicMock()
    fake_facade.upload_day.return_value = []
    with patch.object(bulk, "BackfillFacade", return_value=fake_facade):
        backfill_daily_window(
            "SRC-NYC-311", start=date(2026, 6, 1), end=date(2026, 6, 4),
            bucket="bkt", max_workers=1,
        )
    # In serial mode, call order = document order
    called_days = [c.args[0] for c in fake_facade.upload_day.call_args_list]
    assert called_days == [date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3)]


def test_backfill_daily_window_max_workers_gt_1_runs_concurrently():
    """With max_workers>1, all days still complete (parallelism doesn't
    drop work; we just don't assert order because as_completed returns
    in completion order)."""
    fake_facade = MagicMock()
    # Slow mock — if serial, total ≥ 0.3s; if parallel, total < 0.3s.
    def _slow(day, dataset_name=None):
        time.sleep(0.1)
        return [SimpleNamespace(record_count=1, filename=f"data_{day}.json", dataset_name="x")]

    fake_facade.upload_day.side_effect = _slow
    with patch.object(bulk, "BackfillFacade", return_value=fake_facade):
        t0 = time.monotonic()
        results = backfill_daily_window(
            "SRC-NYC-311", start=date(2026, 6, 1), end=date(2026, 6, 4),
            bucket="bkt", max_workers=4,
        )
        elapsed = time.monotonic() - t0

    assert len(results) == 3
    # Serial would be 3 * 0.1 = 0.3s; parallel with 4 workers should be ~0.1s.
    # Allow generous margin for CI scheduling.
    assert elapsed < 0.25, f"parallel backfill took {elapsed:.3f}s, expected <0.25s"


# ── backfill_monthly_window: slicing + dispatch ─────────────────────────────


def test_backfill_monthly_window_calls_upload_month_for_each_month():
    """3-month window → 3 calls to facade.upload_month."""
    fake_facade = MagicMock()
    fake_facade.upload_month.side_effect = lambda month, dataset_name=None: [
        SimpleNamespace(record_count=20, filename=f"data_{month}.json", dataset_name="ds")
    ]
    with patch.object(bulk, "BackfillFacade", return_value=fake_facade):
        results = backfill_monthly_window(
            "SRC-NYPD",
            start=date(2026, 3, 1), end=date(2026, 6, 1),  # 3 months
            bucket="bkt", max_workers=1,
        )

    assert [r.document for r in results] == [
        date(2026, 3, 1), date(2026, 4, 1), date(2026, 5, 1),
    ]
    assert fake_facade.upload_month.call_count == 3


def test_backfill_monthly_window_partial_failure_continues():
    from ingestion.backfill import BackfillError

    fake_facade = MagicMock()
    def _upload_month(month, dataset_name=None):
        if month == date(2026, 4, 1):
            raise BackfillError("rate limited", source_id="SRC-NYPD",
                                dataset_name="ds", phase="fetch")
        return [SimpleNamespace(record_count=1, filename=f"data_{month}.json", dataset_name="ds")]

    fake_facade.upload_month.side_effect = _upload_month
    with patch.object(bulk, "BackfillFacade", return_value=fake_facade):
        results = backfill_monthly_window(
            "SRC-NYPD", start=date(2026, 3, 1), end=date(2026, 6, 1),
            bucket="bkt", max_workers=1,
        )

    assert len(results) == 3
    failed = [r for r in results if r.status == "failed"]
    assert len(failed) == 1
    assert failed[0].document == date(2026, 4, 1)
    assert "rate limited" in failed[0].error


# ── backfill_static: one-shot ────────────────────────────────────────────────


def test_backfill_static_calls_upload_static_once():
    """Static has no time slicing — exactly one call to facade.upload_static."""
    fake_facade = MagicMock()
    fake_facade.upload_static.return_value = [
        SimpleNamespace(record_count=5, filename="data_static.ndjson.gz", dataset_name="borough_boundaries")
    ]
    with patch.object(bulk, "BackfillFacade", return_value=fake_facade):
        results = backfill_static("SRC-DCP", bucket="bkt")

    assert len(results) == 1
    assert results[0].status == "ok"
    assert results[0].document is None  # static has no document
    assert results[0].manifest_count == 1
    fake_facade.upload_static.assert_called_once()


# ── backfill_snapshot: one-shot, today only ──────────────────────────────────


def test_backfill_snapshot_calls_upload_snapshot_once():
    """No window to slice: the upstream only ever holds its current state."""
    fake_facade = MagicMock()
    fake_facade.upload_snapshot.return_value = [
        SimpleNamespace(
            record_count=237_867, filename="data.ndjson.gz",
            dataset_name="snow_clearing_status",
        ),
    ]
    with patch.object(bulk, "BackfillFacade", return_value=fake_facade):
        results = bulk.backfill_snapshot("SRC-WPG-SNOW", bucket="uoip")

    assert len(results) == 1
    assert results[0].status == "ok"
    assert results[0].document == date.today()
    assert results[0].manifest_count == 1
    fake_facade.upload_snapshot.assert_called_once()


def test_backfill_snapshot_defaults_the_partition_to_today():
    fake_facade = MagicMock()
    fake_facade.upload_snapshot.return_value = []
    with patch.object(bulk, "BackfillFacade", return_value=fake_facade):
        bulk.backfill_snapshot("SRC-WPG-SNOW", bucket="uoip")

    assert fake_facade.upload_snapshot.call_args.kwargs["ingest_date"] == date.today()


def test_backfill_snapshot_honours_an_explicit_ingest_date():
    fake_facade = MagicMock()
    fake_facade.upload_snapshot.return_value = []
    with patch.object(bulk, "BackfillFacade", return_value=fake_facade):
        bulk.backfill_snapshot(
            "SRC-WPG-SNOW", bucket="uoip", ingest_date=date(2026, 11, 3),
        )

    assert fake_facade.upload_snapshot.call_args.kwargs["ingest_date"] == date(
        2026, 11, 3,
    )


def test_backfill_snapshot_reports_failure_rather_than_raising():
    """One failed collection must surface as a result, not kill the caller."""
    fake_facade = MagicMock()
    fake_facade.upload_snapshot.side_effect = RuntimeError("upstream 503")
    with patch.object(bulk, "BackfillFacade", return_value=fake_facade):
        results = bulk.backfill_snapshot("SRC-WPG-SNOW", bucket="uoip")

    assert results[0].status == "failed"
    assert "upstream 503" in results[0].error


def test_fetch_snapshot_passes_an_empty_bucket():
    fake_facade = MagicMock()
    fake_facade.fetch_snapshot.return_value = {"snow_clearing_status": [{"x": 1}] * 3}
    with patch.object(bulk, "BackfillFacade", return_value=fake_facade) as mock_cls:
        results = bulk.fetch_snapshot("SRC-WPG-SNOW")

    assert mock_cls.call_args.kwargs["bucket"] == ""
    assert results[0].manifest_count == 3


# ── Fetch variants (dry-run) ──────────────────────────────────────────────────


def test_fetch_daily_window_does_not_write_to_object_storage():
    """fetch_* variants pass an empty bucket to the facade — no writes."""
    fake_facade = MagicMock()
    fake_facade.fetch_day.return_value = {"nyc_311": [{"x": 1}] * 5}
    with patch.object(bulk, "BackfillFacade", return_value=fake_facade) as mock_cls:
        fetch_daily_window("SRC-NYC-311",
                           start=date(2026, 6, 1), end=date(2026, 6, 4),
                           max_workers=1)
    # Bucket should be empty string (no writes)
    assert mock_cls.call_args.kwargs["bucket"] == ""
    assert fake_facade.fetch_day.call_count == 3


def test_fetch_static_one_call():
    fake_facade = MagicMock()
    fake_facade.fetch_static.return_value = {"borough_boundaries": [{"x": 1}]}
    with patch.object(bulk, "BackfillFacade", return_value=fake_facade):
        results = fetch_static("SRC-DCP")
    assert len(results) == 1
    assert results[0].status == "ok"


# ── BulkResult dataclass ────────────────────────────────────────────────────


def test_bulk_result_is_frozen():
    """BulkResult is frozen — a chunk outcome shouldn't be mutable."""
    r = BulkResult(document=date(2026, 6, 1), status="ok",
                   manifest_count=10, error=None)
    with pytest.raises((AttributeError, Exception)):
        r.status = "failed"  # type: ignore[misc]


# ── backfill_daily_window: api_type dispatch (wide-fetch vs per-day) ────────


def test_backfill_daily_window_open_meteo_uses_one_wide_facade_call():
    """Open-Meteo's API takes past_days, not arbitrary dates — bulk must
    NOT call upload_day N times (each call would return the same data).
    It should call facade.upload_window ONCE for the whole window."""
    facade = MagicMock()
    facade.upload_window.return_value = [
        SimpleNamespace(record_count=24, filename="data_2026-06-07.json", dataset_name="ds"),
        SimpleNamespace(record_count=24, filename="data_2026-06-08.json", dataset_name="ds"),
    ]
    with patch.object(bulk, "BackfillFacade", return_value=facade):
        results = backfill_daily_window(
            "SRC-Open-Meteo",
            start=date(2026, 6, 7), end=date(2026, 6, 14),
            bucket="bkt", max_workers=4,
        )

    facade.upload_window.assert_called_once()
    # Per-day upload_day must NOT have been called (would waste 7 Socrata
    # calls returning the same data).
    facade.upload_day.assert_not_called()
    # Result is 1 entry (not 7) — describes the single wide call.
    assert len(results) == 1
    assert results[0].status == "ok"
    assert results[0].manifest_count == 2  # 2 per-day files produced


def test_backfill_daily_window_socrata_still_per_day_calls():
    """For Socrata-based daily sources (311), bulk should still call
    facade.upload_day once per day. The wide-fetch path is Open-Meteo only."""
    facade = MagicMock()
    facade.upload_day.return_value = [
        SimpleNamespace(record_count=10, filename="x.json", dataset_name="ds"),
    ]
    with patch.object(bulk, "BackfillFacade", return_value=facade):
        results = backfill_daily_window(
            "SRC-NYC-311",
            start=date(2026, 6, 1), end=date(2026, 6, 4),  # 3 days
            bucket="bkt", max_workers=1,
        )

    # 3 per-day calls, no wide fetch
    assert facade.upload_day.call_count == 3
    facade.upload_window.assert_not_called()
    assert len(results) == 3
    # Results in completion order (serial)
    assert [r.document for r in results] == [
        date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3),
    ]


def test_fetch_daily_window_open_meteo_uses_one_wide_fetch():
    """Dry-run path for Open-Meteo also uses the wide-fetch dispatch."""
    facade = MagicMock()
    facade.fetch_window.return_value = {"ds": [{"x": 1}] * 24}
    with patch.object(bulk, "BackfillFacade", return_value=facade):
        results = fetch_daily_window(
            "SRC-Open-Meteo",
            start=date(2026, 6, 7), end=date(2026, 6, 14),
            max_workers=1,
        )

    facade.fetch_window.assert_called_once()
    facade.fetch_day.assert_not_called()
    assert len(results) == 1
    assert results[0].manifest_count == 24  # 24 records fetched
