# Getting Started

## Requirements

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (package manager, lockfile at `uv.lock`)
- Docker + Docker Compose (for the Airflow / Spark stack)
- An object-storage bucket and credentials (GCS today)

## Install

```bash
make install
```

This runs `uv sync` against `uv.lock`. Development dependencies live in
`[project.optional-dependencies] dev` in `pyproject.toml`.

## Configure

Copy `.env.example` to `.env` and fill in the values:

| Variable | Purpose |
|---|---|
| `GCS_BUCKET_NAME` | Target bucket for Bronze and Silver |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to the service-account key (local development only) |
| `SOCRATA_APP_TOKEN` | Raises the Socrata rate limit; optional but recommended |
| `DEPLOYMENT_PHASE` | `1` = cloud stack, `2` = self-hosted stack |

`.env` is for local development only and is never committed. In a deployed
environment these are injected as environment variables.

## Quality gates

Both must pass before any change is considered done:

```bash
make lint
```

```bash
make test-unit
```

`make lint` runs ruff (Python) and sqlfluff (SQL) and must produce zero warnings.
`make test-unit-offline` runs the same suite excluding the one test that calls a
live upstream API — use it without network access.

## Run the stack

```bash
docker compose -f infra/docker/docker-compose.yml up -d
```

This brings up Airflow (scheduler, webserver, DAG processor) plus
`spark-master` and `spark-worker` on a shared Docker network.

> **After changing Python code, restart Airflow.** A `git pull` alone is not
> enough — the scheduler forks tasks from its in-memory state, so it keeps
> running the old code until restarted:
>
> ```bash
> docker compose -f infra/docker/docker-compose.yml restart airflow-scheduler airflow-webserver airflow-dag-processor
> ```

## Run one job

```bash
make spark-submit JOB=spark/jobs/etl_open_meteo.py
```

```bash
make dag-trigger DAG=dag_ingest_nyc_311
```

## Next

- [Architecture](architecture.md) — how the layers fit together
- [Backfill](backfill.md) — loading historical data
- [Operations](operations.md) — schedules, failures, recovery
