# Silver ETL

Silver turns collected records into typed, geographically usable data. Its job
is to establish a reliable structure while keeping business labels available for
later interpretation. For the role of Spark in the larger pipeline, see
[Architecture](architecture.md).

## Follow a service request

The [service-request job](../../spark/jobs/etl_service_request.py) performs these steps:

1. Read Bronze with an explicit raw schema. Numeric-looking identifiers remain
   identifiers rather than being inferred differently across files.
2. Derive `open_date_local` from the original Winnipeg wall-clock date, then
   convert event timestamps to UTC. A late-evening request must remain in the
   correct local reporting day.
3. Validate the composite key `(case_id, interaction_id)`. The job raises on a
   duplicate; deduplicating by `case_id` would erase legitimate interactions.
4. Assign usable coordinates to work-zone polygons. Keep `matched`, `unmatched`
   and `no_geo` separate in `geo_match_status`.
5. Enforce the target schema, retain rejected rows, and write date partitions
   using dynamic partition overwrite. Check the row count and spatial hit rate.

Raw `type`, channel and administrative labels stay in Silver. Winter
classification, channel normalization and label case folding happen in **Gold**.
That boundary keeps a change in business vocabulary from masquerading as a change
in the upstream record.

## Output contracts differ by dataset

| Output | Layout or behavior |
|---|---|
| Service requests | `silver/service_request/open_date_local=YYYY-MM-DD/` |
| Rejected service requests | `silver/_rejects/service_request/window=START_END/` |
| Weather archive | `silver/weather_archive/date=YYYY-MM-DD/` |
| Snowfall events | Whole-table rebuild from the complete Silver weather series |
| Plow shifts, parking bans, zone boundaries, address counts | Whole-table reference rebuilds |

Do not assume every Silver table has a `date` partition or the same deduplication
rule. The [schemas](../../spark/schemas/), [jobs](../../spark/jobs/) and
[DDL](../../sql/ddl/) define each contract. The v1.0 service-request checkpoint
contains 12,477,414 rows across 4,878 partitions; see the
[scope explanation](data-sources.md#source-scope-and-analysis-scope).

## Why event segmentation is a separate operation

A storm can cross a daily processing window. Segmenting each window independently
could turn one storm into two events. The daily archive job updates weather
records; a run with `--emit-events` rebuilds events from the whole Silver series.

The H1 rule uses a 3 cm daily threshold, a 10-day / 10 cm accumulation criterion,
and zero tolerated gap days. The weather backfill DAG still exposes a **2 cm
default** for its daily threshold, so set `snowfall_threshold_cm` to **3.0**
explicitly when reproducing H1. The CLI defaults and the DAG defaults must not be
assumed interchangeable. Changing the rule requires a new event-rule version and
rechecking dependent panels.

## Run a bounded window

With Bronze inputs, reference geometry and the compute stack prepared, trigger
`dag_backfill_silver_service_request` in Airflow with a small window:

```json
{"start": "2024-01-15", "end": "2024-01-16", "bucket": "uoip"}
```

The end date is exclusive. Use your own bucket. A successful run should produce
the corresponding local-date partition and complete `sync_partitions`, which
registers it for Trino. Read task logs for row counts, rejected rows and spatial
coverage; task success alone is not a comparison with the published baseline.

`dag_backfill_silver_weather_archive` takes the same window fields plus event
parameters. It also rebuilds the event table, so use it after preparing the
intended historical weather series, not as a harmless one-day experiment on a
shared dataset. See [Backfill](backfill.md).

## Run a reference job from the project container

After collecting the matching Bronze source, this example submits the boundary
job using the same shared Spark configuration as the DAGs. Run it from the
repository root with the compute containers and Spark master already running.
It writes to the bucket configured inside the container.

```bash
docker compose --env-file .env -f infra/docker/docker-compose.yml \
  exec -T airflow-scheduler python - <<'PYTHON'
import os
import subprocess
import sys

sys.path.insert(0, "/opt/airflow/dags")
from _spark_common import S3A_JARS, SPARK_CONF

job = "etl_plow_zone_boundary.py"
command = ["spark-submit", "--master", "spark://spark-master:7077",
           "--deploy-mode", "client", "--jars", S3A_JARS]
for key, value in SPARK_CONF.items():
    command.extend(["--conf", f"{key}={value}"])
command.extend([f"/opt/airflow/plugins/spark/jobs/{job}",
                "--bucket", os.environ["S3_BUCKET_NAME"]])
subprocess.run(command, check=True)
PYTHON
```

A successful boundary run writes the reference Parquet used by request assignment.
The recorded baseline has 82 boundary records. After collecting their respective
Bronze inputs, the same submission pattern works with `job` changed to
`etl_plow_shift.py`, `etl_parking_ban.py` or `etl_snow_clearing_address.py`.
The address-count job needs the boundary output first and defaults to the newest
collected snapshot. Each reference job has full-table output semantics; this
command is not a read-only inspection.

## Scheduling and dependencies

The daily service-request and weather-archive DAGs run a seven-day processing
window. Reference jobs run explicitly when their inputs change. A whole-table
write does not intrinsically prevent scheduling; these particular reference
jobs simply have no dedicated scheduled DAG in this baseline.

Airflow's driver and Spark executors must use matching Python versions and
compatible dependencies. The repository supplies the worker image and shared
S3A settings in [dags/_spark_common.py](../../dags/_spark_common.py). Geometry
assignment uses Python code on executors, so installing a dependency only on
the driver is insufficient.

Writing files does not automatically update Hive partition metadata. Confirm
that the DAG synchronizes partitions, or perform the corresponding metadata
step before investigating apparently missing data in Trino.

## Extend a transformation

Keep reusable transforms in `spark/transforms/`, declare the raw and target
schemas, and make the job assemble read, transform, validate and write steps.
Select the grain, key, timezone and write strategy before implementing the job.
For a schema change, update the producing transform, schema, DDL, contract and
migration note together. Add scheduling only where it serves a real refresh need.

[Data Quality](data-quality.md) explains downstream checks;
[Operations](operations.md) covers failures and rebuild order.
