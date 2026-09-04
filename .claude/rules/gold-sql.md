# Gold SQL — Query Conventions

> Read this before writing anything in `sql/dml/`, `sql/intelligence/`, or any
> query that reads `silver_service_request`. Companion to
> [backfill.md](backfill.md); same status — binding, not advisory.

---

## The measured facts these rules come from

Taken on the compute node, 2026-08-19, Trino 451 against the production tables:

| Query | Result |
|---|---|
| `COUNT(*)` on one day partition | 1,385 rows, instant |
| `COUNT(*)` over one calendar year | 777,833 rows, seconds |
| Seven real columns over one calendar year | seconds |
| Seven real columns over **all 4,878 partitions** | 🔴 **fails**: `Unable to execute HTTP request: Read timed out` |

`silver_service_request` holds **12,477,414 rows across 4,878 day partitions**,
one file each (C7). The failure is Trino's S3 client timing out against MinIO,
not the CLI, not the coordinator, and not a query-time limit — a whole-table
scan asks for 4,878 objects at once and the reads queue past the socket
timeout. Trino is a platform-level shared service (ADR 0006 §9), so its
connection settings are not this repo's to tune.

> 🔴 **更正（2026-09-01）**：the compute node and the storage node are in
> **two different datacentres**, so every one of those object reads crosses a
> WAN link. The wall below is still partition count, but the *height* of the
> wall is a function of per-round-trip latency, not a constant of this
> codebase — the same 4,878 partitions co-located with the storage would not
> necessarily fail. Treat R1/R2's cost parameters as deployment-dependent.
> See [cross-region-object-store-incident.md](../../docs/dev/postmortem/cross-region-object-store-incident.md).

Two corollaries worth stating because they mislead:

- **`$partitions` and `COUNT(*)` are not evidence the data is readable.**
  The first reads only the Hive Metastore; the second can be answered from
  Parquet footers. A query that touches no real column proves nothing about
  one that does — this cost a diagnostic round on 2026-08-19.
- **The wall is partition count, not row count or throughput.** One year is
  365 partitions and returns in seconds. Nothing here is slow.

---

## R1 · A query reading Silver carries a date predicate

Every `sql/dml/*.sql` and `sql/intelligence/*.sql` statement that reads
`silver_service_request` must constrain `open_date_local`. No exceptions in
committed SQL; ad-hoc exploration is the author's own risk.

The predicate is what lets Trino prune partitions — it is the difference
between reading 365 objects and 4,878. Prune with the **partition column**:
a predicate on `open_ts_utc` filters rows but prunes nothing, because the
partition key is the local date and the metastore knows nothing about the
timestamp column's range.

Most Gold tables satisfy this for free, and it is worth understanding why
rather than treating R1 as a tax: the scoring chain aggregates **over snowfall
events**, and an event is a dimension row with a start and an end. BO-3
measured N = 100 events at a median of 1.0 day, so `fact_service_request_zone_event`
(22 zones × 99 events × 6 categories) reads on the order of a hundred day
partitions, not five thousand. The event window *is* the date predicate.

Exactly one Gold table genuinely spans the full history:
`fact_winter_request_daily_by_label`, grain `(date, label_type, label_id)`,
covering ~6,600 days. It is also the only Gold table whose grain contains a
date at all, and the only candidate for a partitioned DDL — see the L2 design
doc §6.3. Needing chunked execution and needing a partition key turn out to be
the same table, which is why R2 exists for one consumer.

---

## R2 · Chunking is an execution strategy, never a schema

A chunked build runs the same statement N times under N date predicates and
combines the results. The year appears **only in each chunk's `WHERE`**. It
does not become a column, a partition key, a dimension table, or a grain.

The output is byte-identical to what a single unchunked statement would
produce. If it is not, the aggregation was not chunkable — see R3.

Do **not** add a year dimension. The analytical time unit for winter
operations is the snowfall event and the snow season, neither of which aligns
with a calendar year; a `dim_year` would be an execution detail mistaken for
business semantics, which the city-agnostic guardrails already forbid for the
same reason (business semantics live in config and Gold dimensions, derived
from what the business asks, not from how the query ran).

Silver needs nothing to support this. It is already day-partitioned, which is
precisely why a one-year query is fast.

A chunked statement declares two things in its file header, because a reader
cannot infer either from the SQL:

```sql
-- chunked_by: calendar year (2008..2026)
-- combine: additive (SUM of per-chunk counts)
```

---

## R3 · Non-additive aggregates must not be chunked

`COUNT`, `SUM`, `MIN`, `MAX` combine across chunks. These do not:

| Aggregate | Why not |
|---|---|
| `COUNT(DISTINCT x)` | A key present in two chunks is counted twice |
| Median, percentiles | Not derivable from per-chunk values |
| **Any ratio or percentage** | 🔴 See below |

**Emit counts from each chunk and compute the ratio once, at the end.**
Averaging per-chunk percentages weights every chunk equally regardless of its
row count, and silently returns a plausible wrong number. The E5 null-rate
baseline was collected per year for exactly this reason: the chunks emit
numerators and denominators, and the percentages are computed after summing.

If an aggregate is not combinable and the scan genuinely cannot be narrowed,
that is a reason to reconsider the query, not to chunk it anyway.

---

## R4 · Chunked writes and whole-table rebuilds conflict

