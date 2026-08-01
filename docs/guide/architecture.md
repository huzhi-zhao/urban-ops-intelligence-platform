# Architecture

UOIP is a medallion-architecture Lakehouse running on a **fully self-hosted stack** —
no managed cloud components anywhere. Each layer has one job, one storage format, and
one set of guarantees.

![End-to-end architecture](../images/platform-architecture.svg)

New to the project? Read [Overview](overview.md) first — it explains what the platform
is for. This page explains how it is built and why.

---

## 1. The stack

| Concern | Technology |
|---|---|
| Object storage | MinIO (S3 protocol) |
| Compute | Spark 3.5.1 Standalone (Docker) |
| Orchestration | Airflow (Docker, LocalExecutor) |
| Metadata | Hive Metastore (MySQL backend) |
| Query | Trino — **the only SQL dialect in the repository** |
| Table format | Hive-partitioned Parquet, with Iceberg as a later step |
| BI | Superset |
| Monitoring | Airflow alerting, plus an external watchdog for snapshot collection |

## 2. Layers

| Layer | Storage | Format | Guarantees |
|---|---|---|---|
| **Bronze** | MinIO | gzipped NDJSON (`.ndjson.gz`) + an uncompressed JSON manifest per file | Immutable. Never overwritten. Byte-for-byte what the API returned |
| **Silver** | MinIO | Parquet, partitioned by date | Schema-enforced, deduplicated, all timestamps UTC. Rejected rows kept under `silver/_rejects/` |
| **Gold** | MinIO, registered in Hive Metastore, queried by Trino | Hive-partitioned Parquet, star schema | Partitioned by date, clustered by zone. Not yet implemented |

Every job is **idempotent**: re-running the same execution date produces the same
output with no duplicates. Bronze writes the same deterministic path; Silver uses
dynamic partition overwrite; Gold will use `MERGE` or `INSERT OVERWRITE PARTITION`.

Layer boundaries are hard rules: **Airflow never touches data, Spark never schedules,
SQL never ingests.** Business logic in a DAG file is an architecture violation.

## 3. Components

| Component | Role | Explicitly not responsible for |
|---|---|---|
| **Airflow** | Scheduling, parameter rendering, retries, alerting | Reading or writing data; it never touches a record |
| **Ingestion package** (`ingestion/`) | API clients, per-source fetchers, object-storage loaders | Scheduling, date arithmetic |
| **Snapshot collector** (`ingestion/snapshot/`) | Daily collection of overwrite-in-place upstreams | Anything replayable — that belongs in Airflow |
| **Spark Standalone** | All Bronze → Silver compute | Knowing which dataset it is running |
| **Trino + Hive Metastore** | Gold modelling, spatial joins, scoring SQL | Ingestion |

## 4. Deployment topology

Storage and compute run on **two separate nodes**:

| Node | Runs | Nature |
|---|---|---|
| **Storage node** | MinIO (dedicated, 100 GB / 90 GB usable); the snapshot collector timer | The **sole source of truth**. Not rebuildable |
| **Compute node** | Airflow · Spark · Trino · Hive Metastore · Superset (4 core / 24 GB ARM) | **Stateless. Rebuilding it loses no data** |

The split follows the **availability boundary, not performance**. Rebuilding the
compute node costs nothing; the storage node holds history that cannot be
re-acquired. Traffic between them is plain S3 over the LAN (`s3a://`), which at this
volume (1–2 GB per job) is nowhere near a bottleneck.

That boundary produces one general rule, worth stating on its own:

> **An ingestion task that cannot be replayed must not depend on a component that is
> designed to be thrown away and rebuilt.**

There is **no backup and no replica** of the storage node. That is an accepted risk,
not an oversight: everything except the snapshot archive can be re-fetched from
upstream, and the deliverable is a paper rather than a live service. The decision, and
the condition for revisiting it, is in
[ADR 0006](../dev/adr/0006-storage-compute-query-stack.md) §2.1.1.

## 5. Two ingestion paths

Most sources are replayable — any past window can be re-fetched at will, so they run as
scheduled Airflow DAGs with retries, catchup and a self-healing audit.

One is not. Address-level snow clearing status is published as an overwrite-in-place
snapshot with no time field at all: a day not collected is gone forever. Following the
rule above, it runs as a **standalone timer on the storage node**, outside Airflow, with
its own failure notification and an external dead-man watchdog.
See [Snapshot Collection](snapshot-collection.md).

```
replayable sources ──▶ Airflow (compute node) ──s3a──▶ Bronze
snapshot source    ──▶ timer   (storage node) ──local─▶ Bronze
```

## 6. One DAG run = one time window

