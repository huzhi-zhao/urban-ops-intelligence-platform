"""
S3 Loader — writes raw NDJSON records to the Bronze layer on MinIO.

Data files are gzipped newline-delimited JSON (one record per line) so they can
be streamed and a single corrupt line never invalidates the whole file. Manifest
files stay uncompressed single JSON objects (metadata, not records — a few
hundred bytes each, and worth keeping readable with `head`).

Storage layouts:

  # Snapshot / daily incremental (DAG loads) — partition by ingest_date
  {bucket}/bronze/raw/{source_id}/{dataset_name}/ingest_date=YYYY-MM-DD/data.ndjson.gz
  {bucket}/bronze/raw/{source_id}/{dataset_name}/ingest_date=YYYY-MM-DD/manifest.json

  # Daily split (backfill of high-volume event streams) — records are
  # grouped by their timestamp_field and one file is written per day,
  # nested inside a month folder, each with its own manifest.
  {bucket}/bronze/raw/{source_id}/{dataset_name}/YYYY-MM/data_YYYY-MM-DD.ndjson.gz
  {bucket}/bronze/raw/{source_id}/{dataset_name}/YYYY-MM/manifest_YYYY-MM-DD.json

  # Monthly backfill / shard — flat files, month encoded in filename
  {bucket}/bronze/raw/{source_id}/{dataset_name}/data_YYYY-MM.ndjson.gz
  {bucket}/bronze/raw/{source_id}/{dataset_name}/manifest_YYYY-MM.json

The layout is selected by the source's ``partition_strategy`` in its YAML —
see AGENTS.md → "Bronze partitioning strategies" for the full table.

Manifest contents:
  - source_id, dataset_name, ingest_date, month_partition
  - record_count, file_size_bytes, sha256_checksum  ← describe the *uncompressed* payload
  - compression, stored_bytes                       ← describe the stored object
  - data_date_range (min/max of the timestamp field)
  - fetch_timestamp (ISO datetime of this upload — overwrites on re-run)
  - timestamp_field

Re-upload behavior: data and manifest are always overwritten (S3 PUT is idempotent).
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import tempfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from typing import IO, Any

from ingestion.loaders.s3_client import build_s3_client

# Bronze objects carry the .gz extension and deliberately do NOT set
# Content-Encoding. Spark's s3a:// reader picks its decompression codec from the
# file extension and ignores HTTP headers, so a gzip object named .ndjson is read
# as text and yields garbled rows *without raising*; and a Content-Encoding
# header makes some S3 clients decompress a second time. See ADR 0006 §4.1.
GZIP_SUFFIX = ".gz"
NDJSON_CONTENT_TYPE = "application/gzip"
MANIFEST_CONTENT_TYPE = "application/json"


def bronze_prefix(
    source_id: str,
    dataset_name: str,
    *,
    partition: str | None = None,
) -> str:
    """Object-storage prefix for one dataset, optionally one partition of it.

    ``partition`` is the strategy's partition segment as it appears on disk:
    ``"2026-01"`` for ``daily``, ``"ingest_date=2026-01-15"`` for ``snapshot``,
    ``None`` for ``monthly`` / ``static``, which write at the dataset root.

    Public because callers outside the loader need to *name* an object without
    writing one — logging a written path, auditing a partition. Building that
    string by hand is how a log line comes to disagree with where the bytes
    actually went.
    """
    base = f"bronze/raw/{source_id}/{dataset_name}"
    return f"{base}/{partition}" if partition else base


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


class SnapshotTooSmallError(RuntimeError):
    """A snapshot stream yielded implausibly few records, so nothing was written.

    Snapshot sources cannot be re-collected for a past day, so an empty or
    truncated pull must fail loudly rather than overwrite the partition with
    something that looks collected but is not.
    """


@dataclass(frozen=True)
class _StreamStats:
    """What the manifest needs, accumulated while the records stream past."""

    record_count: int
    uncompressed_bytes: int
    sha256: str
    data_date_min: str | None
    data_date_max: str | None


@dataclass
class ManifestEntry:
    """Metadata manifest entry written alongside each Bronze data file.

    ``file_size_bytes`` and ``sha256_checksum`` describe the **uncompressed
    NDJSON payload**, not the stored object: they describe the *data*, so
    re-running a backfill for the same records yields the same checksum
    regardless of the compression settings used to store it.
    """

    source_id: str
    dataset_name: str
    ingest_date: str  # YYYY-MM-DD (date of this upload)
    month_partition: str  # YYYY-MM (calendar month of the data)
    filename: str
    record_count: int
    file_size_bytes: int  # uncompressed payload
    sha256_checksum: str  # uncompressed payload
    compression: str | None  # "gzip" | None
    stored_bytes: int  # size of the object actually written
    data_date_min: str | None  # ISO date of earliest record
    data_date_max: str | None  # ISO date of latest record
    fetch_timestamp: str  # ISO datetime of this upload (re-written on re-run)
    timestamp_field: str  # field used for date range (e.g. "created_date")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class S3BronzeLoader:
    """Writes raw NDJSON records to the MinIO Bronze layer with a JSON manifest."""

    # Ingest-date partition filenames
    DATA_FILE = "data.ndjson" + GZIP_SUFFIX
    MANIFEST_FILE = "manifest.json"

    COMPRESSION = "gzip"

    def __init__(
        self,
        bucket_name: str,
        timestamp_field: str = "created_date",
        client: Any | None = None,
    ) -> None:
        """
        Initialize Bronze loader.

        Args:
            bucket_name: Object-storage bucket name (e.g. "uoip").
            timestamp_field: Field name for date range extraction in manifest.
            client: Optional boto3 S3 client. Built from the environment
                (``S3_*`` variables) when omitted.
        """
        self.bucket_name = bucket_name
        self.timestamp_field = timestamp_field
        self._client_override = client
        self._built_client: Any | None = None

    @property
    def _client(self) -> Any:
        """The S3 client, built on first write.

        Deliberately lazy: the facade constructs one loader per dataset up
        front, including for ``--dry-run`` fetches that never write anything.
        Building eagerly would make a dry run fail on a machine with no S3
        credentials configured, which is precisely the machine a dry run is for.
        """
        if self._client_override is not None:
            return self._client_override
        if self._built_client is None:
            self._built_client = build_s3_client()
        return self._built_client

    def _sha256(self, data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _to_ndjson(self, records: list[dict[str, Any]]) -> bytes:
        """Serialize records as newline-delimited JSON (one record per line)."""
        if not records:
            return b""
        lines = (json.dumps(r, ensure_ascii=False) for r in records)
        return ("\n".join(lines) + "\n").encode("utf-8")

    def _gzip(self, payload: bytes) -> bytes:
        """Compress a payload reproducibly.

        ``mtime=0`` keeps the output byte-identical across runs of the same
        records. gzip otherwise embeds the current time in its header, which
        would make two uploads of identical data differ — harmless for the
        manifest checksum (that covers the uncompressed payload) but confusing
        when comparing stored objects directly.
        """
        buffer = io.BytesIO()
        with gzip.GzipFile(fileobj=buffer, mode="wb", compresslevel=9, mtime=0) as fh:
            fh.write(payload)
        return buffer.getvalue()

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
        stored_bytes: int,
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
            compression=self.COMPRESSION,
            stored_bytes=stored_bytes,
            data_date_min=data_min,
            data_date_max=data_max,
            fetch_timestamp=_utc_now_naive().isoformat(),
            timestamp_field=self.timestamp_field,
        )

    def _upload(
        self,
        path: str,
        content: bytes,
        meta: dict[str, str],
        content_type: str = MANIFEST_CONTENT_TYPE,
    ) -> None:
        """Upload bytes to object storage; overwrites any existing object at path."""
        self._client.put_object(
            Bucket=self.bucket_name,
            Key=path,
            Body=content,
            ContentType=content_type,
            Metadata=meta,
        )

    def _write_pair(
        self,
        source_id: str,
        dataset_name: str,
        prefix: str,
        data_filename: str,
        manifest_filename: str,
        manifest: ManifestEntry,
        compressed: bytes,
        extra_meta: dict[str, str],
    ) -> None:
        """Write one data object and its paired manifest under ``prefix``."""
        base_meta = {
            "source_id": source_id,
            "dataset_name": dataset_name,
            **extra_meta,
        }
        self._upload(
            f"{prefix}/{data_filename}",
            compressed,
            {**base_meta, "record_count": str(manifest.record_count)},
            content_type=NDJSON_CONTENT_TYPE,
        )
        self._upload(
            f"{prefix}/{manifest_filename}",
            json.dumps(manifest.to_dict(), indent=2).encode("utf-8"),
            base_meta,
        )

    # ── Ingest-date partition write (daily incremental / snapshot) ─────────────

    def write(
        self,
        source_id: str,
        dataset_name: str,
        ingest_date: date,
        records: list[dict[str, Any]],
    ) -> ManifestEntry:
        """
        Write records using an ``ingest_date`` partition.

        Path: bronze/raw/{source_id}/{dataset_name}/ingest_date={date}/data.ndjson.gz
              bronze/raw/{source_id}/{dataset_name}/ingest_date={date}/manifest.json

        Idempotent: re-running for the same ingest_date overwrites both files.
        """
        content = self._to_ndjson(records)
        compressed = self._gzip(content)
        month = ingest_date.strftime("%Y-%m")

        manifest = self._make_manifest(
            source_id=source_id,
            dataset_name=dataset_name,
            ingest_date=ingest_date,
            month_partition=month,
            filename=self.DATA_FILE,
            records=records,
            content_bytes=content,
            stored_bytes=len(compressed),
        )

        self._write_pair(
            source_id=source_id,
            dataset_name=dataset_name,
            prefix=bronze_prefix(
                source_id, dataset_name,
                partition=f"ingest_date={ingest_date.isoformat()}",
            ),
            data_filename=self.DATA_FILE,
            manifest_filename=self.MANIFEST_FILE,
            manifest=manifest,
            compressed=compressed,
            extra_meta={"ingest_date": ingest_date.isoformat()},
        )

        return manifest

    def write_snapshot(
        self,
        source_id: str,
        dataset_name: str,
        ingest_date: date,
        records: list[dict[str, Any]],
    ) -> ManifestEntry:
        """
        Write one day's snapshot of an overwrite-in-place upstream.

        Same on-disk layout as :meth:`write` — the ``ingest_date=`` partition is
        exactly what a snapshot needs, so no new path code is involved. The
        separate name exists because the *guarantee* differs: a daily
        incremental partition can be refetched from upstream if it is lost, and
        a snapshot partition cannot. Anything that treats Bronze partitions as
        replayable must not treat these ones that way.

        Idempotent within a day: re-running on the same ``ingest_date``
        overwrites that day. It cannot recover a day that was never collected.
        """
        return self.write(
            source_id=source_id,
            dataset_name=dataset_name,
            ingest_date=ingest_date,
            records=records,
        )

    def write_snapshot_stream(
        self,
        source_id: str,
        dataset_name: str,
        ingest_date: date,
        records: Iterable[dict[str, Any]],
        min_records: int = 1,
    ) -> ManifestEntry:
        """
        Stream one day's snapshot to Bronze without materialising it.

        Records are consumed one at a time, serialised straight into a temporary
        gzip file on disk, and only then uploaded (boto3 splits the upload into
        parts on its own). Peak memory is therefore a single record plus the gzip
        window — not the whole dataset. The snapshot this exists for is ~238k
        records whose Python-object footprint far exceeds their 184 MB serialised
        size, and the storage node is small.

        Args:
            records: Any iterable — typically a paginated fetch generator.
            min_records: Refuse to write fewer than this many records. Defaults
                to 1 so a silent upstream failure cannot overwrite the day with
                an empty partition that *looks* collected. Nothing is uploaded
                when the guard trips: the temp file is discarded first.

        Raises:
            SnapshotTooSmallError: if the stream yielded fewer than
                ``min_records``. The caller should treat this as a failed
                collection and alert — for a snapshot source there is no later
                run that can recover this day.
        """
        prefix = bronze_prefix(
            source_id, dataset_name,
            partition=f"ingest_date={ingest_date.isoformat()}",
        )

        with tempfile.TemporaryFile() as raw_file:
            stats = self._stream_to_gzip(records, raw_file)

            if stats.record_count < min_records:
                raise SnapshotTooSmallError(
                    f"{source_id}/{dataset_name} snapshot for {ingest_date} yielded "
                    f"{stats.record_count} record(s), below the minimum of "
                    f"{min_records}; refusing to write. Nothing was uploaded, so "
                    f"the previous run's partition (if any) is untouched.",
                )

            stored_bytes = raw_file.tell()
            raw_file.seek(0)
            self._client.upload_fileobj(
                raw_file,
                self.bucket_name,
                f"{prefix}/{self.DATA_FILE}",
                ExtraArgs={
                    "ContentType": NDJSON_CONTENT_TYPE,
                    "Metadata": {
                        "source_id": source_id,
                        "dataset_name": dataset_name,
                        "ingest_date": ingest_date.isoformat(),
                        "record_count": str(stats.record_count),
                    },
                },
            )

        manifest = ManifestEntry(
            source_id=source_id,
            dataset_name=dataset_name,
            ingest_date=ingest_date.isoformat(),
            month_partition=ingest_date.strftime("%Y-%m"),
            filename=self.DATA_FILE,
            record_count=stats.record_count,
            file_size_bytes=stats.uncompressed_bytes,
            sha256_checksum=stats.sha256,
            compression=self.COMPRESSION,
            stored_bytes=stored_bytes,
            data_date_min=stats.data_date_min,
            data_date_max=stats.data_date_max,
            fetch_timestamp=_utc_now_naive().isoformat(),
            timestamp_field=self.timestamp_field,
        )

        self._upload(
            f"{prefix}/{self.MANIFEST_FILE}",
            json.dumps(manifest.to_dict(), indent=2).encode("utf-8"),
            {
                "source_id": source_id,
                "dataset_name": dataset_name,
                "ingest_date": ingest_date.isoformat(),
            },
        )

        return manifest

    def _stream_to_gzip(
        self,
        records: Iterable[dict[str, Any]],
        destination: IO[bytes],
    ) -> _StreamStats:
        """Serialise records into ``destination`` as gzipped NDJSON.

        Everything the manifest needs is accumulated on the way past, because
        after this returns the records are gone — the whole point is not to keep
        them. Byte counts and the checksum describe the *uncompressed* payload,
        matching the non-streaming path.
        """
        digest = hashlib.sha256()
        count = 0
        uncompressed = 0
        date_min: str | None = None
        date_max: str | None = None

        with gzip.GzipFile(
            fileobj=destination, mode="wb", compresslevel=9, mtime=0,
        ) as gz:
            for record in records:
                line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
                gz.write(line)
                digest.update(line)
                uncompressed += len(line)
                count += 1

                day = self._record_date(record)
                if day is not None:
                    date_min = day if date_min is None else min(date_min, day)
                    date_max = day if date_max is None else max(date_max, day)

        return _StreamStats(
            record_count=count,
            uncompressed_bytes=uncompressed,
            sha256=digest.hexdigest(),
            data_date_min=date_min,
            data_date_max=date_max,
        )

    def _record_date(self, record: dict[str, Any]) -> str | None:
        """ISO date of one record, or None when it has no usable timestamp."""
        if not self.timestamp_field:
            return None
        raw = record.get(self.timestamp_field)
        if not raw:
            return None
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            return None

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

        Path: bronze/raw/{source_id}/{dataset_name}/data_{YYYY-MM}.ndjson.gz
              bronze/raw/{source_id}/{dataset_name}/manifest_{YYYY-MM}.json

        month_partition: YYYY-MM string (e.g. "2026-03").

        Idempotent: re-running for the same month overwrites both files.
        fetch_timestamp in the manifest reflects the time of the latest upload.
        """
        content = self._to_ndjson(records)
        compressed = self._gzip(content)
        ingest_date = date.today()
        data_filename = f"data_{month_partition}.ndjson{GZIP_SUFFIX}"

        manifest = self._make_manifest(
            source_id=source_id,
            dataset_name=dataset_name,
            ingest_date=ingest_date,
            month_partition=month_partition,
            filename=data_filename,
            records=records,
            content_bytes=content,
            stored_bytes=len(compressed),
        )

        self._write_pair(
            source_id=source_id,
            dataset_name=dataset_name,
            prefix=bronze_prefix(source_id, dataset_name),
            data_filename=data_filename,
            manifest_filename=f"manifest_{month_partition}.json",
            manifest=manifest,
            compressed=compressed,
            extra_meta={"month_partition": month_partition},
        )

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

        Used by sources with ``partition_strategy: daily``. Records are
        grouped by their date in ``timestamp_field``
        and each group is written as its own file with a paired manifest:

            bronze/raw/{source_id}/{dataset_name}/{YYYY-MM}/data_{YYYY-MM-DD}.ndjson.gz
            bronze/raw/{source_id}/{dataset_name}/{YYYY-MM}/manifest_{YYYY-MM-DD}.json

        Each day has its own manifest describing the data file in the
        same folder. To enumerate all days in a month, list the
        ``data_*.ndjson.gz`` objects; each one has a matching ``manifest_*.json``.

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

        ingest_date = date.today()
        manifests: list[ManifestEntry] = []

        for day_iso, day_records in sorted(groups.items()):
            day = date.fromisoformat(day_iso)
            month_partition = day.strftime("%Y-%m")
            data_filename = f"data_{day_iso}.ndjson{GZIP_SUFFIX}"

            content = self._to_ndjson(day_records)
            compressed = self._gzip(content)
            manifest = self._make_manifest(
                source_id=source_id,
                dataset_name=dataset_name,
                ingest_date=ingest_date,
                month_partition=month_partition,
                filename=data_filename,
                records=day_records,
                content_bytes=content,
                stored_bytes=len(compressed),
            )

            self._write_pair(
                source_id=source_id,
                dataset_name=dataset_name,
                prefix=bronze_prefix(
                    source_id, dataset_name, partition=month_partition,
                ),
                data_filename=data_filename,
                manifest_filename=f"manifest_{day_iso}.json",
                manifest=manifest,
                compressed=compressed,
                extra_meta={
                    "data_date": day_iso,
                    "month_partition": month_partition,
                },
            )

            manifests.append(manifest)

        return manifests
