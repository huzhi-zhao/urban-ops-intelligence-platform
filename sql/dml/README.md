# `sql/dml/` — Gold-layer load statements

Trino SQL that fills the Gold tables declared in `sql/ddl/`. One file per
target table, named exactly after it (`dim_plow_zone.sql`,
`fact_event_zone_rank.sql`, …).

Execution engine is **Trino, not Spark**. Spatial attribution is already
resolved in Silver (ADR 0009 — `silver_service_request.plow_zone` is a
solved zone label by the time it lands), so the Gold layer needs no geometry
functions: everything here is join + aggregate + arithmetic over Hive
external tables. See `docs/dev/design/20260817-etl-implementation.md` §3.0
for the decision and §4 for the rejected alternatives.

## Rules

- **`INSERT OVERWRITE PARTITION`, never `MERGE`.** The unit of overwrite is
  one whole day partition (C6/C17, `docs/dev/launch/20260814-table-creation-deployment-launch.md` §7.2).
  `MERGE` on a Hive external table would need Iceberg, which is out of H1.
- **No `SELECT *`** anywhere — explicit column lists only, so an upstream
  schema drift fails loudly instead of silently reshaping a Gold table.
- **No hardcoded dates.** Every date is a parameter; a statement that cannot
  be replayed for an arbitrary `execution_date` does not belong here.
- Files do **not** write `catalog.schema` qualifiers. `scripts/ddl/apply_ddl.py`
  injects them at connect time, and the loaders follow the same convention.
- Every fact carries `etl_run_id` / `built_at` / `source_max_ingest_date`
  (ADR 0010 D7).
- Dimension tables are rebuilt whole (`INSERT OVERWRITE`), not incrementally
  patched — they are small and their seeds are the authority.

## What does *not* live here

Scoring, ranking and recommendation SQL goes to `sql/intelligence/`.
Table definitions stay in `sql/ddl/`. Seed data is `config/seeds/`.
