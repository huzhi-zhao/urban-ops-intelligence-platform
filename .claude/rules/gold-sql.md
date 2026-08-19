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

The L2 decision (design §6.3) is that Gold tables are rebuilt whole, via
`CREATE OR REPLACE TABLE ... AS SELECT` (available in Trino 451). That
statement is atomic and leaves the old table in place on failure, which is
what makes whole-table rebuild safe.

It also destroys the previous chunk. A build that is both chunked and
whole-table must:

1. write chunks into a staging table with `INSERT`, then
2. swap it into place once, after the last chunk succeeds.

Never `CREATE OR REPLACE` per chunk. The failure mode is a table holding only
the final chunk — a plausible-looking, much smaller table, with nothing
raising.

⚠️ Still unverified as of 2026-08-19: how `CREATE OR REPLACE TABLE` behaves on
an **external** table with a declared `external_location`, and whether the
previous generation's objects are overwritten or left as orphans that the next
full scan would read. Test on a smoke prefix (`make ddl-create
PREFIX=smoke-YYYYMMDD`), never on the production tables.

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
