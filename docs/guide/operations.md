# Operations

This runbook is for a deployment you have already configured. For a new local
installation, start with [Getting Started](getting-started.md). For historical
windows, use [Backfill](backfill.md).

A healthy deployment needs more than green task boxes: inspect output coverage,
data freshness and the audit verdict for the build being used.

## What runs when

The following are the cron expressions in the repository, not a claim about
which DAGs are enabled on your machine. Airflow times use the deployment's DAG
timezone; these DAGs use naive start dates, so confirm the configured timezone
before interpreting them as Winnipeg local time.

| Job | Schedule | Purpose |
|---|---|---|
| `dag_ingest_service_requests` | `0 5 * * *` | Refresh recent 311 data |
| `dag_ingest_weather_archive` | `0 6 * * *` | Refresh weather archive |
| `dag_silver_service_request` | `0 7 * * *` | Transform recent requests and synchronize partitions |
| `dag_silver_weather_archive` | `0 7 * * *` | Transform recent weather |
| `dag_audit_bronze` | `0 8 * * *` | Coverage repair and content audit |
| `dag_dq_audit` | `30 8 * * *` | Independent audit, scorecard and certification |
| Clearing snapshot timer | 06:30 America/Winnipeg in the example unit | Preserve the day's current-state observation |
| Backfill DAGs and `dag_gold_build` | Manual | Explicit historical loads and analytical rebuilds |

Reference-table refreshes, event rebuilding, model training and figure export
are explicit operations. A later cron time does not establish a dependency on
a different DAG's successful run; check upstream completion before using outputs.
Daily Silver refreshes do not automatically refresh every Gold result.

## Refresh an analysis

Use this sequence when intentionally updating the analytical build in your own
bucket. Preserve any published exports first. Gold rebuilds drop and recreate
selected tables and purge their data prefixes; readers can see incomplete state
while a build runs or after it fails.

1. Refresh the required Bronze sources, then Silver reference inputs and request
   windows. Rebuild snowfall events from the complete intended weather series
   with the chosen event rule. Synchronize partition metadata.
2. In a new deployment, run `make ddl-create` against your configured bucket and
   metastore. This declares the tables; it does not populate them.
3. Build seed dimensions, derived dimensions, then descriptive facts:

   ```bash
   make gold-build ONLY=seeds DRY_RUN=1
   make gold-build ONLY=seeds
   make gold-build ONLY=dims
   make gold-build ONLY=facts
   ```

4. Train M1 from the populated Gold panel in its isolated environment and upload
   the resulting forecast artifacts to your configured bucket:

   ```bash
   UV_PROJECT_ENVIRONMENT=.venv-ml uv run --extra ml \
     python -m scripts.models.train_m1 --upload
   ```

   The run writes `predictions.csv` and `metrics.json` under a model-version
   directory in `var/forecast-runs/`; upload stores artifacts under
   `gold/_forecast_runs/`. Inspect the holdout metrics alongside their baseline.
   `--dump-panel` and `--panel-file` provide an offline training path after a
   real panel has been exported; they do not supply a bundled training dataset.

5. Set `MODEL_VERSION` to the artifact version you intend to serve, then build
   scoring tables explicitly:

   ```bash
   make gold-build ONLY=scoring FORECAST_VERSION="$MODEL_VERSION" DRY_RUN=1
   make gold-build ONLY=scoring FORECAST_VERSION="$MODEL_VERSION"
   ```

6. Run build assertions and the relevant independent audit. Inspect its scorecard
   and record certification for that run. Then export new figure JSON and render
   supported charts. See [Data Quality](data-quality.md#inspect-a-build) and
   [result export](getting-started.md#query-and-export-real-results).

The full build has Winnipeg panel-size and coverage gates. A small experimental
dataset can validate ingestion and transformations without satisfying those
full-analysis gates. Do not lower them to make a one-day sample look complete.
Partial rebuilds also require checking their dependents: updating a dimension
alone does not refresh the facts that used its previous values.

## Diagnose failures

| Symptom | What to inspect | Recovery |
|---|---|---|
| Ingestion failed | Source/window, upstream response, retries and credentials | Correct the cause; clear or rerun the affected task, or use a bounded CLI window |
| Missing replayable Bronze window | Manifest coverage and audit log | Let the coverage audit repair eligible recent gaps, or backfill the named window |
| Duplicate keys or count differences | Content audit repair list and source revision | Investigate, re-pull verified replayable windows, then rebuild affected downstream data |
| Missing snapshot day | Timer, journal and independent watchdog | Record the gap; restore collection for today. Never backdate current data |
| Empty Silver output | Bronze paths, schema, local-date window and rejects | Correct the input problem; retain the expected output floor |
| Parquet exists but Trino returns no rows | Partition metadata and `sync_partitions` task | Synchronize the affected table, then query a bounded partition |
| Gold gate fails | First failing table, grain, dependencies and source watermark | Correct the cause and rebuild the table and affected dependents |
| Multiple forecast versions need a serving choice | Available artifact versions and build arguments | Use the CLI's explicit `FORECAST_VERSION`; do not rely on an implicit selection |
| DAG green but certification is `suspect` or `unknown` | Audit observations and checks that could not execute | Resolve the finding or audit failure, then rerun and inspect the new verdict |
| Figure missing after rendering | Renderer `skip` messages and rendered count | Use a supported figure or its intended carrier; export success is not rendering support |

Airflow catchup creates missing intervals; it is not an endless retry mechanism
for a task that has already failed. A Bronze dry run only checks the source path.
If it succeeds while upload fails, investigate storage access separately.

## A long Spark run may still be working

The reference deployment once spent about 2.5 hours in an object-store commit
phase with little log output. It was incorrectly treated as stalled. Before
interrupting a long job, compare process CPU time, container network activity and
object counts across two observations.

The later [incident analysis](../dev/postmortem/cross-region-object-store-incident.md)
identified cross-data-center latency multiplying thousands of copy/delete and
small-read operations. Keep compute close to storage where practical; use bounded
windows and partition predicates. Increasing memory will not by itself fix
round-trip latency.

For Python version errors, ensure driver and executor Python match. For S3A
`NoSuchMethodError`, check the pinned Hadoop/AWS dependency pair in
[dags/_spark_common.py](../../dags/_spark_common.py). For garbled Bronze rows,
check the `.gz` suffix before changing the schema.

## Apply code and configuration changes

| Change | Command |
|---|---|
| Python code mounted into existing Airflow containers | `make stack-restart-airflow` |
| `.env` or Compose environment settings | `make stack-recreate-airflow` |
| Airflow image dependencies | `make stack-rebuild-airflow` |
| Inspect recent service logs | `make stack-logs S=airflow-scheduler` |
| Stop project containers, retain volumes | `make stack-down` |

Restarting a container does not update its environment. Rebuilding an image does
not by itself validate a job's data output. After a change, run a bounded check
that exercises the affected path.

## Protect persistent state

Use your own capacity measurements rather than extrapolating a fixed “years of
headroom” claim. Historical scans, many small objects, model artifacts and
snapshot growth all contribute differently.

The reference deployment recorded no storage backup; that is a limitation of that
deployment, not a recommended setting for yours. Preserve object data, metastore
state and any Airflow state you need. The forward snapshot archive is especially
important because upstream cannot replay it.

Before leaving collection unattended, confirm that its failure notification and
missed-run watchdog both work. Keep snapshot and other workflow watchdogs
separate: a successful backfill must not impersonate a successful snapshot run.
