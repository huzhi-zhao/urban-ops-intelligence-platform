# Architecture

UOIP uses a layered data pipeline to make its analysis inspectable. Each stage
answers a different question: what was collected, how it was standardized, what
it means for the analysis, and how a result was calculated.

You do not need to know the full stack to follow the project. Start with the path
of one service request, then use the component references as needed.

## Follow one request

1. **Collect it.** A Python client reads a window from the public API. Bronze
   stores the records as compressed newline-delimited JSON, paired with a
   manifest containing counts and a checksum.
2. **Make it usable.** Spark reads an explicit schema, interprets the local
   timestamp, checks the interaction key and assigns usable coordinates to a
   plow zone. Silver writes typed Parquet files.
3. **Put it in context.** Gold SQL classifies the request, associates it with
   an event and zone, and aggregates it at the question's grain. Dimensions
   supply event, geographic and business context.
4. **Estimate and score.** M1 estimates event–zone request counts. Gold combines
   normalized demand, scheduled order and weather into explicit scoring profiles.
5. **Check and present it.** Independent audits reconcile layers. Figure queries
   export results with captions and certification metadata, which can then be
   rendered without querying the running platform.

A *Lakehouse* combines files in object storage with table metadata and SQL access.
The Bronze, Silver and Gold names describe increasing structure and analytical
meaning; a higher layer is not automatically more trustworthy without checks.

## Components and responsibilities

| Component | Responsibility | Repository entry point |
|---|---|---|
| Python ingestion | API access, windowed collection and manifests | [ingestion/](../../ingestion/) |
| MinIO | S3-compatible storage for records, Parquet and artifacts | Source connection settings in [.env.example](../../.env.example) |
| Spark 3.5.1 | Bronze-to-Silver transformations and point-to-zone assignment | [spark/jobs/](../../spark/jobs/) |
| Airflow | Scheduling, retries and dispatching jobs | [dags/](../../dags/) |
| Hive Metastore | Table locations, columns and partitions | [SQL DDL](../../sql/ddl/) |
| Trino | Gold transformations, label crosswalks and analytical queries | [SQL DML](../../sql/dml/) |
| M1 | Request-count estimation and holdout evaluation | [models/request_forecast/](../../models/request_forecast/) |
| Presentation tools | Figure queries and self-contained HTML for supported charts | [sql/presentation/](../../sql/presentation/), [renderer](../../scripts/presentation/render_html.py) |

Airflow invokes the code that processes data; DAG files keep business logic in
reusable modules. SQL handles the analytical relationships, while ingestion owns
API access. This separation lets a command-line run and a scheduled run share
implementation and failure behavior.

## Storage and rebuild behavior

| Layer | Format | What a rerun does |
|---|---|---|
| Bronze | `.ndjson.gz` plus JSON manifests | Writes deterministic source paths; controlled refreshes can replace a replayable window |
| Silver | Parquet with dataset-specific partitions | Replaces written date partitions, or rebuilds a reference table |
| Gold | Hive external tables over Parquet | Rebuilds selected tables through drop, storage-prefix purge, create and insert |

Bronze preserves source records without business cleansing. It is not an object
versioning system: the loader can replace a path, static sources refresh their
single file, and corrected upstream data can change a replayable window. Snapshot
history needs stricter handling because an earlier observation cannot be fetched
again. See [Ingestion and Bronze](ingestion-bronze.md).

Gold rebuilds are deterministic over unchanged inputs, but **not atomic table
swaps**. A failure can leave a table missing or incomplete until it is rebuilt.
The build runner applies dependency order and row-count gates. Readers should use
a completed, checked build rather than query tables mid-rebuild.

## Aligning geography

Spatial assignment and administrative labeling serve different purposes:

- **Point to work zone, in Silver:** assign each located request to a polygon.
  A broadcast polygon set and a cached spatial index avoid rebuilding the lookup
  for every request. Invalid boundaries are repaired before matching.
- **Work zone to administrative labels, in Gold:** aggregate winter requests
  by their zone and raw ward/neighbourhood labels. The crosswalk records each
  label's request share over three calibration seasons, not a polygon-area share.

Missing coordinates and coordinates outside all polygons have separate statuses.
That distinction makes the quality denominator meaningful: rows with no location
cannot demonstrate a broken spatial join.

