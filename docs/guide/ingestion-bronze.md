# Ingestion & the Bronze Layer

Bronze is the immutable record of what each upstream API returned. Nothing in the
platform is allowed to overwrite or edit a Bronze file after it is written — that rule
is what makes every downstream layer reproducible: if a score comes out wrong, it can be
recomputed from Bronze without touching the upstream again.

## File format

All Bronze data files are **gzipped NDJSON** (`.ndjson.gz`) — one JSON record per line,
compressed.

Neither half is a style preference:

- **Newline-delimited**, because `spark.read.json()` streams that shape and rejects JSON
  arrays. It also means one corrupt line cannot invalidate a whole file.
- **gzipped**, because it is a precondition rather than an optimisation. Measured
  compression is 5.6×–10×; the daily clearing snapshot alone is 184 MB/day uncompressed
  against 18.5 MB/day compressed — 67 GB/year versus 6.7 GB/year.

Manifests stay **uncompressed** `.json`. Each is a few hundred bytes, and being able to
`head` one is worth more than the saving. It also decouples the audit job's
existence checks from the compression strategy.

> 🚨 The `.gz` extension is **mandatory** and `Content-Encoding` must **never** be set.
> Spark's `s3a://` reader picks its decompression codec from the file extension and
> ignores HTTP headers: a gzip object named `.ndjson` is read as text and produces
> garbled rows **without raising an error**. This is the hardest failure mode in the
> pipeline to diagnose, and it is entirely preventable by naming files correctly.

## Partition layout

Each source declares `partition_strategy` in its YAML, which decides the path layout
under `bronze/raw/{source_id}/{dataset}/`:

| Strategy | For | Path |
|---|---|---|
| `daily` | High-volume event streams | `{YYYY-MM}/data_{YYYY-MM-DD}.ndjson.gz` + `{YYYY-MM}/manifest_{YYYY-MM-DD}.json` |
| `monthly` (default) | Lower-volume streams | `data_{YYYY-MM}.ndjson.gz` + `manifest_{YYYY-MM}.json` |
| `static` | Reference data that changes on the order of years | `data_static.ndjson.gz` + `manifest_static.json` |
| `snapshot` | Overwrite-in-place upstreams with no time field | `ingest_date={YYYY-MM-DD}/data.ndjson.gz` + `ingest_date={YYYY-MM-DD}/manifest.json` |

Under `daily`, records are split into per-day files by the date portion of the dataset's
`timestamp_field`, which is therefore mandatory. Records with a missing or unparseable
timestamp are dropped rather than filed under the wrong day.

`snapshot` partitions by **collection date rather than record date**, and is the only
strategy that permits `timestamp_field: null`. It exists for upstreams that overwrite in
place and keep no history, where each day's pull is the only copy that will ever exist.
`static` would write one fixed filename and overwrite yesterday — precisely what
`snapshot` must avoid. Snapshot collection is operated differently from everything else;
see [Snapshot Collection](snapshot-collection.md).

This layout and the manifest field names are a **frozen on-disk contract**. Data already
written cannot be rewritten (snapshot history is unrecoverable), so the implementation may
change but the paths and field semantics may not.

## Manifests

Every data file has a paired manifest. Two fields describe the **uncompressed NDJSON
payload**, not the stored object:

| Field | Describes |
|---|---|
| `record_count` | Records written |
| `file_size_bytes` | The uncompressed payload |
| `sha256_checksum` | The uncompressed payload |
| `compression` | `"gzip"` or `null` |
| `stored_bytes` | The object actually written |

Checksumming the uncompressed payload keeps the idempotency check meaningful: the same
records re-fetched produce the same checksum, unaffected by gzip's embedded timestamp.

The audit job uses manifests, not data files, to detect gaps.

## Code layout

```
scripts/backfill/backfill_<source>.py   CLI entry points — argparse only
        ↓
scripts/backfill/bulk.py                window slicing + thread pool
        ↓
ingestion/backfill/facade.py            one atomic fetch+write per document
        ↓
ingestion/loaders/s3_loader.py          gzip, manifest, MinIO write
        ↓
Bronze
```

Business logic lives only in the facade and bulk layers. Per-source scripts and DAG files
are pure dispatch — no API calls, no date arithmetic inline. Adding a source means adding
a YAML file and a dispatch script; the registry discovers it automatically.

Snapshot collection has its own path (`ingestion/snapshot/` + `scripts/collect_snapshot.py`)
because it must stream to a temporary file instead of materialising the pull in memory —
238k rows held as Python objects would exhaust the storage node.

## Scheduled ingestion

Replayable sources run as Airflow DAGs, in four groups:

| Group | Naming | Schedule |
|---|---|---|
| Incremental | `dag_ingest_<dataset>` | Cron, `catchup=True`, with a lookback window |
| Backfill | `dag_backfill_<dataset>` | `schedule=None`, driven by `start` / `end` / `bucket` params |
| Audit / self-heal | `dag_audit_bronze` | Daily |
| Silver | `dag_silver_<dataset>` | Cron, after the Bronze window it depends on |

The snapshot source is deliberately **not** in this list — it runs as a systemd timer on
the storage node.

## Self-healing

`dag_audit_bronze` scans manifests over a rolling window (14 days for daily sources,
3 months for monthly) and, for any gap it finds, calls the same bulk functions the
backfill path uses. If a gap cannot be filled the task fails loudly rather than silently
leaving a hole.

A transient upstream outage therefore usually repairs itself:

```
ingest DAG fails
  → retries=3 (covers network flakiness)
  → catchup=True re-runs the missed interval on the next scheduler pass
  → dag_audit_bronze finds any remaining gap and refills it
  → still failing? task turns red and alerts
```

Snapshot partitions are checked by the same job but **only checked, never refilled**.
A missing snapshot day cannot be re-collected, and "refilling" it would write today's
data into yesterday's partition — fabricating history rather than repairing it.

## Content integrity

Existence checking is blind to content: a shard can be present, the right size, and still
have a row repeated and a row missing. `dag_audit_bronze` therefore runs a second task
over the same window with two checks that are **not alternatives**:

| | finds | misses |
|---|---|---|
| **B** primary key unique within a shard | repeated rows | dropped rows |
| **C** Bronze row count vs the upstream `count(*)` | drops *and* repeats | rewritten values |

One page-boundary slip repeats a row *and* drops one, and the two cancel out in the row
count — C alone would call that day clean, B alone would never see the drop. Check C
exempts the most recent few days, because 311's recent counts legitimately grow.

**A finding does not fail the task.** Bronze is immutable, so the audit reports a
re-pull list and the re-pull runs from the CLI under a human. What does fail the task is
a check that could not run at all — that is the audit being broken, not the data. The
same checks run from the CLI:

```bash
python -m scripts.profiling.bronze_integrity_audit --full
```

## Rules

- Never overwrite a Bronze file.
- Use `execution_date`, never `datetime.now()`, for window logic.
- Every DAG sets `retries=3`, `retry_delay=5min`, and a failure callback.
- Socrata-backed DAGs implement a 7-day lookback for late-arriving facts.

## Related

- [Backfill](backfill.md) — loading a historical window
- [Snapshot Collection](snapshot-collection.md) — the unreplayable path
- [Silver ETL](silver-etl.md) — what happens to this data next
