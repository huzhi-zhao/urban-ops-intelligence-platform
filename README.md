# Urban Operations Intelligence Platform (UOIP)

A city-agnostic Lakehouse pipeline that ingests municipal open data — service
requests, traffic collisions, weather, administrative boundaries — and produces a
daily **Operational Load Score** with resource-allocation recommendations per district.

> **What it answers.** A city's 311 call centre needs to know: where will service
> requests spike tomorrow? Which districts need more ambulances? Should we staff up
> the heating-complaint queue before a snowstorm? UOIP answers those from data,
> on a schedule, without anyone opening a spreadsheet.

The platform is not tied to one city. City-specific facts live in configuration
(`config/sources/*.yaml` plus a boundary dataset), not in pipeline code.
The current deployment runs on **New York City** open data.

## Architecture

```
Open data APIs (Socrata / Open-Meteo)
         ↓
Ingestion (Airflow — incremental pull + self-healing audit)
         ↓
Bronze (NDJSON)  →  Silver (Parquet)  →  Gold (warehouse tables)
                                              ↓
                                    Operational Intelligence Engine
                                              ↓
                                    Recommendations / Dashboard
```

![Architecture](docs/images/platform-architecture.svg)

## Status

| Layer | State |
|---|---|
| **Bronze** — raw NDJSON in object storage | ✅ Complete. 4 sources, incremental + backfill + daily self-healing audit |
| **Silver** — cleaned Parquet | 🟡 2 of 4 sources (weather, boundaries) |
| **Gold** — warehouse star schema | ❌ Not started |
| **Intelligence & recommendations** | ❌ Not started |
| **Dashboard / CI** | ❌ Not started |

## Quick start

```bash
make install
```

```bash
make lint && make test-unit
```

```bash
docker compose -f infra/docker/docker-compose.yml up -d
```

Full setup, configuration and troubleshooting: [Getting Started](docs/guide/getting-started.md).

## Documentation

| Guide | What it covers |
|---|---|
| [Getting Started](docs/guide/getting-started.md) | Install, configure, run the stack, quality gates |
| [Architecture](docs/guide/architecture.md) | Layers, components, execution model, deployment phases |
| [Data Sources](docs/guide/data-sources.md) | Registered sources, known data issues, adding a source or a city |
| [Ingestion & Bronze](docs/guide/ingestion-bronze.md) | Partition strategies, manifests, scheduled DAGs, self-healing |
| [Silver ETL](docs/guide/silver-etl.md) | Spark job contract, output layout, adding a source |
| [Backfill](docs/guide/backfill.md) | Loading historical windows from CLI or Airflow |
| [Operations](docs/guide/operations.md) | Runbook: schedules, failures, recovery, cost controls |

Developer documentation — requirements, design intent, and architecture decision
records — lives under [docs/dev/](docs/dev/README.md) and is written in Chinese.

## Repository layout

```
ingestion/      API clients, per-source fetchers, object-storage loaders
spark/          PySpark jobs, reusable transforms, Silver schemas
sql/            DDL, incremental DML, intelligence SQL (not yet created)
dags/           Airflow DAGs — scheduling only, no business logic
scripts/        Backfill CLI entry points
config/         Source registry (YAML, Pydantic-validated)
contracts/      Data contracts
infra/          Terraform (cloud) and Docker Compose (self-hosted)
tests/          Unit tests and fixtures
docs/           guide/ (English, outward-facing) and dev/ (Chinese, developer)
```

## Conventions

- **ETL jobs are idempotent** — re-running an execution date produces identical output
- **DAGs contain scheduling only** — business logic lives in `ingestion/` or `spark/`
- **All timestamps are UTC**
- **Bronze is immutable** — never overwrite a raw file
- **No `SELECT *` in Gold SQL** — explicit column lists only
