"""
Bulk orchestration helpers — outer layer that **slices** a [start, end)
window into N documents and calls the atomic facade for each.

The :class:`BackfillFacade` only handles **one document** (one day, one
month, or one static snapshot). Bulk's job is to **cut** the user's
window into N pieces and dispatch each piece:

- daily source   → cut into N=days  → call ``facade.upload_day(day)`` each
- monthly source → cut into N=months → call ``facade.upload_month(month)`` each
- static source  → 1 piece         → call ``facade.upload_static()`` once

Multi-threading is a **secondary perf knob** (set ``max_workers>1``); the
primary contract is the slicing. ``max_workers=1`` gives serial,
deterministic execution.

For ``daily`` sources the dispatch depends on the **api_type** of the
source's first dataset:

- ``api_type=socrata`` (e.g. 311) — per-day fetch: 1 Socrata query per day
- ``api_type=open_meteo`` — **wide fetch**: 1 Open-Meteo query covers the
  whole window (its API takes ``past_days`` + ``forecast_days`` relative
  to today, not arbitrary dates). The facade's ``upload_window`` method
  is the single entry point; ``write_daily`` inside splits the response
  by date into per-day shards.

Use from a one-off script, an Airflow DAG, a per-source script, or a
notebook:

    from scripts.backfill.bulk import backfill_daily_window, backfill_monthly_window
    from datetime import date

    # Backfill 311 for June 2026: 30 slices (one per day, Socrata per-day)
    backfill_daily_window(
        "SRC-NYC-311",
        start=date(2026, 6, 1),
        end=date(2026, 7, 1),
        bucket="nyc-uoip",
    )

    # Backfill Open-Meteo for last week: 1 call, 7 daily files produced
    backfill_daily_window(
        "SRC-Open-Meteo",
        start=date(2026, 6, 7),
        end=date(2026, 6, 14),
        bucket="nyc-uoip",
    )

    # Backfill NYPD for Q1 2026: 3 slices (one per month, each writes
    # 4 dataset shards). 12 objects total.
    backfill_monthly_window(
        "SRC-NYPD",
        start=date(2026, 1, 1),
        end=date(2026, 4, 1),
        bucket="nyc-uoip",
    )

These helpers are NOT CLI entry points — see ``scripts/backfill/main.py``
and the per-source ``backfill_*.py`` scripts for that.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from ingestion.backfill import BackfillError, BackfillFacade
from ingestion.config import ApiType, SourceConfig, load_source_config

logger = logging.getLogger(__name__)


def _is_wide_fetch_source(cfg: SourceConfig) -> bool:
    """True if the source's API takes a window in a single call.

    Open-Meteo: API takes ``past_days`` + ``forecast_days`` (not
    arbitrary dates). 1 call covers the whole window.

    Socrata: API takes a ``$where`` clause with arbitrary date range,
    but each day's data is its own query — we slice per day to give
    better progress visibility and failure isolation.
    """
    return bool(cfg.datasets) and cfg.datasets[0].api_type == ApiType.OPEN_METEO


# ── Result type ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BulkResult:
    """One chunk's outcome from a bulk backfill.

    For daily sources, ``document`` is the day.
    For monthly sources, ``document`` is the 1st of the month.
    For static sources, ``document`` is ``None``.
    """
    document: date | None
    status: Literal["ok", "failed"]
    manifest_count: int
    error: str | None


# ── Date / month iterators ────────────────────────────────────────────────────


def _daterange(start: date, end: date) -> list[date]:
    """Return every day in ``[start, end)`` as a list, in chronological order."""
    if end <= start:
        return []
    days = (end - start).days
    return [start + timedelta(days=i) for i in range(days)]


def _monthrange(start: date, end: date) -> list[date]:
    """Return the 1st of every month in ``[start, end)``, in chronological order.

    ``start`` and ``end`` are both normalized to the 1st of their month.
    """
    if end <= start.replace(day=1):
        return []
    months: list[date] = []
    cur = start.replace(day=1)
    while cur < end:
        months.append(cur)
        if cur.month == 12:
            cur = date(cur.year + 1, 1, 1)
        else:
            cur = date(cur.year, cur.month + 1, 1)
    return months


# ── Parallel executor ──────────────────────────────────────────────────────────


def _run_parallel(
    items: list[date | None],
    work_fn,
    *,
    max_workers: int,
) -> list[BulkResult]:
    """Submit one ``work_fn(item)`` per item to a thread pool.

    Returns one :class:`BulkResult` per item, in completion order.
    Per-item failures are captured into the result; the pool does not raise.
    """
    results: list[BulkResult] = []
    if not items:
        return results
    if max_workers <= 1:
        # Serial — no thread overhead, deterministic order.
        for item in items:
            results.append(_safe_call(work_fn, item))
        return results

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_item = {pool.submit(_safe_call, work_fn, item): item for item in items}
        for future in as_completed(future_to_item):
            results.append(future.result())
    return results


def _safe_call(work_fn, item) -> BulkResult:
    """Run ``work_fn(item)`` and translate any exception into a failed BulkResult."""
    try:
        return work_fn(item)
    except BackfillError as e:
        return BulkResult(document=item, status="failed", manifest_count=0, error=str(e))
    except Exception as e:
        return BulkResult(
            document=item, status="failed", manifest_count=0,
            error=f"unexpected: {e}",
        )


# ── Daily window (SRC-NYC-311, SRC-Open-Meteo) ──────────────────────────────


def backfill_daily_window(
    source_id: str,
    *,
    start: date,
    end: date,
    bucket: str,
    max_workers: int = 4,
) -> list[BulkResult]:
    """**Slice** ``[start, end)`` for a daily-partition source.

    Dispatch by the source's ``api_type``:

    - ``open_meteo`` — 1 wide call to ``facade.upload_window(start, end)``.
      The Open-Meteo API takes ``past_days`` + ``forecast_days``, not
      arbitrary dates, so per-day slicing would call the same data N
      times. The single call's response is then split by
      ``timestamp_field`` into per-day files by ``write_daily``.
    - other (e.g. ``socrata``) — per-day slicing: 1 Socrata query per
      day, parallel up to ``max_workers``.

    Args:
        source_id: e.g. ``"SRC-NYC-311"`` (socrata) or ``"SRC-Open-Meteo"``.
        start: inclusive start day.
        end: exclusive end day.
        bucket: Object-storage bucket name.
        max_workers: thread-pool size for the per-day path (default 4;
            1 = serial). Ignored for the wide-fetch path.

    Returns:
        One :class:`BulkResult` per day (socrata) or one ``BulkResult``
        (open_meteo) describing the single wide call. Failures on one
        slice do not stop the others.
    """
    cfg = load_source_config(source_id)
    facade = BackfillFacade(cfg, bucket=bucket)

    if _is_wide_fetch_source(cfg):
        # Open-Meteo: 1 API call covers the whole window.
        # ``upload_window`` returns manifests already split by date.
        logger.info(
            "Slicing %s daily (wide-fetch): [%s, %s) -> 1 call",
            source_id, start, end,
        )
        try:
            manifests = facade.upload_window(start, end)
            for m in manifests:
                logger.info(
                    "%s: %d records -> %s", source_id, m.record_count, m.filename,
                )
            return [BulkResult(
                document=None, status="ok",
                manifest_count=len(manifests), error=None,
            )]
        except BackfillError as e:
            return [BulkResult(
                document=None, status="failed",
                manifest_count=0, error=str(e),
            )]

    # Socrata-style: 1 API call per day, parallel up to max_workers.
    days = _daterange(start, end)
    logger.info(
        "Slicing %s daily (per-day fetch): [%s, %s) -> %d days",
        source_id, start, end, len(days),
    )

    def _work(day: date) -> BulkResult:
        try:
            manifests = facade.upload_day(day)
        except BackfillError as e:
            return BulkResult(
                document=day, status="failed",
                manifest_count=0, error=str(e),
            )

        for m in manifests:
            logger.info(
                "%s %s: %d records -> %s", source_id, day, m.record_count, m.filename,
            )
        return BulkResult(
            document=day, status="ok",
            manifest_count=len(manifests), error=None,
        )

    return _run_parallel(days, _work, max_workers=max_workers)


def fetch_daily_window(
    source_id: str,
    *,
    start: date,
    end: date,
    max_workers: int = 4,
) -> list[BulkResult]:
    """For a daily-partition source, fetch (no write) over ``[start, end)``.

    Dispatch by ``api_type`` (same as :func:`backfill_daily_window`):
    Open-Meteo uses 1 wide call, others use per-day slicing.
    """
    cfg = load_source_config(source_id)
    facade = BackfillFacade(cfg, bucket="")

    if _is_wide_fetch_source(cfg):
        # Open-Meteo dry-run: 1 wide fetch, no write.
        logger.info(
            "Slicing %s daily (wide-fetch, dry-run): [%s, %s) -> 1 call",
            source_id, start, end,
        )
        data = facade.fetch_window(start, end)
        total = sum(len(r) for r in data.values())
        for ds_name, records in data.items():
            logger.info(
                "%s [%s, %s) %s: %d records (dry-run)",
                source_id, start, end, ds_name, len(records),
            )
        return [BulkResult(
            document=None, status="ok", manifest_count=total, error=None,
        )]

    days = _daterange(start, end)
    def _work(day: date) -> BulkResult:
        data = facade.fetch_day(day)
        total = sum(len(r) for r in data.values())
        for ds_name, records in data.items():
            logger.info("%s %s %s: %d records (dry-run)", source_id, day, ds_name, len(records))
        return BulkResult(document=day, status="ok", manifest_count=total, error=None)

    return _run_parallel(days, _work, max_workers=max_workers)


# ── Monthly window (SRC-NYPD) ────────────────────────────────────────────────


def backfill_monthly_window(
    source_id: str,
    *,
    start: date,
    end: date,
    bucket: str,
    max_workers: int = 2,
) -> list[BulkResult]:
    """**Slice** ``[start, end)`` into N months and call ``facade.upload_month``
    for each. ``start`` and ``end`` are normalized to the 1st of their month.

    Default ``max_workers=2`` (NYPD shares one Socrata token across 4
    datasets; going wider just hits the rate limit).

    Per month, ``facade.upload_month`` returns one ManifestEntry per
    dataset. If fewer than ``len(cfg.datasets)`` come back, the facade
    silently skipped some — we mark the month as failed so the
    per-source script exits 2.
    """
    cfg = load_source_config(source_id)
    facade = BackfillFacade(cfg, bucket=bucket)
    months = _monthrange(start, end)
    logger.info(
        "Slicing %s monthly: [%s, %s) -> %d months",
        source_id, start, end, len(months),
    )

    def _work(month: date) -> BulkResult:
        try:
            manifests = facade.upload_month(month)
        except BackfillError as e:
            return BulkResult(
                document=month, status="failed",
                manifest_count=0, error=str(e),
            )

        for m in manifests:
            logger.info(
                "%s %s: %d records -> %s",
                source_id, month.strftime("%Y-%m"), m.record_count, m.filename,
            )
        return BulkResult(
            document=month, status="ok",
            manifest_count=len(manifests), error=None,
        )

    return _run_parallel(months, _work, max_workers=max_workers)


def fetch_monthly_window(
    source_id: str,
    *,
    start: date,
    end: date,
    max_workers: int = 2,
) -> list[BulkResult]:
    """For each month in ``[start, end)``, call ``facade.fetch_month(month)``."""
    cfg = load_source_config(source_id)
    facade = BackfillFacade(cfg, bucket="")
    months = _monthrange(start, end)

    def _work(month: date) -> BulkResult:
        data = facade.fetch_month(month)
        total = sum(len(r) for r in data.values())
        for ds_name, records in data.items():
            logger.info("%s %s %s: %d records (dry-run)", source_id, month, ds_name, len(records))
        return BulkResult(document=month, status="ok", manifest_count=total, error=None)

    return _run_parallel(months, _work, max_workers=max_workers)


# ── Snapshot (overwrite-in-place upstreams) ───────────────────────────────────


def backfill_snapshot(
    source_id: str,
    *,
    bucket: str,
    ingest_date: date | None = None,
) -> list[BulkResult]:
    """One-shot: call ``facade.upload_snapshot()`` once.

    There is no window to slice. The upstream holds only its current state, so
    "backfilling" a snapshot source means collecting today — a date range would
    imply a history the upstream does not have. Missed days cannot be recovered.
    """
    cfg = load_source_config(source_id)
    facade = BackfillFacade(cfg, bucket=bucket)
    day = ingest_date or date.today()

    def _work(_: None) -> BulkResult:
        manifests = facade.upload_snapshot(ingest_date=day)
        for m in manifests:
            logger.info(
                "%s snapshot %s: %d records -> %s",
                source_id, day, m.record_count, m.filename,
            )
        return BulkResult(
            document=day, status="ok", manifest_count=len(manifests), error=None,
        )

    return _run_parallel([None], _work, max_workers=1)


def fetch_snapshot(
    source_id: str,
) -> list[BulkResult]:
    """One-shot: call ``facade.fetch_snapshot()`` once. No object-storage write."""
    cfg = load_source_config(source_id)
    facade = BackfillFacade(cfg, bucket="")

    def _work(_: None) -> BulkResult:
        data = facade.fetch_snapshot()
        total = sum(len(r) for r in data.values())
        for ds_name, records in data.items():
            logger.info(
                "%s snapshot %s: %d records (dry-run)", source_id, ds_name, len(records),
            )
        return BulkResult(
            document=date.today(), status="ok", manifest_count=total, error=None,
        )

    return _run_parallel([None], _work, max_workers=1)


# ── Static (SRC-DCP) ──────────────────────────────────────────────────────────


def backfill_static(
    source_id: str,
    *,
    bucket: str,
) -> list[BulkResult]:
    """One-shot: call ``facade.upload_static()`` once.

    Time arguments are ignored — static snapshots have no time dimension.
    """
    cfg = load_source_config(source_id)
    facade = BackfillFacade(cfg, bucket=bucket)

    def _work(_: None) -> BulkResult:
        manifests = facade.upload_static()
        for m in manifests:
            logger.info(
                "%s static: %d records -> %s", source_id, m.record_count, m.filename,
            )
        return BulkResult(document=None, status="ok", manifest_count=len(manifests), error=None)

    return _run_parallel([None], _work, max_workers=1)


def fetch_static(
    source_id: str,
) -> list[BulkResult]:
    """One-shot: call ``facade.fetch_static()`` once. No object-storage write."""
    cfg = load_source_config(source_id)
    facade = BackfillFacade(cfg, bucket="")

    def _work(_: None) -> BulkResult:
        data = facade.fetch_static()
        total = sum(len(r) for r in data.values())
        for ds_name, records in data.items():
            logger.info("%s static %s: %d records (dry-run)", source_id, ds_name, len(records))
        return BulkResult(document=None, status="ok", manifest_count=total, error=None)

    return _run_parallel([None], _work, max_workers=1)
