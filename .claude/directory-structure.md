---
name: directory-structure
description: Full repository layout — every key path, file purpose, and phase annotation
metadata:
  type: reference
---

# NYC-UOIP — Directory Structure Reference

> ⚠️ **本文描述的是目标形态，不少路径尚未创建**（`sql/`、`.github/`、
> `spark/quality/`、`ingestion/schemas/`、`contracts/consumer-contracts/` 等）。
> 真实实现进度以 `CLAUDE.md` 的 Implementation status 一节为准。
> `docs/` 一节例外——它描述的是 2026-07-28 重构后的真实结构。

## Documentation (`docs/`) — 真实结构

文档只有两类，不混放。详细规则见 `CLAUDE.md` 的 Documentation conventions。

```
README.md                根 README，English，只按功能链接 docs/guide/
docs/README.md           两桶索引 + 写作规则
docs/guide/              对外操作手册 · English only
  getting-started.md     安装、配置、质量门禁、起停服务
  architecture.md        分层、组件职责、执行模型、部署阶段
  data-sources.md        数据源登记表、接入新源/新城市
  ingestion-bronze.md    Bronze 布局、分区策略、增量与自愈
  silver-etl.md          Silver 作业规范与进度
  backfill.md            CLI 与 DAG 回填
  operations.md          Runbook：排期、故障、恢复、成本
docs/dev/                开发文档 · 中文可
  README.md
  requirements/          project-overview.md · business-objectives.md
  architecture/          platform-architecture.md · roadmap.md
  adr/                   README.md(索引) + 0001…0005，不改名不删除
  notes/                 领域知识与踩坑笔记
docs/images/             在用图，文件名不含城市名
```

约定：目录名用语义不用数字前缀（数字只给 ADR 编号）；文件名一律
English kebab-case，语言差异只体现在正文；每篇文档被 `docs/README.md`
恰好链接一次；表述保持城市无关（平台叫 UOIP，城市是配置维度）。

## Root-level config files

| Path | Purpose |
|---|---|
| `CLAUDE.md` | Claude Code agent instructions (project-specific overrides) |
| `AGENTS.md` | Shared AI agent conventions (all tools/Copilot/Codex) |
| `Makefile` | Dev commands: `lint`, `test-unit`, `test-integration`, `spark-submit`, `dag-trigger` |
| `pyproject.toml` | Python deps (uv), ruff + pytest config |
| `.env.example` | Env var template — never commit `.env` |
| `datacontract.yaml` | Open Data Contract Standard entry point |

## Ingestion layer

### Airflow DAGs (`dags/`)
- `dag_ingest_nyc_311.py` — incremental pull, pagination, GCS/MinIO Bronze write
- `dag_ingest_nypd_collisions.py` — 7-day lookback for late-arriving facts
- `dag_ingest_open_meteo.py` — daily snapshot, 7-day forecast + 3-day history
- `dag_ingest_borough_boundaries.py` — one-time static GeoJSON load
- `dag_etl_bronze_to_silver.py` — triggers Spark job after ingestion
- `dag_etl_silver_to_gold.py` — BigQuery / Trino Gold layer load
- `dag_intelligence_engine.py` — daily load score + recommendation calculation
- `dags/operators/` — custom Airflow operators/sensors (`socrata_to_gcs_operator.py`, `dataproc_spark_operator.py`)

**Rule**: DAGs contain scheduling logic ONLY. No business logic inline.

### Python API clients (`ingestion/clients/`)
- `socrata_client.py` — pagination, App Token, rate-limit retry
- `open_meteo_client.py`
- `nyc_open_data_client.py` — GeoJSON static download

### Loaders (`ingestion/loaders/`)
- `gcs_loader.py` — Phase 1
- `minio_loader.py` — Phase 2

### Raw schemas (`ingestion/schemas/`)
Pydantic models validating raw API response shape before write.
- `nyc_311_raw_schema.py`
- `nypd_collisions_raw_schema.py`
- `open_meteo_raw_schema.py`

