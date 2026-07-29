"""
GCS Loader — writes raw NDJSON records to Bronze layer on Google Cloud Storage.

Data files are newline-delimited JSON (one record per line) so they can be
appended/streamed and a single corrupt line never invalidates the whole file.
Manifest files remain single JSON objects (metadata, not records).

Storage layouts:

  # Daily incremental (DAG loads) — partition by ingest_date
  {bucket}/bronze/raw/{source_id}/{dataset_name}/ingest_date=YYYY-MM-DD/data.ndjson
  {bucket}/bronze/raw/{source_id}/{dataset_name}/ingest_date=YYYY-MM-DD/manifest.json

  # Daily split (backfill of high-volume event streams) — records are
  # grouped by their timestamp_field and one file is written per day,
  # nested inside a month folder. manifest.json in each month folder
  # describes the most recent upload.
  {bucket}/bronze/raw/{source_id}/{dataset_name}/YYYY-MM/data_YYYY-MM-DD.ndjson
  {bucket}/bronze/raw/{source_id}/{dataset_name}/YYYY-MM/manifest_YYYY-MM-DD.json

  # Monthly backfill / shard — flat files, month encoded in filename
  {bucket}/bronze/raw/{source_id}/{dataset_name}/data_YYYY-MM.ndjson
  {bucket}/bronze/raw/{source_id}/{dataset_name}/manifest_YYYY-MM.json

The daily split layout is used by sources with ``partition_strategy: daily``
in their YAML (currently SRC-NYC-311, SRC-Open-Meteo). The monthly layout
is used by ``partition_strategy: monthly`` (NYPD, DCP).

Manifest contents:
  - source_id, dataset_name, ingest_date, month_partition
  - record_count, file_size_bytes, sha256_checksum
  - data_date_range (min/max of the timestamp field)
  - fetch_timestamp (ISO datetime of this upload — overwrites on re-run)
  - timestamp_field

Re-upload behavior: data and manifest are always overwritten (GCS PUT is idempotent).

Phase: Phase 1 (GCP)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from typing import Any

from google.cloud import storage


def _utc_now_naive() -> datetime:
    """Current UTC time as a naive datetime.

    Exact replacement for the deprecated `datetime.utcnow()` (removed in a
    future Python), deliberately keeping the value *naive* rather than
    returning `datetime.now(UTC)` directly: `fetch_timestamp` is serialised
    into every Bronze manifest, and an aware datetime would append "+00:00",
    changing the on-disk manifest format. That would make new manifests
    inconsistent with the thousands already written, and any code comparing a
    parsed old timestamp against a parsed new one would raise
    "can't subtract offset-naive and offset-aware datetimes".

    The value is UTC either way — see the module docstring. If the manifest
    format is ever versioned, switch this to `datetime.now(UTC)` and migrate
    the readers in tests/integration/ at the same time.
    """
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass
class ManifestEntry:
    """Metadata manifest entry written alongside each Bronze data file."""

    source_id: str
    dataset_name: str
    ingest_date: str  # YYYY-MM-DD (date of this upload)
    month_partition: str  # YYYY-MM (calendar month of the data)
    filename: str
    record_count: int
    file_size_bytes: int
    sha256_checksum: str
    data_date_min: str | None  # ISO date of earliest record
    data_date_max: str | None  # ISO date of latest record
    fetch_timestamp: str  # ISO datetime of this upload (re-written on re-run)
    timestamp_field: str  # field used for date range (e.g. "created_date")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GCSBronzeLoader:
    """Writes raw NDJSON records to GCS Bronze layer with a JSON manifest."""

    # Daily incremental filenames
    DATA_FILE = "data.ndjson"
    MANIFEST_FILE = "manifest.json"

    def __init__(
        self,
        bucket_name: str,
        timestamp_field: str = "created_date",
        client: storage.Client | None = None,
    ) -> None:
        """
        Initialize Bronze loader.

        Args:
            bucket_name: GCS bucket name (e.g. "nyc-uoip").
            timestamp_field: Field name for date range extraction in manifest.
            client: Optional GCS client. If None, uses default credentials.
        """
        self.bucket_name = bucket_name
        self.timestamp_field = timestamp_field
        self._client = client or storage.Client()

    def _sha256(self, data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _to_ndjson(self, records: list[dict[str, Any]]) -> bytes:
        """Serialize records as newline-delimited JSON (one record per line)."""
        if not records:
            return b""
        lines = (json.dumps(r, ensure_ascii=False) for r in records)
        return ("\n".join(lines) + "\n").encode("utf-8")

    def _date_range(
        self, records: list[dict[str, Any]]
    ) -> tuple[str | None, str | None]:
        """Extract min/max date from records using timestamp_field."""
        dates = []
        for r in records:
            raw = r.get(self.timestamp_field)
            if not raw:
                continue
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                dates.append(dt.date())
            except ValueError:
                continue
        if not dates:
            return None, None
        return min(dates).isoformat(), max(dates).isoformat()

    def _group_by_date(
        self, records: list[dict[str, Any]]
    ) -> dict[str, list[dict[str, Any]]]:
        """Group records by their date in the ``timestamp_field``.

        Records with a missing or unparseable timestamp are dropped. Each
        group is keyed by ISO date ``YYYY-MM-DD`` and preserves input order
        within the group.

        Returns:
            Ordered dict ``{YYYY-MM-DD: [records_for_that_day, ...]}``.

        Raises:
            ValueError: if ``timestamp_field`` is empty.
        """
        if not self.timestamp_field:
            raise ValueError(
                "Cannot group by date: timestamp_field is not configured on this loader",
            )
        groups: dict[str, list[dict[str, Any]]] = {}
        for r in records:
            raw = r.get(self.timestamp_field)
            if not raw:
                continue
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            key = dt.date().isoformat()
            groups.setdefault(key, []).append(r)
        return groups

    def _make_manifest(
        self,
        source_id: str,
        dataset_name: str,
        ingest_date: date,
        month_partition: str,
        filename: str,
        records: list[dict[str, Any]],
        content_bytes: bytes,
    ) -> ManifestEntry:
        data_min, data_max = self._date_range(records)
        return ManifestEntry(
            source_id=source_id,
            dataset_name=dataset_name,
            ingest_date=ingest_date.isoformat(),
            month_partition=month_partition,
            filename=filename,
            record_count=len(records),
            file_size_bytes=len(content_bytes),
            sha256_checksum=self._sha256(content_bytes),
            data_date_min=data_min,
            data_date_max=data_max,
            fetch_timestamp=_utc_now_naive().isoformat(),
            timestamp_field=self.timestamp_field,
        )

    def _upload(
        self,
        bucket: storage.Bucket,
        path: str,
        content: bytes,
        meta: dict[str, str],
        content_type: str = "application/json",
    ) -> None:
        """Upload bytes to GCS; overwrites existing object at path."""
        blob = bucket.blob(path)
        blob.upload_from_string(content, content_type=content_type)
        blob.metadata = meta

    # ── Daily incremental write (DAG loads) ────────────────────────────────────

    def write(
        self,
        source_id: str,
        dataset_name: str,
        ingest_date: date,
        records: list[dict[str, Any]],
    ) -> ManifestEntry:
        """
        Write records using daily ingest_date partition.

        Path: bronze/raw/{source_id}/{dataset_name}/ingest_date={date}/data.ndjson
              bronze/raw/{source_id}/{dataset_name}/ingest_date={date}/manifest.json

        Idempotent: re-running for the same ingest_date overwrites both files.
        """
        bucket = self._client.bucket(self.bucket_name)
        content = self._to_ndjson(records)
        month = ingest_date.strftime("%Y-%m")

        manifest = self._make_manifest(
            source_id=source_id,
            dataset_name=dataset_name,
            ingest_date=ingest_date,
            month_partition=month,
            filename=self.DATA_FILE,
            records=records,
            content_bytes=content,
        )

        data_path = f"bronze/raw/{source_id}/{dataset_name}/ingest_date={ingest_date.isoformat()}/{self.DATA_FILE}"
        self._upload(bucket, data_path, content, {
            "source_id": source_id,
            "dataset_name": dataset_name,
            "ingest_date": ingest_date.isoformat(),
            "record_count": str(len(records)),
        }, content_type="application/x-ndjson")

        manifest_path = f"bronze/raw/{source_id}/{dataset_name}/ingest_date={ingest_date.isoformat()}/{self.MANIFEST_FILE}"
        manifest_bytes = json.dumps(manifest.to_dict(), indent=2).encode("utf-8")
        self._upload(bucket, manifest_path, manifest_bytes, {
            "source_id": source_id,
            "dataset_name": dataset_name,
            "ingest_date": ingest_date.isoformat(),
        })

        return manifest

    # ── Monthly shard write (backfill / historical loads) ─────────────────────

    def write_monthly_shard(
        self,
        source_id: str,
        dataset_name: str,
        month_partition: str,
        records: list[dict[str, Any]],
    ) -> ManifestEntry:
        """
        Write a flat monthly shard — no month= subdirectory.

        Path: bronze/raw/{source_id}/{dataset_name}/data_{YYYY-MM}.ndjson
              bronze/raw/{source_id}/{dataset_name}/manifest_{YYYY-MM}.json

        month_partition: YYYY-MM string (e.g. "2026-03").

        Idempotent: re-running for the same month overwrites both files.
        fetch_timestamp in the manifest reflects the time of the latest upload.
        """
        bucket = self._client.bucket(self.bucket_name)
        content = self._to_ndjson(records)
        ingest_date = date.today()

        manifest = self._make_manifest(
            source_id=source_id,
            dataset_name=dataset_name,
            ingest_date=ingest_date,
            month_partition=month_partition,
            filename=f"data_{month_partition}.ndjson",
            records=records,
            content_bytes=content,
        )

        data_path = f"bronze/raw/{source_id}/{dataset_name}/data_{month_partition}.ndjson"
        self._upload(bucket, data_path, content, {
            "source_id": source_id,
            "dataset_name": dataset_name,
            "month_partition": month_partition,
            "record_count": str(len(records)),
        }, content_type="application/x-ndjson")

        manifest_path = f"bronze/raw/{source_id}/{dataset_name}/manifest_{month_partition}.json"
        manifest_bytes = json.dumps(manifest.to_dict(), indent=2).encode("utf-8")
        self._upload(bucket, manifest_path, manifest_bytes, {
            "source_id": source_id,
            "dataset_name": dataset_name,
            "month_partition": month_partition,
        })

        return manifest

    # ── Daily split write (per-source partition_strategy=daily) ───────────────

    def write_daily(
        self,
        source_id: str,
        dataset_name: str,
        records: list[dict[str, Any]],
    ) -> list[ManifestEntry]:
        """
        Write records split by their data date into per-day files.

        Used by sources with ``partition_strategy: daily`` (NYC 311,
        Open-Meteo). Records are grouped by their date in ``timestamp_field``
        and each group is written as its own file with a paired manifest:

            bronze/raw/{source_id}/{dataset_name}/{YYYY-MM}/data_{YYYY-MM-DD}.ndjson
            bronze/raw/{source_id}/{dataset_name}/{YYYY-MM}/manifest_{YYYY-MM-DD}.json

        Each day has its own manifest describing the data file in the
        same folder. To enumerate all days in a month, list the
        ``data_*.ndjson`` objects; each one has a matching ``manifest_*.json``.

        Records with a missing or unparseable timestamp are dropped (they
        cannot be assigned to a day). If no records remain, the call
        returns an empty list and writes nothing.

        Idempotent: re-running writes overwrite the per-day data file and
        the per-day manifest.

        Returns:
            One :class:`ManifestEntry` per day written, sorted by date.
        """
        if not self.timestamp_field:
            raise ValueError(
                f"write_daily() requires timestamp_field; loader for "
                f"{source_id}/{dataset_name} has none configured",
            )
        groups = self._group_by_date(records)
        if not groups:
            return []

        bucket = self._client.bucket(self.bucket_name)
        ingest_date = date.today()
        manifests: list[ManifestEntry] = []

        for day_iso, day_records in sorted(groups.items()):
            day = date.fromisoformat(day_iso)
            month_partition = day.strftime("%Y-%m")
            data_filename = f"data_{day_iso}.ndjson"
            manifest_filename = f"manifest_{day_iso}.json"

            content = self._to_ndjson(day_records)
            manifest = self._make_manifest(
                source_id=source_id,
                dataset_name=dataset_name,
                ingest_date=ingest_date,
                month_partition=month_partition,
                filename=data_filename,
                records=day_records,
                content_bytes=content,
            )

            data_path = (
                f"bronze/raw/{source_id}/{dataset_name}/"
                f"{month_partition}/{data_filename}"
            )
            self._upload(bucket, data_path, content, {
                "source_id": source_id,
                "dataset_name": dataset_name,
                "data_date": day_iso,
                "month_partition": month_partition,
                "record_count": str(len(day_records)),
            }, content_type="application/x-ndjson")

            manifest_path = (
                f"bronze/raw/{source_id}/{dataset_name}/"
                f"{month_partition}/{manifest_filename}"
            )
            manifest_bytes = json.dumps(
                manifest.to_dict(), indent=2,
            ).encode("utf-8")
            self._upload(bucket, manifest_path, manifest_bytes, {
                "source_id": source_id,
                "dataset_name": dataset_name,
                "data_date": day_iso,
                "month_partition": month_partition,
            })

            manifests.append(manifest)

        return manifests


def load_gcs_credentials() -> str:
    """Load GCS credentials path from environment."""
    import os
    cred_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    if not cred_path:
        raise OSError(
            "GOOGLE_APPLICATION_CREDENTIALS not set. "
            "Set path to GCP service account JSON key file."
        )
    return cred_path
