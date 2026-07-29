# Architecture

UOIP is a medallion-architecture Lakehouse. Each layer has one job, one storage
format, and one set of guarantees.

![End-to-end architecture](../images/platform-architecture.svg)

## Layers

| Layer | Storage | Format | Guarantees |
|---|---|---|---|
| **Bronze** | Object storage (GCS today, S3/MinIO compatible) | NDJSON, one file per partition + a JSON manifest | Immutable. Never overwritten. Byte-for-byte what the API returned. |
| **Silver** | Object storage | Parquet, partitioned by date | Schema-enforced, deduplicated, all timestamps UTC. Rejected rows are kept under `silver/_rejects/`. |
| **Gold** | Data warehouse (BigQuery today, Iceberg/Trino planned) | Managed tables, star schema | Partitioned by date, clustered by district. Not yet implemented. |

Every job is **idempotent**: re-running the same execution date produces the same
output, with no duplicates. Bronze re-writes the same deterministic path; Silver
uses dynamic partition overwrite; Gold will use `MERGE` / `INSERT OVERWRITE PARTITION`.

## Components

| Component | Role | Explicitly not responsible for |
|---|---|---|
| **Airflow** | Scheduling, parameter rendering, retries, alerting | Reading or writing data; it never touches a record |
| **Ingestion package** (`ingestion/`) | API clients, per-source fetchers, object-storage loaders | Scheduling, date arithmetic |
| **Spark Standalone** (`spark-master` / `spark-worker`) | All Bronze → Silver compute | Knowing which dataset it is running |
| **Warehouse** | Gold modelling, spatial joins, scoring SQL | Ingestion |

A key design rule: **one DAG run = one time window**. Airflow does not slice work.
Slicing happens inside `scripts/backfill/bulk.py` (Bronze) or inside the Spark
job's `[start, end)` window (Silver). Backfilling a year is one DAG run, not 365.

### Execution model for Silver

Spark runs in `client` deploy mode. That means the Spark **driver** process lives
inside the Airflow scheduler container (`SparkSubmitOperator` forks `spark-submit`
there), and only the **executors** run on `spark-worker` and touch data.

```
Airflow scheduler ──spark-submit──▶ Spark master ──▶ Spark worker (executors)
      │                                                      │
      └── driver process lives here                          └── reads Bronze,
                                                                 writes Silver
```

## Deployment phases

| Phase | Stack | Status |
|---|---|---|
| **Phase 1** | GCS · Spark Standalone (Docker) · BigQuery · Airflow (Docker) | Active |
| **Phase 2** | MinIO · Spark + Iceberg · Trino · Airflow (Docker) | Planned |

Select with the `DEPLOYMENT_PHASE` environment variable (`1` or `2`).

> Phase 1 originally targeted GCP Dataproc for compute and Cloud Composer for
> orchestration. Both were dropped — Dataproc node registration proved unreliable,
> and Composer costs roughly $10/day for an environment the project does not need.
> Compute and orchestration are self-hosted in Docker; **storage stays on GCS**.

## City-agnostic by design

The platform is not tied to a single city. City-specific facts live in
configuration, not code:

- **Source definitions** — `config/sources/*.yaml` (endpoint, dataset ids,
  timestamp field, partition strategy)
- **Geographic dimension** — a boundary file per city, loaded into the geography
  dimension and used for spatial attribution

The current production deployment uses New York City open data. Adding a city
means adding source YAML files and a boundary dataset, not changing pipeline code.
See [Data Sources](data-sources.md).

## Related

- [Ingestion & Bronze](ingestion-bronze.md) · [Silver ETL](silver-etl.md) · [Operations](operations.md)