Airflow does not slice work. Slicing happens inside `scripts/backfill/bulk.py`
(Bronze) or inside the Spark job's `[start, end)` window (Silver). Backfilling a year
is **one** DAG run, not 365.

### Execution model for Silver

Spark runs in `client` deploy mode. The Spark **driver** lives inside the Airflow
scheduler container (`SparkSubmitOperator` forks `spark-submit` there); only the
**executors** run on `spark-worker` and touch data.

```
Airflow scheduler ──spark-submit──▶ Spark master ──▶ Spark worker (executors)
      │                                                      │
      └── driver process lives here                          └── reads Bronze,
                                                                 writes Silver
```

Object-storage credentials reach the executors through the worker's
`spark-defaults.conf` (mode 600) or injected environment variables — **never through a
`--conf` flag**, which would expose them in the Spark UI environment page, the process
list and the Airflow task log.

---

## 7. Why this stack

The short version: the workload is small (~10 GB/year), the team is one person, and the
deliverable is a paper. Every choice below optimises for *being able to finish and
explain the thing*, not for scale.

| Choice | Why | What it was chosen over |
|---|---|---|
| **Self-hosted, no cloud** | The snapshot archive (BO-7) must accumulate across a full winter. A free cloud tier expiring mid-winter would force a migration exactly when the asset is half-built | GCP (Dataproc / Composer / GCS / BigQuery). All four were dropped one by one; the last two when the credit window turned out to be shorter than the data's lifecycle |
| **MinIO** | S3 protocol, so every tool speaks it natively and the code is portable to any S3 target | Single-node HDFS on the compute node — no redundancy benefit, and it would move the source of truth into the rebuildable node |
| **Spark Standalone in Docker** | Predictable, debuggable, no cluster-provisioning failures; the same image runs locally and on the node | Managed Dataproc, which proved unreliable at node registration |
| **Trino** | The one capability actually needed later is **Iceberg writes** (`MERGE INTO` for late-arriving 311 updates). Trino's other strengths — MPP, federation, concurrency — are all unused here, but the memory cost was measured and fits | DuckDB, which is lighter but cannot write Iceberg. Kept as a local exploration tool, not for production SQL |
| **One SQL dialect (Trino)** | Two dialects in `sql/` means two things to maintain and re-verify forever | Maintaining a BigQuery/Trino pair |
| **Parquet now, Iceberg later** | "Make Trino read MinIO" and "make Iceberg work" are each a half-hour problem and together a full-day one. Sequencing them is free | Adopting Iceberg from day one |
| **Airflow** | Retries, catchup, backfill parameters and alerting are exactly the primitives batch ingestion needs, and it is worth learning as a portable skill | Cron, or managed Composer (~$10/day for an environment this project does not need) |
| **gzipped NDJSON in Bronze** | Newline-delimited is the only shape `spark.read.json()` streams; gzip is a **precondition, not an optimisation** — the daily snapshot is 184 MB raw vs 18.5 MB compressed, a 10× difference from one default | Plain JSON arrays (unloadable) or uncompressed NDJSON (67 GB/year for one source) |
| **Snapshot collection outside Airflow** | See §5 — unreplayable work must not inherit a rebuildable component's availability | A regular Airflow DAG |

Each of these is recorded in full, with rejected alternatives, in the ADRs under
[`docs/dev/adr/`](../dev/adr/README.md). The stack decision is ADR 0006.

> ⚠️ One consequence worth knowing before touching Bronze: the `.gz` **file extension is
> mandatory** and `Content-Encoding` must never be set. Spark's `s3a://` reader picks its
> decompression codec from the extension and ignores HTTP headers, so a mislabelled
> object is read as text and produces garbled rows **without raising an error**.

---

## 8. Configuration, not code

City-specific facts live in configuration and in data, never in pipeline code:

- **Sources** — `config/sources/*.yaml`: endpoints, dataset ids, timestamp field,
  partition strategy (Pydantic-validated)
- **Business vocabulary** — channel normalisation maps, the request-type dictionary,
  priority-tier commitments: configuration or Gold seed/dimension tables
- **Geography** — boundary datasets loaded into `dim_geography`

The practical test: *would this need editing to run against another city?* If yes, it is
configuration. Ingestion and shared Spark transforms use role names (service request,
work zone, administrative district); instance names (311, plow zone, ward) appear only in
SQL, source YAML and dimension tables.

This is a design discipline that keeps the pipeline honest, not a promise of
multi-city support — the platform is built and evaluated against Winnipeg.

## Related

- [Overview](overview.md) · [Data Sources](data-sources.md) · [Ingestion & Bronze](ingestion-bronze.md) · [Silver ETL](silver-etl.md) · [Operations](operations.md)
- Decision records: [`docs/dev/adr/`](../dev/adr/README.md)
