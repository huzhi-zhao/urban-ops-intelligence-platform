# Backfill

Backfill loads a historical window into a layer that is otherwise fed
incrementally. It reuses the exact same code as the scheduled path — only the
window size differs.

## Bronze backfill from the CLI

```bash
python -m scripts.backfill.main --source SRC-NYC-311 --start 2024-01-01 --end 2025-01-01 --bucket nyc-uoip-prod
```

`--bucket` falls back to the `GCS_BUCKET_NAME` environment variable.
The window is half-open: `[start, end)`.

| Source | Example |
|---|---|
| `SRC-NYC-311` (daily) | `--source SRC-NYC-311 --start 2024-01-01 --end 2025-01-01` |
| `SRC-NYPD` (monthly) | `--source SRC-NYPD --start 2024-01-01 --end 2025-01-01` |
| `SRC-Open-Meteo` (daily, one wide fetch) | `--source SRC-Open-Meteo --start 2024-01-01 --end 2025-01-01` |
| `SRC-DCP` (static) | `--source SRC-DCP --start 2024-01-01 --end 2024-01-01` |

Per-source scripts can also be invoked directly:

```bash
python -m scripts.backfill.backfill_nypd --start 2026-05-01 --end 2026-06-01 --bucket nyc-uoip-prod --dataset nypd_collisions
```

Useful flags:

| Flag | Effect |
|---|---|
| `--dry-run` | Calls the upstream API but writes nothing to storage |
| `--dataset` | Restrict a multi-dataset source to one dataset |
| `--max-workers` | Parallelism across slices |
| `--action fetch` | Read back what was written instead of uploading |

### Always dry-run first

```bash
python -m scripts.backfill.backfill_nypd --start 2026-05-01 --end 2026-06-01 --bucket nyc-uoip-prod --dry-run
```

A dry run really calls the API and logs per-slice record counts, but writes no
data file and no manifest. It verifies connectivity, rate limits, token validity
and expected volume before you commit anything to Bronze.

## Bronze backfill from Airflow

Four manual DAGs (`schedule=None`) take params:

```json
{"start": "2024-01-01", "end": "2025-01-01", "bucket": "nyc-uoip-prod"}
```

`dag_backfill_nyc_311` · `dag_backfill_nypd` · `dag_backfill_open_meteo` · `dag_backfill_dcp`
(`dag_backfill_dcp` needs no dates.) `dag_backfill_open_meteo` accepts at most a
365-day window per run.

**One DAG run = one window.** Airflow does not slice; `bulk.py` does. Backfilling
a year is one run, not 365.

## Silver backfill

`dag_backfill_silver_open_meteo` (arbitrary window) and `dag_backfill_silver_dcp`
(full overwrite), or submit the job directly with wide `--start` / `--end`.
See [Silver ETL](silver-etl.md).

## Failure behaviour

A failing slice does not abort the others. Each slice returns a result carrying
its document, status, record count and error; the caller checks whether any slice
failed. Re-running a completed window is safe — writes are deterministic and
idempotent.
