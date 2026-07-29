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
GCS Bronze                       ← bronze/raw/{sid}/{ds}/{YYYY-MM}/data_{date}.json
```

**Rule**: business logic lives only in the facade and bulk layers. Per-source scripts
and DAG files are pure dispatch — no API calls, no date arithmetic inline.

---

## Dispatch by partition strategy

Each source YAML (`config/sources/*.yaml`) declares `partition_strategy`.
`bulk.py` and `_common.py` dispatch on it:

| strategy | source | bulk function | facade method |
|---|---|---|---|
| `daily` + socrata | SRC-NYC-311 | `backfill_daily_window` (per-day loop) | `upload_day(date)` |
| `daily` + open_meteo | SRC-Open-Meteo | `backfill_daily_window` (1 wide call) | `upload_window(start, end)` |
| `monthly` | SRC-NYPD | `backfill_monthly_window` | `upload_month(date)` |
| `static` | SRC-DCP | `backfill_static` | `upload_static()` |

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
# Daily source (311), upload mode
python -m scripts.backfill.main --source SRC-NYC-311 \
    --start 2024-01-01 --end 2025-01-01 --bucket nyc-uoip-prod

# Monthly source (NYPD), dry-run
python -m scripts.backfill.main --source SRC-NYPD \
    --start 2024-01-01 --end 2025-01-01 --dry-run

# Static (DCP), upload
python -m scripts.backfill.main --source SRC-DCP \
    --start 2024-01-01 --end 2024-01-01 --bucket nyc-uoip-prod
```

`--bucket` falls back to `GCS_BUCKET_NAME` env var. `--dry-run` calls `fetch_*`
instead of `upload_*`, no GCS writes.

---

## Calling bulk functions from a DAG (copy-paste pattern)

```python
from scripts.backfill.bulk import backfill_daily_window, backfill_monthly_window, backfill_static
from datetime import date

# 311
results = backfill_daily_window("SRC-NYC-311", start=date(2024,1,1), end=date(2025,1,1), bucket="nyc-uoip-prod")

# NYPD
results = backfill_monthly_window("SRC-NYPD", start=date(2024,1,1), end=date(2025,1,1), bucket="nyc-uoip-prod")

# Open-Meteo (1 wide call, returns list of 1 BulkResult)
results = backfill_daily_window("SRC-Open-Meteo", start=date(2024,1,1), end=date(2025,1,1), bucket="nyc-uoip-prod")

# DCP (static, no dates)
results = backfill_static("SRC-DCP", bucket="nyc-uoip-prod")
```

`BulkResult` fields: `document` (date|None), `status` ("ok"|"failed"), `manifest_count`, `error`.
Failures on one slice do **not** abort others — check `any(r.status=="failed" for r in results)`.

---

## DAG status (as of 2026-07-24)

13 DAGs in `dags/`, in four groups:

**Bronze backfill — manual, `schedule=None`, Params-driven**
- `dag_backfill_nyc_311.py` — daily, Socrata
- `dag_backfill_nypd.py` — monthly, Socrata
- `dag_backfill_open_meteo.py` — daily, wide-fetch (max 365-day window per run)
- `dag_backfill_dcp.py` — static (no date params needed)

**Bronze incremental — scheduled, `catchup=True`**
- `dag_ingest_nyc_311.py` — `0 6 * * *`
- `dag_ingest_open_meteo.py` — `0 6 * * *`
- `dag_ingest_nypd.py` — `0 6 1 * *` (monthly)
- `dag_ingest_dcp.py` — `schedule=None` (static source, refresh on demand)

**Bronze audit / self-heal**
- `dag_audit_bronze.py` — `0 8 * * *`, `catchup=False`. Scans GCS manifests over a
  rolling window (14 days / 3 months), calls `bulk.py` directly to fill any gap,
  raises if a gap can't be filled. Excludes SRC-DCP.

**Silver (Spark)** — see `docs/01-architecture/decisions/week3-Silver-Execution-Architecture.md`
- `dag_silver_open_meteo.py` — `0 7 * * *`, `catchup=True`, 7-day sliding lookback
- `dag_backfill_silver_open_meteo.py` — manual, arbitrary `[start, end)`
- `dag_backfill_silver_dcp.py` — manual, static full overwrite

Shared helpers: `dags/_dag_common.py` (DEFAULT_ARGS, backfill_params, get_bucket)
and `dags/_spark_common.py` (GCS_CONNECTOR_JAR, SPARK_CONF) for the Silver DAGs.
DAG import test: `tests/unit/test_dag_imports.py` (skips if airflow not installed locally).

Design: 1 DAG Run = 1 time window. Airflow does NOT slice — `bulk.py` does
(Bronze) / the Spark job's `[start, end)` window does (Silver).

**Not yet built**: Silver for 311 and NYPD, and every Gold-layer DAG.

---

## Cloud Composer deployment (Phase 1) — declared but NOT provisioned

Infra: `google_composer_environment.main` is declared in `infra/terraform/main.tf`
but is **not in `terraform.tfstate`** — it was never applied, or was destroyed.
Nothing is billing for Composer today. Airflow currently runs on the
self-hosted Docker stack (`infra/docker/docker-compose.yml`) instead.

> ### 💸 DO NOT run a bare `make terraform-apply`
> Verified with `terraform plan` on 2026-07-24: the plan is
> **`1 to add, 0 to change, 0 to destroy`**, and the single resource to add is
> `google_composer_environment.main`. So `make terraform-apply` right now
> **provisions Composer and starts billing ~$10/day** — for an environment the
> project doesn't currently use.
>
> If you only want the other resources, target them explicitly. If you do
> provision Composer, destroy it the moment the backfill finishes:
> `terraform destroy -target=google_composer_environment.main`

GCP project: `nyc-uoip-prod` (per `infra/terraform/terraform.tfvars`).
Bucket: `nyc-uoip-prod`, location `US-CENTRAL1`.
Note `region = "us-east1"` in tfvars applies to the service account / Composer,
**not** the bucket — bucket and BigQuery dataset both use
`var.storage_location` (default `us-central1`), so they are co-located and
BigQuery loads from the bucket will not hit a cross-region error.

> **Stale-state warning**: `google_bigquery_dataset.main` in `terraform.tfstate`
> lives in the OLD project `pace-lab-bdp` (`projects/pace-lab-bdp/datasets/nyc_uoip`)
> while every other resource is in `nyc-uoip-prod`. Terraform shows **no diff**,
> because the resource block never sets `project`, so it silently keeps the
> state value.
>
> This matters for the Gold layer: `google_project_iam_member.bigquery_data_editor`
> and `..._job_user` grant the service account BigQuery rights on
> **`nyc-uoip-prod` only**, so writes to `pace-lab-bdp.nyc_uoip` will fail with a
> permissions error. Old exploration SQL in
> `docs/00-requirements/domain-knowledge/week3-explore_raw_data.md` also still
> references `pace-lab-bdp.explore.*`.
>
> Fix before building Gold: add `project = var.project_id` to
> `google_bigquery_dataset.main`. ⚠️ `project` forces replacement, so plan it
> first and confirm the dataset is empty — applying will drop and recreate it.

Deploy workflow:
```bash
make terraform-apply      # provision (~20 min first time)
make deploy-composer      # sync dags/ + ingestion/ + scripts/ + config/ to Composer GCS
# Then trigger in Airflow UI (URL: terraform output composer_airflow_uri)
# {"start": "2024-01-01", "end": "2025-01-01", "bucket": "nyc-uoip-prod"}
```

Composer adds `gs://<bucket>/plugins/` to PYTHONPATH automatically.
Our packages (ingestion/, scripts/, config/) land there via `deploy-composer`.

Env vars injected via Terraform: GCS_BUCKET_NAME, SOCRATA_APP_TOKEN, DEPLOYMENT_PHASE=1.
Set `socrata_app_token` in `terraform.tfvars` (not committed).
