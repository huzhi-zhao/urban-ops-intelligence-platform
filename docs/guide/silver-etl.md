# Silver ETL

Silver turns raw Bronze NDJSON into clean, schema-enforced, deduplicated Parquet.
Every Silver job is a PySpark application submitted by Airflow.

![Silver execution architecture](../images/silver-execution-architecture.svg)

## Status

| Source | Job | State |
|---|---|---|
| `SRC-Open-Meteo` | `spark/jobs/etl_open_meteo.py` | Implemented — date-partitioned, 7-day sliding window |
| `SRC-DCP` | `spark/jobs/etl_dcp.py` | Implemented — static, 5 rows, full overwrite |
| `SRC-NYC-311` | — | **Not implemented** |
| `SRC-NYPD` | — | **Not implemented** |

## What a Silver job does

1. Read Bronze with an **explicit schema** — `spark.read.json()` without a schema
   silently coerces types and is prohibited.
2. Deduplicate. Lookback windows re-fetch days that were already ingested, so
   every job needs a defined dedup key and freshness rule.
3. Normalise timestamps to UTC.
4. Validate ranges; rows that fail are written to `silver/_rejects/{dataset}/`
   rather than dropped silently.
5. Enforce the target `StructType` from `spark/schemas/`.
6. Write Parquet partitioned by date, with dynamic partition overwrite so that
   re-running a window only replaces the partitions in that window.
7. Assert a row-count floor. Output below the baseline raises, which fails the
   Airflow task and triggers retries and alerting.

Reusable transform functions live in `spark/transforms/`. Job files in
`spark/jobs/` are entry points only — do not define utility functions there.

## Output layout

```
silver/weather/date=YYYY-MM-DD/
silver/borough_boundaries/
silver/_rejects/{dataset}/
```

## DAGs

| DAG | Schedule | Window |
|---|---|---|
| `dag_silver_open_meteo` | `0 7 * * *`, catchup | Fixed 7-day sliding window ending at the execution date |
| `dag_backfill_silver_open_meteo` | manual | Arbitrary `[start, end)` |
| `dag_backfill_silver_dcp` | manual | Full overwrite |

Incremental scheduling and one-shot backfill are **different operations**.
`catchup=True` replays past intervals, but each replay is still the same 7-day
window — it never becomes a full-history scan. A full backfill is a single wide
call, submitted directly:

```bash
spark-submit spark/jobs/etl_open_meteo.py --bucket <bucket> --start 2024-01-01 --end 2026-06-29
```

## Adding a source to Silver

1. Define `<X>_RAW_SCHEMA` and `<X>_SILVER_SCHEMA` in `spark/schemas/`.
2. Write the cleaning functions in `spark/transforms/`, ending with a schema-enforcement step.
3. Write `spark/jobs/etl_<dataset>.py` following the read → transform → enforce → write shape.
4. Write `dags/dag_silver_<dataset>.py` reusing `SparkSubmitOperator` and the
   shared config in `dags/_spark_common.py`.

The roles of Airflow, driver, master, worker and object storage do not change —
only the job script the driver submits.

> Prefer DataFrame operators over Python UDFs. The one UDF in the codebase
> (geometry → WKT conversion for `SRC-DCP`) forces executors to import project
> code, which in turn constrains the Python version and `PYTHONPATH` of the
> worker image. The reasoning is documented in `dags/_spark_common.py` — read it
> before changing any Spark configuration.
