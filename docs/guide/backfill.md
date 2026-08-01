# Backfill

Backfill loads a historical window into a layer that is otherwise fed incrementally. It
reuses the exact same code as the scheduled path — only the window size differs, which
is why a backfilled partition and a scheduled one are byte-identical.

## Bronze backfill from the CLI

```bash
python -m scripts.backfill.main --source <SRC-ID> --start 2024-01-01 --end 2025-01-01 --bucket uoip
```

The window is half-open: `[start, end)`. `--bucket` falls back to the `S3_BUCKET_NAME`
environment variable.

| Flag | Effect |
|---|---|
| `--dry-run` | Calls the upstream API but writes nothing to storage |
| `--dataset` | Restrict a multi-dataset source to one dataset |
| `--max-workers` | Parallelism across slices (default 4 for daily, 2 for monthly; `1` = serial) |
| `--action fetch` | Return the data instead of uploading it |

Per-source scripts can also be invoked directly, with the same flags:

```bash
python -m scripts.backfill.backfill_wpg_snow --start 2026-07-01 --end 2026-07-02 --dry-run
```

Registered source ids are listed in [Data Sources](data-sources.md) §2 and are always
readable from `config/sources/`.

### Always dry-run first

A dry run really calls the API and logs per-slice record counts, but writes no data file
and no manifest. It verifies connectivity, rate limits, token validity and expected
volume before anything is committed to Bronze — cheap insurance before a run that may
take hours.

```bash
python -m scripts.backfill.main --source <SRC-ID> --start 2024-01-01 --end 2025-01-01 --dry-run
```

### Snapshot sources are a special case

A `snapshot` source has nothing to back-fill in the usual sense. The upstream keeps no
history, so the only day that can ever be collected is today; `--start` / `--end` are
accepted for CLI uniformity and ignored. The scheduled collection does not go through
this path at all — it runs as a timer on the storage node. This entry point exists for
manual re-runs and dry-run inspection. See [Snapshot Collection](snapshot-collection.md).

## Bronze backfill from Airflow

Backfill DAGs are manual (`schedule=None`) and take params:

```json
{"start": "2024-01-01", "end": "2025-01-01", "bucket": "uoip"}
```

**One DAG run = one window.** Airflow does not slice; `bulk.py` does. Backfilling a year
is one run, not 365. Wide-fetch sources (Open-Meteo) accept at most a 365-day window per
run, because the upstream call itself is one request.

## Silver backfill

Either trigger the corresponding `dag_backfill_silver_<dataset>` DAG, or submit the Spark
job directly with a wide `--start` / `--end`. See [Silver ETL](silver-etl.md).

## Failure behaviour

A failing slice does not abort the others. Each slice returns a result carrying its
document, status, record count and error; the caller checks whether any slice failed, and
exits non-zero if so. Re-running a completed window is safe — writes are deterministic and
idempotent, so a partial failure is repaired by simply re-running the same command.

## Related

- [Ingestion & Bronze](ingestion-bronze.md) — partition strategies and the layer contract
- [Operations](operations.md) — what to do when a scheduled run fails