The L2 decision (design §4.3) is that Gold tables are rebuilt whole. **How**
they are rebuilt was measured on 2026-08-19 against Trino 451, because three
of the four obvious ways do not exist on the Hive connector:

| Statement | Result on an external Hive table |
|---|---|
| `CREATE OR REPLACE TABLE` | 🔴 `NOT_SUPPORTED: This connector does not support replacing tables` |
| `TRUNCATE TABLE` | 🔴 `NOT_SUPPORTED: This connector does not support truncating tables` |
| `DELETE FROM t` (no `WHERE`) | 🔴 `NOT_SUPPORTED: Cannot delete from non-managed Hive table` |
| `DROP TABLE` + `CREATE TABLE` + `INSERT` | ✅ the only one that works |

So a whole-table rebuild is **four steps, in this order**:

1. `DROP TABLE`
2. **purge the table's storage prefix** (`apply_ddl._purge_storage`)
3. `CREATE TABLE` from `sql/ddl/<table>.sql`
4. `INSERT INTO <table> (<explicit columns>) SELECT ...`

🔴 **Step 2 is not optional and not tidiness.** Dropping an *external* table
leaves its objects behind — that is what "external" means. Measured: after
`DROP` the 2 smoke objects were still there, and the table recreated from the
same DDL read `COUNT(*) = 2` immediately, before a single `INSERT`. Skip the
purge and a rebuild silently unions the previous generation with the new one.

**This rebuild is not atomic**, unlike the `CREATE OR REPLACE` it replaces.
There is a window — seconds — in which the table does not exist or is empty.
That is accepted rather than solved: the Gold build is manually triggered
(design §4.4), takes seconds, and its only reader is Superset. `ALTER TABLE
... RENAME TO` **does not** buy the atomicity back: renaming a staging table
keeps `external_location` pointing at the staging path, which moves the
table's physical home instead of swapping its contents.

Because `INSERT` **appends**, the exact row-count gate after every build is
load-bearing, not ceremony: a failed purge shows up as doubled rows and
nothing else raises.

A chunked build sits on top of that four-step sequence rather than repeating
it: **drop, purge and create once**, then `INSERT` each chunk into the same
table, then run the gates after the last chunk. The per-chunk `INSERT` is the
same statement the unchunked build ends with, so nothing about the schema or
the file layout differs.

Never drop-and-recreate per chunk. The failure mode is a table holding only
the final chunk — a plausible-looking, much smaller table, with nothing
raising.

A chunked build's row-count gate is therefore the only thing standing between
a half-finished run and a table that looks fine. Record how many chunks were
expected, and assert it.

✅ Measured on a smoke prefix, 2026-08-19 (O12). The orphan question that
originally sat here is answered above: yes, they are left behind, which is why
the purge is step 2. Anything else in this file that says "unverified" should
be settled the same way — on `make ddl-create PREFIX=smoke-YYYYMMDD`, never on
the production tables.

---

## R5 · The standing prohibitions, restated because Gold is where they bite

From `AGENTS.md`, repeated here because `sql/dml/` is the first place in this
repo where they are enforceable rather than theoretical:

- **No `SELECT *`** in Gold SQL. Explicit column lists; schema drift must
  break the build, not propagate.
- **No hardcoded date strings.** Dates are parameters, including the chunk
  boundaries under R2.
- Every Gold table carries `etl_run_id`, `built_at` and
  `source_max_ingest_date` (ADR 0010 D7).

---

## R6 · Reach Silver through `{{ silver }}`, and only through it

Every Silver table a `sql/dml/` or `sql/intelligence/` file reads must be
written `FROM {{ silver }}.silver_<table>`. Never bare, never `hive.`, never a
literal `uoip_silver`.

🔴 **A bare Silver table name does not fail to resolve — it resolves in the
wrong schema.** The build connects with the *Gold* schema as the session
default (`_connect(settings, self.gold_schema)`), so `FROM
silver_service_request` is looked up as `hive.uoip_gold.silver_service_request`
and dies with `TABLE_NOT_FOUND`. Measured 2026-08-19: six of the nine
dimension DML files carried this, and the first production run of `--only
dims` hit it on chunk 1 of the first table.

**The braces are doubled because the placeholder sits outside a string
literal.** This is the one structural difference from `{chunk_start}` /
`{etl_run_id}`, which are substituted *inside* quoted literals and so leave
the file parseable. A schema qualifier lives in a `FROM`/`JOIN` clause, where
single-brace text is not SQL: sqlfluff reports one `PRS unparsable section`
and then **stops enforcing every other rule on that file** — the `SELECT *`
ban (R5) and the date-predicate check (R1) included. A silently unlinted file
is a worse outcome than the original error.

Both ends are wired for the doubled form and must stay in step:

| Where | What resolves it |
|---|---|
| `make lint` | `.sqlfluff` → `[sqlfluff:templater:jinja:context] silver = uoip_silver` |
| build time | `build_gold.py` → `text.replace("{{ silver }}", self.silver_schema)` |

The lint context hardcodes the production schema; the runtime substitution is
the one that honours `--location-prefix`, so a smoke build still points at
`uoip_silver_smoke_*`. Lint checks shape, not deployment.

Enforced by `test_dml_files_qualify_every_silver_table_with_the_silver_placeholder`,
which scans every `FROM`/`JOIN` target for `silver_`. That test exists because
the neighbouring rule ("no hardcoded catalog or schema") forbids the *wrong*
qualification without ever requiring the *right* one — the gap between two
rules, which is where this defect lived.
