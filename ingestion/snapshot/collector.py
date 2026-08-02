"""
SnapshotCollector — one day's capture of an overwrite-in-place upstream.

This is the only ingestion path in the project that cannot be replayed. Every
other source can be re-fetched for any past window; a snapshot source keeps no
history, so a day that is not collected is gone permanently. Three consequences
run through this module:

1. **It streams.** Records go from the paginated fetch straight into a gzip file
   on disk. Nothing holds the dataset in memory (ADR 0006 §8.3.4).
2. **It refuses to write a suspiciously small pull.** An upstream that returns
   an empty list on a bad day would otherwise overwrite the partition with
   something that looks collected. See ``min_records``.
3. **It does not run under Airflow.** Airflow lives on the rebuildable compute
   node; this runs beside the storage it writes to (ADR 0006 §2.2). It therefore
   carries its own failure reporting rather than relying on
   ``on_failure_callback``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from ingestion.config import DatasetConfig, SourceConfig, load_source_config
from ingestion.loaders.s3_loader import (
    ManifestEntry,
    S3BronzeLoader,
    SnapshotTooSmallError,
)
from ingestion.snapshot.fetch import fetch_snapshot_records

logger = logging.getLogger(__name__)

# A pull smaller than this is treated as a failed collection rather than a real
# shrinkage of the upstream. Deliberately loose — it is a smoke alarm for "the
# API returned nothing/an error page", not a data-quality threshold. Row-count
# baselines belong in the quality framework, not here.
DEFAULT_MIN_RECORDS = 1000


@dataclass(frozen=True)
class SnapshotResult:
    """Outcome of collecting one dataset."""

    dataset_name: str
    status: str  # "ok" | "failed"
    record_count: int
    stored_bytes: int
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


class SnapshotCollectionError(RuntimeError):
    """Raised when at least one dataset of a snapshot source failed to collect."""


class SnapshotCollector:
    """Collects a source's ``snapshot``-strategy datasets.

    Only those datasets: a source may mix strategies (a weather source with a
    ``daily`` archive and a ``snapshot`` forecast), and the archive has no
    business being written into an ``ingest_date=`` partition.
    """

    def __init__(
        self,
        source_config: SourceConfig,
        bucket: str,
        client: Any | None = None,
        min_records: int = DEFAULT_MIN_RECORDS,
    ) -> None:
        self._datasets = source_config.datasets_with_strategy("snapshot")
        if not self._datasets:
            actual = {
                d.name: source_config.strategy_for(d) for d in source_config.datasets
            }
            raise ValueError(
                f"SnapshotCollector requires at least one dataset with "
                f"partition_strategy='snapshot', but source "
                f"{source_config.source.id!r} has none. Effective strategies: {actual}",
            )
        self.cfg = source_config
        self.bucket = bucket
        self._client = client
        self.min_records = min_records

    @classmethod
    def for_source(
        cls,
        source_id: str,
        bucket: str,
        client: Any | None = None,
        min_records: int = DEFAULT_MIN_RECORDS,
    ) -> SnapshotCollector:
        return cls(
            load_source_config(source_id),
            bucket=bucket,
            client=client,
            min_records=min_records,
        )

    def collect(self, ingest_date: date | None = None) -> list[SnapshotResult]:
        """Collect every dataset into the ``ingest_date`` partition.

        Args:
            ingest_date: Partition to write. Defaults to today. It labels the
                collection, it does not select data — passing an older date will
                not retrieve that day.

        Returns:
            One :class:`SnapshotResult` per dataset, in config order. Datasets
            are independent: one failing does not stop the rest.

        Raises:
            SnapshotCollectionError: after attempting all datasets, if any
                failed. Raising last rather than first means a multi-dataset
                source still collects everything it can before alerting.
        """
        day = ingest_date or date.today()
        results = [self._collect_one(ds, day) for ds in self._datasets]

        failed = [r for r in results if not r.ok]
        if failed:
            raise SnapshotCollectionError(
                f"{self.cfg.source.id} snapshot for {day}: "
                f"{len(failed)}/{len(results)} dataset(s) failed — "
                + "; ".join(f"{r.dataset_name}: {r.error}" for r in failed),
            )
        return results

    def _collect_one(self, ds: DatasetConfig, day: date) -> SnapshotResult:
        loader = S3BronzeLoader(
            bucket_name=self.bucket,
            timestamp_field=ds.timestamp_field or "",
            client=self._client,
        )
        try:
            manifest = loader.write_snapshot_stream(
                source_id=self.cfg.source.id,
                dataset_name=ds.name,
                ingest_date=day,
                records=fetch_snapshot_records(ds),
                min_records=self.min_records,
            )
        except SnapshotTooSmallError as e:
            logger.error("%s/%s: %s", self.cfg.source.id, ds.name, e)
            return SnapshotResult(
                dataset_name=ds.name, status="failed",
                record_count=0, stored_bytes=0, error=str(e),
            )
        except Exception as e:  # noqa: BLE001 — one dataset must not sink the rest
            logger.exception("%s/%s snapshot failed", self.cfg.source.id, ds.name)
            return SnapshotResult(
                dataset_name=ds.name, status="failed",
                record_count=0, stored_bytes=0, error=f"{type(e).__name__}: {e}",
            )

        self._log_success(ds, day, manifest)
        return SnapshotResult(
            dataset_name=ds.name, status="ok",
            record_count=manifest.record_count, stored_bytes=manifest.stored_bytes,
        )

    def _log_success(self, ds: DatasetConfig, day: date, m: ManifestEntry) -> None:
        logger.info(
            "%s/%s snapshot %s: %d records, %.1f MB stored (%.1fx compression) "
            "-> s3://%s/bronze/raw/%s/%s/ingest_date=%s/",
            self.cfg.source.id, ds.name, day, m.record_count,
            m.stored_bytes / 1_048_576,
            (m.file_size_bytes / m.stored_bytes) if m.stored_bytes else 0.0,
            self.bucket, self.cfg.source.id, ds.name, day,
        )
