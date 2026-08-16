# Getting Started

This page sets up a **development** environment: the repository, the quality gates, and
the Airflow + Spark stack. Deploying the daily snapshot collector on the storage node is
a separate procedure — see [Snapshot Collection](snapshot-collection.md).

There is no end-user application yet. Everything today is run from the CLI or the Airflow
UI; a dashboard arrives with the Gold layer.

## Requirements

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (package manager, lockfile at `uv.lock`)
- Docker + Docker Compose, for the Airflow / Spark stack
- Access to a MinIO (or any S3-compatible) bucket and its credentials
- **A reachable Trino + Hive Metastore**, if you intend to create or query
  Silver/Gold tables — see [External dependencies](#external-dependencies) below

### External dependencies

Trino, Hive Metastore and Superset are **not part of this repository's Compose stack**.
They are platform-level shared services on the compute node, at the same level as
Hadoop or Kafka, and may be shared with other projects. Rationale:
[ADR 0006](../dev/adr/0006-storage-compute-query-stack.md) §9.

The consequence is explicit and has to be accepted: **`make stack-up` brings up Airflow
and Spark, but you still cannot create a table.** This repository can no longer be
brought up on its own. Bronze ingestion works without them; anything touching
`sql/ddl/` does not.

| Service | Must be | Used by |
|---|---|---|
| Hive Metastore | running and reachable on its Thrift port | table metadata for Silver and Gold |
| Trino | running, with a `hive` catalog configured against MinIO — **path-style addressing**, since MinIO has no virtual-host DNS | all DDL and Gold SQL |
| Superset | optional | BI on top of Trino |

Their credentials live in the platform-side catalog properties, **never in this
repository**. The connection parameters this repository does need go in `.env`:

| Variable | Purpose |
|---|---|
| `TRINO_HOST` | Compute-node address. **Required, no default** — a default would turn "forgot to configure it" into "silently connected to another instance" |
| `TRINO_PORT` | Host port the Trino container is mapped to |
| `TRINO_USER` / `TRINO_CATALOG` | Trino identity and catalog (`hive`) |

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
| `S3_ENDPOINT_URL` | MinIO **API** port (9000), not the console (9001) |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` | Object-storage credentials |
| `S3_BUCKET_NAME` | Target bucket for Bronze, Silver and Gold |
| `S3_REGION` | Any value; MinIO ignores it, but SigV4 signing requires one |
| `SOCRATA_APP_TOKEN` | Raises the Socrata rate limit; optional but recommended |
| `SNAPSHOT_ALERT_WEBHOOK_URL` / `SNAPSHOT_WATCHDOG_URL` | Snapshot collection alerting — see [Snapshot Collection](snapshot-collection.md) |

`.env` is for local development only and is never committed. In a deployed environment
these are injected as environment variables.

Requests to MinIO are always **path-style** — MinIO has no virtual-host DNS. All project
code goes through `build_s3_client()`, which sets this; anything that constructs its own
client will fail with signature or `NoSuchBucket` errors.

## Quality gates

Both must pass before any change is considered done:

```bash
make lint
```

```bash
make test-unit
```

`make lint` runs ruff (Python) and sqlfluff (SQL, Trino dialect pinned in `.sqlfluff`) and
must produce zero warnings. `make test-unit-offline` runs the same suite excluding the one
test that calls a live upstream API — use it without network access.

Integration tests need real object storage:

```bash
make test-integration
```

They **skip silently when `S3_*` is unset**, so a green run without those variables proves
nothing about the storage path.

## Run the stack

```bash
git pull && make stack-up
```

Run it from the repository root. Use the `make` target rather than a bare
`docker compose -f infra/docker/docker-compose.yml up -d`: `-f` sets the Compose *project
directory* to `infra/docker/`, and `${VAR}` interpolation reads `.env` from there — a file
that does not exist — so every `${VAR:?}` in the compose file aborts the command before
anything starts. The target passes `--env-file .env` to point interpolation at the root
`.env`. (`--project-directory` would fix that too, but it also renames the Compose project
and orphans your existing containers and volumes.) `make stack-cmd` prints the exact
command if you need to run a Compose subcommand by hand.

This brings up Airflow (scheduler, webserver, DAG processor) plus `spark-master` and
`spark-worker` on a shared Docker network. MinIO runs on the separate storage node and is
not part of this compose file.

> **After changing Python code, restart Airflow.** A `git pull` alone is not enough — the
> scheduler forks tasks from its in-memory state, so it keeps running the old code until
> restarted:
>
> ```bash
> git pull && make stack-restart-airflow
> ```

## Run one job

```bash
make spark-submit JOB=spark/jobs/etl_open_meteo.py
```

```bash
make dag-trigger DAG=<dag_id>
```

## Next

- [Overview](overview.md) — what the platform is for
- [Architecture](architecture.md) — how the layers and nodes fit together
- [Backfill](backfill.md) — loading historical data
- [Operations](operations.md) — schedules, failures, recovery
