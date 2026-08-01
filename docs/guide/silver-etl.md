# Silver ETL

Silver turns raw Bronze NDJSON into clean, schema-enforced, deduplicated Parquet. Every
Silver job is a PySpark application submitted by Airflow. The execution model — driver in
the Airflow container, executors on the Spark worker — is described in
[Architecture](architecture.md) §6.

## What a Silver job does

1. Read Bronze with an **explicit schema**. `spark.read.json()` without a schema
   silently coerces types and is prohibited.
2. **Deduplicate.** Lookback windows re-fetch days that were already ingested, so every
   job needs a defined dedup key and freshness rule, or duplicates accumulate silently.
3. Normalise all timestamps to **UTC**, via `spark/transforms/timestamp_normalizer.py`.
4. Validate ranges. Rows that fail are written to `silver/_rejects/{dataset}/` rather
   than dropped silently — a rejected row is evidence, not noise.
5. Enforce the target `StructType` from `spark/schemas/`.
6. Write Parquet partitioned by date, with **dynamic partition overwrite** so that
   re-running a window replaces only the partitions in that window.
7. Assert a row-count floor. Output below the baseline raises, which fails the Airflow
   task and triggers retries and alerting.

Reusable transform functions live in `spark/transforms/`. Files in `spark/jobs/` are
entry points only — do not define utility functions there.

## Winnipeg-specific cleansing

Three transforms are required by the data's known defects (see
[Data Sources](data-sources.md) §1) and are not optional:

| Transform | Why |
|---|---|
| **Channel normalisation** — `Self Service + Mobile + SMS In → VOF` | The 2022 taxonomy migration; without it, any channel series has a false cliff |
| **Request-type dictionary** — parse 3,563 `type` values into priority tier and shift | The basis of weighted volume (BO-1) and the SLA audit (BO-5). Handles `Pr 2` / `Priority 2` / `P2` and `_vof` variants |
| **Geo-availability flag** (`has_geo`) | 79% of rows carry no location. Gold filters on this column so that spatial results state their real denominator |

The dictionary and the channel map are **data, not code** — they belong in `config/` or
in Gold seed tables, so that a vocabulary change is a data change.

Geometry is stored in Silver as **WKT strings** and converted in Gold with
`ST_GeomFromText`. That was originally a workaround, and under Trino it happens to be the
most direct input format for `ST_Contains`.

## Output layout

```
silver/<dataset>/date=YYYY-MM-DD/
silver/<reference dataset>/
silver/_rejects/{dataset}/
```

## Status

| Source | State |
|---|---|
| Weather (`spark/jobs/etl_open_meteo.py`) | Implemented — date-partitioned, 7-day sliding window |
| Static geography (`spark/jobs/etl_dcp.py`) | Implemented — full-overwrite reference job. Written against the retired deployment's boundary file; it is the working pattern for the plow-zone boundaries, not a source in this deployment |
| 311, plow shifts, parking bans, clearing snapshots | Not started |

Current status always lives in the **Implementation status** section of `CLAUDE.md`.

## Scheduling

| DAG shape | Schedule | Window |
|---|---|---|
| `dag_silver_<dataset>` | Cron, `catchup=True` | Fixed sliding window ending at the execution date |
| `dag_backfill_silver_<dataset>` | Manual | Arbitrary `[start, end)` |

Incremental scheduling and one-shot backfill are **different operations**. `catchup=True`
replays past intervals, but each replay is still the same sliding window — it never
becomes a full-history scan. A full backfill is a single wide call:

```bash
spark-submit spark/jobs/etl_open_meteo.py --bucket uoip --start 2008-01-01 --end 2026-07-01
```

## Adding a source to Silver

1. Define `<X>_RAW_SCHEMA` and `<X>_SILVER_SCHEMA` in `spark/schemas/`.
2. Write the cleaning functions in `spark/transforms/`, ending with a schema-enforcement
   step.
3. Write `spark/jobs/etl_<dataset>.py` following the read → transform → enforce → write
   shape.
4. Write `dags/dag_silver_<dataset>.py` reusing `SparkSubmitOperator` and the shared
   config in `dags/_spark_common.py`.

The roles of Airflow, driver, master, worker and object storage do not change — only the
job script the driver submits.

> Prefer DataFrame operators over Python UDFs. The one UDF in the codebase (geometry → WKT
> conversion) forces executors to import project code, which in turn constrains the Python
> version and `PYTHONPATH` of the worker image. The reasoning is documented in
> `dags/_spark_common.py` — read it before changing any Spark configuration.

## Related

- [Ingestion & Bronze](ingestion-bronze.md) — where the input comes from
- [Backfill](backfill.md) — loading a wide historical window
