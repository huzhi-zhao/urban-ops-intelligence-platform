"""
BackfillFacade — atomic single-document fetch + write.

The facade exposes **one public method per (strategy × operation) combination**:

    daily    → upload_day(day) / fetch_day(day)
    monthly  → upload_month(month) / fetch_month(month)
    static   → upload_static() / fetch_static()
    snapshot → upload_snapshot(ingest_date) / fetch_snapshot()

Each method handles **exactly one document** (one day, one calendar month, one
static shard, or one collection-date snapshot). Bulk operations (yearly
backfills) are the caller's responsibility — see ``scripts/backfill/bulk.py``
for orchestration loops.

The strategy-method mapping is enforced: each method operates only on the
datasets whose effective strategy matches it, and raises ``ValueError`` when
none do — so calling ``upload_day()`` on a ``monthly`` source fails fast,
before any object-storage work begins. On a mixed source (a weather source
with a ``daily`` archive and a ``snapshot`` forecast) the same rule means
``upload_day()`` writes the archive and leaves the forecast untouched,
rather than forcing one dataset into the other's layout.

Bronze path layout is chosen per **dataset** by its effective partition
strategy — its own ``partition_strategy`` if set, else the source's. Use
``SourceConfig.strategy_for(ds)``; never read ``source.partition_strategy``
directly on a write path, or a per-dataset override is silently ignored.
- ``daily``   — high-volume event streams (service requests, weather).
                Records are
                split by ``timestamp_field`` into per-day files inside a
                month folder:
                ``s3://{bucket}/bronze/raw/{sid}/{ds}/{YYYY-MM}/data_{YYYY-MM-DD}.ndjson.gz``
                + a ``manifest_{YYYY-MM-DD}.json`` per day.
- ``monthly`` — lower-volume event streams. One file per month:
                ``s3://{bucket}/bronze/raw/{sid}/{ds}/data_{YYYY-MM}.ndjson.gz``
                + ``manifest_{YYYY-MM}.json``.
- ``static``  — reference data with no time dimension (administrative
                boundaries). Fixed shard name:
                ``s3://{bucket}/bronze/raw/{sid}/{ds}/data_static.ndjson.gz``
                + ``manifest_static.json``.
- ``snapshot`` — upstreams that overwrite in place and keep no history.
                Partitioned by *collection* date, not record date:
                ``s3://{bucket}/bronze/raw/{sid}/{ds}/ingest_date={YYYY-MM-DD}/data.ndjson.gz``
                + ``manifest.json``. A missed day is gone for good, which is why
                these do not run under Airflow (ADR 0006 §2.2).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from ingestion.backfill.fetchers import build_fetcher
from ingestion.config import DatasetConfig, SourceConfig
from ingestion.loaders.s3_loader import ManifestEntry, S3BronzeLoader

logger = logging.getLogger(__name__)


class BackfillError(Exception):
    """Raised by ``BackfillFacade`` on fetch or upload failure.

    Carries the offending source/dataset and the failing phase so structured
    logs and exit handlers can route the error to the right place.
    """

    def __init__(
        self,
        message: str,
        *,
        source_id: str | None = None,
        dataset_name: str | None = None,
        phase: str | None = None,
    ) -> None:
        super().__init__(message)
        self.source_id = source_id
        self.dataset_name = dataset_name
        self.phase = phase

    def __str__(self) -> str:
        prefix = f"[{self.phase}]" if self.phase else "[error]"
        ctx = []
        if self.source_id:
            ctx.append(f"source={self.source_id}")
        if self.dataset_name:
            ctx.append(f"dataset={self.dataset_name}")
        ctx_str = " ".join(ctx)
        return f"{prefix} {ctx_str} {self.args[0]}" if ctx_str else f"{prefix} {self.args[0]}"


class BackfillFacade:
    """Unified backfill facade — atomic fetch + write per document."""

    def __init__(
        self,
        source_config: SourceConfig,
        bucket: str,
        client: Any | None = None,
    ) -> None:
        self.cfg = source_config
        self.bucket = bucket
        self._client = client  # shared across per-dataset loaders
        # One loader per dataset so each can carry its own timestamp_field
        # (a source may carry several datasets with different timestamp columns).
        self._loaders: dict[str, S3BronzeLoader] = {
            ds.name: self._make_loader(ds) for ds in source_config.datasets
        }

    # ── Public API: 6 atomic methods (3 strategies × {upload, fetch}) ────────

    def upload_day(
        self,
        day: date,
        dataset_name: str | None = None,
    ) -> list[ManifestEntry]:
        """Atomic: fetch + write records for one calendar day.

        Operates on the datasets whose effective strategy is ``daily``.
        Raises ``ValueError`` if the source has none.
        """
        start, end = day, day + timedelta(days=1)
        return self._upload_window(start, end, dataset_name, strategy="daily")

    def fetch_day(
        self,
        day: date,
        dataset_name: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Atomic: fetch records for one calendar day, do not write."""
        start, end = day, day + timedelta(days=1)
        return self._fetch_window(start, end, dataset_name, strategy="daily")

    def upload_window(
        self,
        start: date,
        end: date,
        dataset_name: str | None = None,
    ) -> list[ManifestEntry]:
        """Atomic: fetch + write records for the given ``[start, end)`` window.

        Use this for sources whose upstream API accepts an arbitrary time
        window in a single call (e.g. **Open-Meteo**, whose ``past_days`` /
        ``forecast_days`` query params cover up to 92 + 16 days in one
        request). For Socrata-style APIs that need a per-day query, prefer
        ``upload_day`` (or ``upload_month``) — those are atomic at day /
        month granularity, which is what Socrata's incremental pull wants.

        For ``daily`` sources, the records returned are split by
        ``timestamp_field`` into per-day files (so a 7-day window produces
        up to 7 ``data_YYYY-MM-DD.ndjson.gz`` files). For ``monthly`` sources,
        the whole window is written as a single shard at the ``start`` month.

        Raises ``ValueError`` on ``static`` sources — use ``upload_static``
        for those.
        """
        if self.cfg.datasets_with_strategy("static"):
            raise ValueError(
                "upload_window() does not apply to static datasets; "
                "use upload_static() instead",
            )
        return self._upload_window(start, end, dataset_name)

    def fetch_window(
        self,
        start: date,
        end: date,
        dataset_name: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Atomic: fetch records for ``[start, end)`` from a wide-fetch API.

        Symmetric to :meth:`upload_window` but does not write. Use for
        Open-Meteo dry-runs.
        """
        if self.cfg.datasets_with_strategy("static"):
            raise ValueError(
                "fetch_window() does not apply to static datasets; "
                "use fetch_static() instead",
            )
        return self._fetch_window(start, end, dataset_name)

    def upload_month(
        self,
        month: date,
        dataset_name: str | None = None,
    ) -> list[ManifestEntry]:
        """Atomic: fetch + write records for one calendar month.

        ``month`` should be the first day of the target month
        (``date(YYYY, MM, 1)``). The internal window is
        ``[month, 1st of next month)``.
        """
        self._validate_first_of_month(month)
        start, end = month, self._next_month(month)
        return self._upload_window(start, end, dataset_name, strategy="monthly")

    def fetch_month(
        self,
        month: date,
        dataset_name: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Atomic: fetch records for one calendar month, do not write."""
        self._validate_first_of_month(month)
        start, end = month, self._next_month(month)
        return self._fetch_window(start, end, dataset_name, strategy="monthly")

    def upload_static(
        self,
        dataset_name: str | None = None,
    ) -> list[ManifestEntry]:
        """Atomic: fetch + write the current static snapshot.

        Requires ``partition_strategy='static'``. Time is irrelevant; the
        shard is written to ``data_static.ndjson.gz`` (fixed name) so re-runs
        overwrite the same file.
        """
        today = date.today()
        return self._upload_window(
            today, today + timedelta(days=1), dataset_name,
            month_partition_override="static", strategy="static",
        )

    def fetch_static(
        self,
        dataset_name: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Atomic: fetch the current static snapshot, do not write."""
        today = date.today()
        return self._fetch_window(
            today, today + timedelta(days=1), dataset_name, strategy="static",
        )

    def upload_snapshot(
        self,
        ingest_date: date | None = None,
        dataset_name: str | None = None,
    ) -> list[ManifestEntry]:
        """Atomic: pull the upstream's current state and store it under today.

        Requires ``partition_strategy='snapshot'``. The upstream keeps no
        history, so the pull is always "whatever it holds right now" and
        ``ingest_date`` only decides which partition receives it — it is not a
        query window and cannot be used to fetch the past.

        Args:
            ingest_date: Partition to write. Defaults to today. Pass an explicit
                date only to re-file a pull that crossed midnight; passing an
                older date does **not** retrieve that day's data.
            dataset_name: Restrict to one dataset of the source.
        """
        day = ingest_date or date.today()
        return self._upload_window(
            day, day + timedelta(days=1), dataset_name,
            ingest_date_override=day, strategy="snapshot",
        )

    def fetch_snapshot(
        self,
        dataset_name: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Atomic: pull the upstream's current state, do not write."""
        today = date.today()
        return self._fetch_window(
            today, today + timedelta(days=1), dataset_name, strategy="snapshot",
        )

    # ── Internal: window-based dispatch (called by the 6 atomic methods) ───

    def _upload_window(
        self,
        start: date,
        end: date,
        dataset_name: str | None,
        month_partition_override: str | None = None,
        ingest_date_override: date | None = None,
        strategy: str | None = None,
    ) -> list[ManifestEntry]:
        """Fetch from upstream and write to Bronze for the given window.

        ``month_partition_override`` is used by ``upload_static()`` to
        force the shard name to ``"static"`` instead of today's month.
        ``ingest_date_override`` is used by ``upload_snapshot()`` to name the
        ``ingest_date=`` partition. For all other callers both are ``None``
        and the shard name comes from ``start``.
        """
        datasets = self._resolve_datasets(dataset_name, strategy)
        manifests: list[ManifestEntry] = []
        failures: list[BaseException] = []

        for ds in datasets:
            try:
                records = self._fetch_one(ds, start, end)
            except BackfillError as e:
                logger.error(
                    "Fetch failed for %s/%s: %s", self.cfg.source.id, ds.name, e,
                )
                failures.append(e)
                continue

            if not records:
                logger.warning(
                    "No records for %s/%s in [%s, %s) — skipping",
                    self.cfg.source.id, ds.name, start, end,
                )
                continue

            try:
                written = self._write_one(
                    ds, records, start,
                    month_partition_override=month_partition_override,
                    ingest_date_override=ingest_date_override,
                )
            except Exception as e:
                logger.error(
                    "Write failed for %s/%s: %s", self.cfg.source.id, ds.name, e,
                )
                failures.append(
                    BackfillError(
                        f"upload failed: {e}",
                        source_id=self.cfg.source.id,
                        dataset_name=ds.name,
                        phase="upload",
                    ),
                )
                continue

            manifests.extend(written)
            for m in written:
                logger.info(
                    "Wrote %d records -> s3://%s/bronze/raw/%s/%s/%s",
                    m.record_count, self.bucket, self.cfg.source.id,
                    ds.name, m.filename,
                )

        if manifests:
            return manifests
        if failures:
            raise BackfillError(
                f"All {len(failures)} dataset(s) failed for {self.cfg.source.id!r}",
                source_id=self.cfg.source.id,
                phase="upload",
            ) from failures[0]
        # No manifests and no failures — every dataset returned zero records.
        return []

    def _fetch_window(
        self,
        start: date,
        end: date,
        dataset_name: str | None,
        strategy: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Fetch from upstream for the given window without writing."""
        datasets = self._resolve_datasets(dataset_name, strategy)
        out: dict[str, list[dict[str, Any]]] = {}
        failures: list[BaseException] = []

        for ds in datasets:
            try:
                records = self._fetch_one(ds, start, end)
            except BackfillError as e:
                logger.error("Fetch failed for %s/%s: %s", self.cfg.source.id, ds.name, e)
                failures.append(e)
                continue
            out[ds.name] = records
            logger.info(
                "Fetched %d records for %s/%s in [%s, %s)",
                len(records), self.cfg.source.id, ds.name, start, end,
            )

        if out:
            return out
        if failures:
            raise BackfillError(
                f"All {len(failures)} dataset(s) failed to fetch for {self.cfg.source.id!r}",
                source_id=self.cfg.source.id,
                phase="fetch",
            ) from failures[0]
        return {}

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _validate_first_of_month(self, month: date) -> None:
        if month.day != 1:
            raise ValueError(
                f"month must be the first day of a month (got {month}); "
                f"callers should pass date(YYYY, MM, 1)",
            )

    def _next_month(self, month: date) -> date:
        """Return the 1st of the month after `month`."""
        if month.month == 12:
            return date(month.year + 1, 1, 1)
        return date(month.year, month.month + 1, 1)

    def _resolve_datasets(
        self,
        dataset_name: str | None,
        strategy: str | None = None,
    ) -> list[DatasetConfig]:
        """Datasets this call operates on.

        ``strategy`` restricts the result to datasets whose *effective*
        partition strategy matches. That filter is what lets one source carry
        datasets with different time semantics: ``upload_day()`` on a weather
        source touches only its ``daily`` archive dataset and leaves the
        ``snapshot`` forecast alone, rather than writing the forecast into a
        layout it does not belong in.
        """
        if dataset_name is None:
            candidates = list(self.cfg.datasets)
        else:
            candidates = [d for d in self.cfg.datasets if d.name == dataset_name]
            if not candidates:
                valid = [d.name for d in self.cfg.datasets]
                raise ValueError(
                    f"Dataset {dataset_name!r} not in source {self.cfg.source.id!r}. "
                    f"Available: {valid}",
                )

        if strategy is None:
            return candidates

        matching = [d for d in candidates if self.cfg.strategy_for(d) == strategy]
        if not matching:
            described = (
                f"dataset {dataset_name!r}" if dataset_name
                else f"source {self.cfg.source.id!r}"
            )
            actual = {d.name: self.cfg.strategy_for(d) for d in candidates}
            raise ValueError(
                f"this method requires partition_strategy={strategy!r}, but "
                f"{described} has no such dataset. Effective strategies: {actual}",
            )
        return matching

    def _make_loader(self, ds: DatasetConfig) -> S3BronzeLoader:
        return S3BronzeLoader(
            bucket_name=self.bucket,
            timestamp_field=ds.timestamp_field or "",
            client=self._client,
        )

    def _fetch_one(
        self,
        ds: DatasetConfig,
        start: date,
        end: date,
    ) -> list[dict[str, Any]]:
        fetcher = build_fetcher(ds, start=start, end=end)
        try:
            return list(fetcher.fetch())
        except BackfillError:
            raise
        except Exception as e:
            raise BackfillError(
                f"fetch failed: {e}",
                source_id=self.cfg.source.id,
                dataset_name=ds.name,
                phase="fetch",
            ) from e

    def _write_one(
        self,
        ds: DatasetConfig,
        records: list[dict[str, Any]],
        start: date,
        month_partition_override: str | None = None,
        ingest_date_override: date | None = None,
    ) -> list[ManifestEntry]:
        """Dispatch to the write method matching the source's partition strategy.

        ``month_partition_override`` (used for static) replaces the
        default `start.strftime("%Y-%m")` for the monthly shard path.
        ``ingest_date_override`` (used for snapshot) names the ingest_date
        partition.
        """
        loader = self._loaders[ds.name]
        strategy = self.cfg.strategy_for(ds)
        if strategy == "daily":
            return loader.write_daily(
                source_id=self.cfg.source.id,
                dataset_name=ds.name,
                records=records,
            )
        if strategy == "snapshot":
            return [loader.write_snapshot(
                source_id=self.cfg.source.id,
                dataset_name=ds.name,
                ingest_date=ingest_date_override or start,
                records=records,
            )]
        # Default: monthly shard.
        month_partition = month_partition_override or start.strftime("%Y-%m")
        return [loader.write_monthly_shard(
            source_id=self.cfg.source.id,
            dataset_name=ds.name,
            month_partition=month_partition,
            records=records,
        )]
