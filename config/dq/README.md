# `config/dq/` — out-of-pipeline data-quality rules

One file, `rules.yaml`, read by `scripts/dq/rules.py` and executed by
`scripts/dq/run_audit.py`. Design:
[`docs/dev/design/20260822-out-of-pipeline-dq-audit.md`](../../docs/dev/design/20260822-out-of-pipeline-dq-audit.md).

Rules are configuration rather than code for the same reason seeds are: a
threshold is business semantics, and the audit runs daily against numbers that
move when the upstream moves. A rule you have to redeploy the auditor to change
is a rule that gets muted instead of updated.

## A rule

| field | meaning |
|---|---|
| `id` | stable key of the trend in `dq_audit_log`. Renaming one starts a new trend |
| `layer` | `bronze` \| `silver` \| `gold` |
| `table` | a table name, or `*` for "once per Gold table" (the runner expands it) |
| `dimension` | `structural` \| `statistical` \| `business` \| `cross_layer` |
| `check` | `{type: ...}` — a built-in check or `{type: sql, sql: ...}` |
| `comparator` | `>=` \| `<=` \| `==` \| `between` \| `pct_change` \| `pct_point_change` |
| `expected` | scalar, or `[low, high]` for `between` |
| `severity` | `error` (blocks certification) \| `warn` |
| `cadence` | `daily` \| `weekly` \| `manual` |
| `note` | **required** — where the number came from |

## Three rules about the rules

🔴 **No row-count rule is an equality** (design §4.2). Exact equality belongs to
the in-pipeline gates, where the expected value and the observed value come out
of the same build. Out here the table gets rebuilt and the upstream appends, so
an equality goes stale and then gets silenced. Lower bounds are the baseline ×
0.9 rounded to something readable — they catch "the table is empty" and "only
the last chunk landed"; the `pct_change` rules catch the drift. Enforced by
`test_no_out_of_pipeline_row_count_rule_is_an_equality`.

🔴 **Every rule carries a measured anchor in `note`.** An L3-c baseline number,
a launch doc's row count, or a contract. A number nobody measured produces an
argument, not a fix.

🔴 **A `sql` check that reads Silver must use `{{ silver }}.` and carry an
`open_date_local` predicate** (gold-sql.md R6 and R1). A bare Silver name
resolves in the Gold schema; an unwindowed read asks MinIO for 4,878 objects at
once and times out. Both are rejected at load time.

## `pct_change` vs `pct_point_change`

`pct_change` is relative (a row count moving 10%); `pct_point_change` is
absolute (a null *rate* moving 5 percentage points). On the 93.46% baseline
those are 4.7 points apart, which is why both exist. Neither fails on the first
run — there is nothing to compare against, and starting a fresh log red would
teach everyone to ignore it.