## Spark ETL (`spark/`)

### Jobs (`spark/jobs/`)
One PySpark entry-point per dataset.
- `etl_nyc_311.py`
- `etl_nypd_collisions.py`
- `etl_open_meteo.py`

### Reusable transforms (`spark/transforms/`)
Importable by jobs — **never add new util functions inside `jobs/`**.
- `deduplication.py` — hash-key dedup + Iceberg MERGE INTO (P2)
- `timestamp_normalizer.py` — EST→UTC, string→timestamp
- `geo_enrichment.py` — borough fill via zip lookup + ST_CONTAINS
- `complaint_standardizer.py` — lower-case, strip, category mapping

### Silver schemas (`spark/schemas/`)
StructType definitions for Silver layer.
- `silver_311_schema.py`
- `silver_collisions_schema.py`
- `silver_weather_schema.py`

### Data quality (`spark/quality/`)
- `expectations_311.json` — Great Expectations
- `deequ_checks_collisions.py` — Phase 2

## SQL / Warehouse (`sql/`)

### DDL (`sql/ddl/`)
CREATE TABLE statements — run once at setup.
- `bigquery/` (Phase 1): `fact_311_requests.sql`, `fact_vehicle_collisions.sql`, `dim_date.sql`, `dim_geography.sql`, `dim_weather_forecast.sql`
- `iceberg/` (Phase 2): Iceberg DDL via Trino / Spark SQL

### DML (`sql/dml/`)
Daily incremental loads — MERGE / INSERT OVERWRITE.
- `load_fact_311.sql`
- `load_fact_collisions.sql`
- `spatial_borough_fill.sql` — ST_CONTAINS join to back-fill `borough_id`

### Intelligence SQL (`sql/intelligence/`)
Operational scoring + recommendation rules.
- `calc_load_score.sql` — `0.4 × 311 + 0.4 × collision + 0.2 × weather`
- `calc_operational_drivers.sql`
- `calc_resource_recommendations.sql`
- `populate_daily_summary.sql` — writes to `fact_daily_operational_summary`

## Infrastructure (`infra/`)

### Terraform — GCP (Phase 1)
- `infra/terraform/main.tf` — GCS buckets, BigQuery datasets, IAM, Composer env
- `infra/terraform/variables.tf`
- `infra/terraform/outputs.tf`

### Docker — Self-hosted (Phase 2)
- `infra/docker/docker-compose.yml` — Airflow + Spark + Trino + MinIO + Hive Metastore
- `infra/docker/airflow.Dockerfile`
- `infra/docker/spark.Dockerfile`

## Data contracts (`contracts/`)

- `contracts/source-registry.md` — SRC-NYC-311 / SRC-NYPD / SRC-Open-Meteo / SRC-DCP authority list
- `contracts/api-contracts/` — JSON Schema per upstream API response
- `contracts/consumer-contracts/` — Gold layer contract (`gold-layer-contract.yaml`)

## Tests (`tests/`)

- `tests/unit/` — pure Python, no Spark cluster needed (`test_socrata_client.py`, `test_timestamp_normalizer.py`, `test_deduplication.py`)
- `tests/integration/` — requires local Spark/BigQuery sandbox
- `tests/fixtures/` — sample JSON files, mock API responses

## CI/CD (`.github/`, `.pre-commit-config.yaml`)

- `.github/workflows/ci.yml` — ruff + unit tests on every PR
- `.github/workflows/deploy-dags.yml` — push DAGs to GCS/Composer on merge to main
- `.pre-commit-config.yaml` — ruff, sqlfluff, conventional commits hook

## Phase annotations in directory structure

| Badge | Meaning |
|---|---|
| `Phase 1` | GCP Demo only (BigQuery, Dataproc, GCS, Composer) |
| `Phase 2` | Self-built cluster only (MinIO, Iceberg, Trino, Docker) |
| `Phase 1+2` | Shared across both phases |