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

- **A Gold table is rebuilt whole, in four steps. `INSERT` here appends.**

  🔴 **Trino has no `INSERT OVERWRITE` syntax at all**, and on the Hive
  connector `CREATE OR REPLACE TABLE`, `TRUNCATE` and `DELETE` are each
  `NOT_SUPPORTED` (measured on Trino 451, 2026-08-19 — launch doc §4.1). An
  earlier version of this file said "`INSERT OVERWRITE PARTITION`, never
  `MERGE`"; written literally, the second run of any file here **doubles the
  row count and raises nothing**. The rebuild is:

      DROP TABLE  ->  purge the storage prefix  ->  CREATE from sql/ddl/  ->  INSERT

  Step 2 is not tidiness: these are *external* tables, so `DROP` leaves the
  objects behind and the recreated table reads the previous generation before
  a single `INSERT` runs. `scripts/gold/build_gold.py` does all four; a DML
  file here contributes only the final `SELECT`.

  Because `INSERT` appends, **the exact row-count gate after each build is
  load-bearing, not ceremony** — a failed purge shows up as doubled rows and
  nothing else raises.

  ⚠️ **C6's `INSERT OVERWRITE PARTITION` still stands for Silver**, which
  Spark writes with `partitionOverwriteMode=dynamic`. C6 is a *layered*
  statement (O11, signed off 2026-08-19): Spark→Silver overwrites a day
  partition; Trino→Gold rebuilds the table. `MERGE` remains out of scope in
  both layers — it needs Iceberg, which is out of H1.

  Full rules and the measurement behind them: `.claude/rules/gold-sql.md` R4.
- **No `SELECT *`** anywhere — explicit column lists only, so an upstream
  schema drift fails loudly instead of silently reshaping a Gold table.
- **No hardcoded dates.** Every date is a parameter; a statement that cannot
  be replayed for an arbitrary `execution_date` does not belong here.
- Files do **not** write `catalog.schema` qualifiers — with **one required
  exception**: a Silver table is reached as `FROM {{ silver }}.silver_<table>`.
  🔴 The connection's default schema is the *Gold* one, so a bare Silver table
  name resolves as `hive.uoip_gold.silver_*` and dies with `TABLE_NOT_FOUND`
  (measured 2026-08-19, six files). The braces are doubled because the
  qualifier sits outside a string literal and single-brace text there makes
  sqlfluff abandon the whole file. See `.claude/rules/gold-sql.md` **R6**.
- Every fact carries `etl_run_id` / `built_at` / `source_max_ingest_date`
  (ADR 0010 D7).
- Dimension tables are rebuilt whole (the four steps above), not incrementally
  patched — they are small and their seeds are the authority.

## What does *not* live here

Scoring, ranking and recommendation SQL goes to `sql/intelligence/`.
Table definitions stay in `sql/ddl/`. Seed data is `config/seeds/`.
