# Backfill Layer — Architecture Notes

> Read this before touching `scripts/backfill/`, `ingestion/backfill.py`, or any backfill DAG.

---

## Three-layer design

```
scripts/backfill/backfill_*.py   ← CLI entry points (argparse, one file per source)
        ↓ calls
scripts/backfill/bulk.py         ← window slicing + ThreadPoolExecutor
        ↓ calls
ingestion/backfill.py            ← BackfillFacade: one atomic pull+write per document
        ↓ writes
MinIO Bronze                     ← bronze/raw/{sid}/{ds}/{YYYY-MM}/data_{date}.ndjson.gz
```

**Rule**: business logic lives only in the facade and bulk layers. Per-source scripts
and DAG files are pure dispatch — no API calls, no date arithmetic inline.

---

## Dispatch by partition strategy

Each source YAML (`config/sources/*.yaml`) declares `partition_strategy`.
`bulk.py` and `_common.py` dispatch on it:

| strategy | bulk function | facade method |
|---|---|---|
| `daily` + socrata | `backfill_daily_window` (per-day loop) | `upload_day(date)` |
| `daily` + open_meteo | `backfill_daily_window` (1 wide call) | `upload_window(start, end)` |
| `monthly` | `backfill_monthly_window` | `upload_month(date)` |
| `static` | `backfill_static` | `upload_static()` |
| `snapshot` | `backfill_snapshot` | `upload_snapshot(date)` |

Which source uses which strategy is in `config/sources/*.yaml` — read it there,
never from a list in code or docs.

`snapshot` partitions by **collection date**, not record date, and streams the
payload (see `.claude/rules` note in `AGENTS.md` → Bronze partitioning strategies).
It is the only strategy that permits `timestamp_field: null`.

`_is_wide_fetch_source()` in `bulk.py` checks `cfg.datasets[0].api_type == ApiType.OPEN_METEO`
to choose between per-day slicing and the single wide-fetch path.

---

## Auto-discovery of per-source scripts

`scripts/backfill/main.py` calls `pkgutil.iter_modules` to find every `backfill_*.py`
file and imports it. Importing triggers the `@register_backfill` decorator (defined in
`_registry.py`), which populates `BACKFILL_REGISTRY`. To add a new source, drop a
`backfill_<slug>.py` file — no edits to `main.py` needed.

---

## CLI invocation pattern

```bash
# Daily source, upload mode
python -m scripts.backfill.main --source SRC-Open-Meteo \
    --start 2024-01-01 --end 2025-01-01 --bucket uoip

# Dry-run: fetch only, no object-storage write
python -m scripts.backfill.main --source SRC-Open-Meteo \
    --start 2024-01-01 --end 2025-01-01 --dry-run

# Snapshot source (--start/--end accepted but ignored)
python -m scripts.backfill.main --source SRC-WPG-SNOW \
    --start 2026-08-02 --end 2026-08-03 --bucket uoip
```

`--bucket` falls back to the `S3_BUCKET_NAME` env var. `--dry-run` calls `fetch_*`
instead of `upload_*`, no object-storage writes.

---

## Calling bulk functions from a DAG (copy-paste pattern)

```python
from scripts.backfill.bulk import (
    backfill_daily_window, backfill_monthly_window, backfill_static, backfill_snapshot,
)
from datetime import date

# daily + socrata → one query per day
results = backfill_daily_window("SRC-SOME-DAILY", start=date(2024,1,1), end=date(2025,1,1), bucket="uoip")

# monthly → one slice per month
results = backfill_monthly_window("SRC-SOME-MONTHLY", start=date(2024,1,1), end=date(2025,1,1), bucket="uoip")

# daily + open_meteo → 1 wide call, returns a list of 1 BulkResult
results = backfill_daily_window("SRC-Open-Meteo", start=date(2024,1,1), end=date(2025,1,1), bucket="uoip")

# static → no dates
results = backfill_static("SRC-SOME-STATIC", bucket="uoip")

# snapshot → today only; the upstream holds nothing else
results = backfill_snapshot("SRC-WPG-SNOW", bucket="uoip")
```

`BulkResult` fields: `document` (date|None), `status` ("ok"|"failed"), `manifest_count`, `error`.
Failures on one slice do **not** abort others — check `any(r.status=="failed" for r in results)`.

---

## DAG status (as of 2026-08-02)

5 DAGs in `dags/`. The 7 pure city-instance DAGs were deleted in batch 1 of
`docs/dev/design/20260802-city-instance-switchover.md`.

**Bronze backfill — manual, `schedule=None`, Params-driven**
- `dag_backfill_open_meteo.py` — daily, wide-fetch (max 365-day window per run)

**Bronze incremental — scheduled, `catchup=True`**
- `dag_ingest_open_meteo.py` — `0 6 * * *`

**Bronze audit / self-heal**
- `dag_audit_bronze.py` — `0 8 * * *`, `catchup=False`. Scans Bronze manifests in
  MinIO over a rolling window (14 days / 3 months), calls `bulk.py` directly to
  fill any gap, raises if a gap can't be filled.
  Which sources it audits is **derived from `config/sources/*.yaml` by
  `partition_strategy`**, not listed in the DAG. `static` sources are skipped
  (no time dimension). `snapshot` sources are **checked but never filled** —
  their upstream keeps no history, so a "fill" would file today's data under a
  past date and fabricate history rather than recover it.

**Silver (Spark)** — see `docs/dev/adr/0005-silver-execution-architecture.md`
- `dag_silver_open_meteo.py` — `0 7 * * *`, `catchup=True`, 7-day sliding lookback
- `dag_backfill_silver_open_meteo.py` — manual, arbitrary `[start, end)`

Shared helpers: `dags/_dag_common.py` (DEFAULT_ARGS, backfill_params, get_bucket)
and `dags/_spark_common.py` (S3A_JARS, SPARK_CONF) for the Silver DAGs.
DAG import test: `tests/unit/test_dag_imports.py` (skips if airflow not installed locally).

Design: 1 DAG Run = 1 time window. Airflow does NOT slice — `bulk.py` does
(Bronze) / the Spark job's `[start, end)` window does (Silver).

**DAG count discipline**: backfill stays on the CLI (it is the three-layer
architecture's own entry point). Only *active* sources get an ingest DAG —
copying the "one backfill DAG + one ingest DAG per source" pattern across 5–6
sources would produce 12 DAGs for no benefit.

**Not yet built**: Silver for the Winnipeg sources, and every Gold-layer DAG.

