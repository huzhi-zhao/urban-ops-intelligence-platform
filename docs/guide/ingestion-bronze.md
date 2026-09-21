# Ingestion and Bronze

Bronze is the record of what the pipeline collected. It keeps source fields
without business cleansing, alongside enough metadata to inspect a pull before
running Spark. [Data Sources](data-sources.md) explains what the records mean;
this page explains how they arrive and what a rerun can change.

## File format

Each data object is **gzipped NDJSON** (`.ndjson.gz`): one JSON record per line
before compression. Each has a separate, uncompressed JSON manifest. This lets
Spark parse a sequence of records and lets an operator inspect collection
metadata without downloading the full payload.

The loader serializes API records into NDJSON; the file is not a byte-for-byte
copy of the HTTP response envelope. Source fields are retained without analytical
classification or normalization.

Keep the `.gz` suffix and do not set `Content-Encoding`. Spark's `s3a://` reader
selects decompression from the filename; a compressed file with the wrong suffix
can be read as garbled text. See the [loader](../../ingestion/loaders/s3_loader.py).

## Partition strategies

A partition groups data according to its access and history requirements. All
paths below are relative to `bronze/raw/{source_id}/{dataset}/`.

| Strategy | Data and manifest paths | Current use |
|---|---|---|
| `daily` | `YYYY-MM/data_YYYY-MM-DD.ndjson.gz` and `YYYY-MM/manifest_YYYY-MM-DD.json` | Service requests, weather archive |
| `monthly` | `data_YYYY-MM.ndjson.gz` and `manifest_YYYY-MM.json` | Supported by the generic loader; no current Winnipeg source uses it |
| `static` | `data_static.ndjson.gz` and `manifest_static.json` | Small shift, ban and boundary reference tables |
| `snapshot` | `ingest_date=YYYY-MM-DD/data.ndjson.gz` and `ingest_date=YYYY-MM-DD/manifest.json` | Clearing status and forecast snapshots |

Daily files use the configured record timestamp. Snapshot files use collection
date because the same source can show a different state tomorrow. A dataset can
override its source's strategy; Open-Meteo archive and forecast do exactly that.

Paths and manifest field meanings form a frozen storage contract. A refactor
must preserve them for existing data.

## Read a manifest

| Field | Meaning |
|---|---|
| `record_count` | Number of records in the payload |
| `file_size_bytes` | Uncompressed NDJSON size |
| `sha256_checksum` | Checksum of the uncompressed payload |
| `compression` | `gzip` for these data objects |
| `stored_bytes` | Compressed object size |
| `fetch_timestamp` | Time of the upload, which can change on a rerun |

A checksum establishes the identity of that payload. It cannot establish that
an API returned every expected record. Row counts and key checks answer different
questions; [Data Quality](data-quality.md) shows why both are needed.

## What idempotence means here

Replaying a window targets the same paths rather than appending duplicate files.
For the same serialized record sequence, the uncompressed checksum is stable.
Compressed bytes and fetch metadata need not be identical, and an upstream
revision can legitimately change the payload.

The loader uses replacement writes. Historical “immutable Bronze” wording is a
preservation policy, not enforced object locking. In practice:

- Time-window refreshes can replace records to absorb late updates.
- Static refreshes replace the current reference file.
- A verified repair can replace a corrupt replayable window, followed by rebuilding
  its downstream outputs. The [pagination repair](../dev/postmortem/bronze-socrata-pagination-incident.md)
  is one recorded example.
- Snapshot history must not be backdated or reconstructed from current state.
  Even a second successful pull on the same day can overwrite the first observation.

Use a separate bucket for experiments. Do not manually edit raw payloads to make
an analytical result look right.

## The collection path

```text
source YAML → thin CLI or DAG → window slicing → fetcher → S3 loader
```

The CLI and scheduled ingestion reuse the same windowed functions. Per-source
entry points select a registered source; fetchers handle API-specific behavior;
the loader handles compression, paths and manifests. Parallelism belongs to
window slicing, not duplicated DAG logic.

The [snapshot collector](../../ingestion/snapshot/) instead streams a large
current-state pull through a temporary file. This avoids holding hundreds of
thousands of Python dictionaries in memory on the storage host.

## Incremental loads and repair

Service requests and weather archive have daily ingestion DAGs with lookback
windows. Retries handle transient failures. Airflow catchup schedules uncreated
historical intervals; it does **not** automatically retry an already failed run
indefinitely after retries are exhausted.

`dag_audit_bronze` checks recent manifests and fills eligible replayable gaps.
It checks snapshot coverage without fabricating missing observations. Its content
integrity task reports duplicates and upstream count differences; findings
produce a repair list, not automatic raw-data replacement. A check that cannot
execute is a separate failure.

The generic audit windows and source exclusions are defined in
[the audit DAG](../../dags/dag_audit_bronze.py). The concrete schedule is in
[Operations](operations.md#what-runs-when).

## Next steps

[Backfill](backfill.md) shows a bounded historical pull.
[Silver ETL](silver-etl.md) follows those objects into typed data.
[Snapshot Collection](snapshot-collection.md) covers observations that cannot be replayed.