The choice of plow zone as the modeling unit follows the schedule's grain.
The [crosswalk findings](results.md#a-work-zone-is-not-a-ward) show why a direct
ward-to-zone lookup would lose information. The detailed decision is recorded
in [ADR 0009](../dev/adr/0009-plow-zone-as-the-unit-of-analysis.md).

## Scheduling and execution

Most replayable time windows run through Airflow. One backfill run represents
one requested window; the ingestion layer splits it into source-appropriate
slices. Silver receives its own explicit window. A full-history load can use
several bounded runs to limit the cost of retrying.

Spark uses client deploy mode. The **driver runs inside the Airflow scheduler
container**, while executors run on the Spark worker. Both need access to object
storage. The driver plans the job and can touch storage before executors start;
credentials are injected into both environments, never passed in command flags.

The clearing-status collector runs independently, on the storage host. Its source
only exposes the current state, so every missed day loses an observation. Keeping
it separate from the rebuildable compute stack lets collection continue while
that stack is being maintained. Its watchdog also detects a run that never
started. [Snapshot Collection](snapshot-collection.md) covers deployment.

## Deployment topology

The reference deployment separates persistent object storage from compute.
That is a deployment choice, not a requirement to own two machines: a learning
installation can put the components on one computer.

| Supplied by this repository's Compose | Prepared separately, or reused from your own setup |
|---|---|
| Airflow API server, scheduler, DAG processor and metadata Postgres | Spark master |
| Project Spark worker with matching Python and transformation dependencies | MinIO or a compatible S3 endpoint |
| Project code mounts and job configuration | Hive Metastore and Trino; Superset is optional |

These are standard components. The repository connects to them over its external
Docker network, `bigdata-net`; there is no requirement to access the original
author's platform. See [Getting Started](getting-started.md#run-the-pipeline-on-your-own-infrastructure)
for preparation order, connection settings and a first-run check.

Object data and table metadata need persistence. Airflow's database also stores
run history and configuration. Recreating a compute container is different from
deleting its volumes; the latter can lose operational state even when data files
remain intact.

## Engineering lessons behind the choices

**Small data can still have expensive I/O.** An early Silver rebuild spent about
2.5 hours committing tens of thousands of objects. It was twice interrupted as
apparently stalled. Object-store rename meant copy-and-delete operations, and
the two nodes were in different data centers. The number of round trips and
latency mattered more than the data's total size. Commit tuning and bounded
scans help; placing compute near storage addresses the underlying deployment
cost. [Incident and correction](../dev/postmortem/cross-region-object-store-incident.md).

**Files and tables have separate visibility.** Spark can successfully write
Parquet while Trino still sees no new partitions. Synchronizing partition
metadata is a required handoff, not another data transformation.

**Compression is part of interoperability.** Bronze uses gzipped NDJSON so
Spark can read records line by line after decompression. The `.gz` suffix matters:
Spark's S3 reader selects the codec from it. A compressed object with the wrong
suffix can look like unreadable text. [Bronze format](ingestion-bronze.md#file-format).

**Quality needs an independent path.** Build gates stop bad output during a run;
a scheduled audit can inspect data after the run, including across layer
boundaries. It records a failed observation separately from an observation that
could not be made. [Data Quality](data-quality.md).

## Why these technologies

Spark provides explicit schemas and reusable transformations for the historical
load. Parquet supports column-oriented analytical reads. Trino and Hive Metastore
expose those files as SQL tables without adding another copy of the data. Airflow
provides window scheduling and retries; MinIO supplies the common S3 interface.

The implementation uses one SQL dialect, Trino, and Hive-partitioned Parquet.
Iceberg is a later migration, not the current table format. The self-hosted
stack replaced an earlier managed-cloud design; the decision history and
trade-offs are in [ADR 0006](../dev/adr/0006-storage-compute-query-stack.md).

Some interfaces use generic roles and source configuration, but Winnipeg is the
only implemented analytical instance. Configurable ingestion is not a claim that
another city's definitions, boundaries and schedule semantics work unchanged.

## Next steps

- [Ingestion and Bronze](ingestion-bronze.md) and [Silver ETL](silver-etl.md) follow the lower layers.
- [Scoring and Recommendations](scoring-and-recommendations.md) explains the analytical outputs.
- [Getting Started](getting-started.md) takes you from a sample figure to your own deployment.
