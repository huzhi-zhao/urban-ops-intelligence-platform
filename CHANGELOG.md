# Changelog

Schema migration notes for the Silver and Gold layers, as required by
`AGENTS.md` → 「Data contract obligations」. This file is **not** a release log
and not a substitute for `git log`: a change belongs here only if someone
holding existing data has to do something about it — a column added, renamed,
retyped or dropped, a partition layout changed, a semantic redefinition that
leaves old partitions meaning something different from new ones.

Entry format — one line per change, with the migration action spelled out:

```
- `table.column` — what changed. **Migration**: what to do with data already written.
```

The file was created on 2026-08-16, long after the schemas it documents; the
Silver/Gold DDL landed on 2026-08-14 and predates it. Do not read the absence
of earlier entries as "nothing changed before then" — the record simply starts
here.

## [1.0] — 2026-08-22 · Silver + Gold schema frozen

The 25 tables (8 Silver + 17 Gold) declared on 2026-08-14 are now **all built
from production data**, and the schema they were built against is frozen at
v1.0. **No column was added, renamed, retyped or dropped anywhere in L1, L2 or
L3** — nothing in this release needs a migration. The entry exists to record
what v1.0 *is*, so a later change has something to be a change from.

- **Silver** (8 tables) — `silver_service_request` 12,477,414 rows across 4,878
  day partitions; the rest built whole-table.
- **Gold** (17 tables) — 3 seed dimensions, 6 derived dimensions, 5 facts,
  3 scoring tables (`fact_request_forecast`, `fact_winter_event_zone_load`,
  `fact_recommendation`). Zero-row tables: **0**. All-null columns: **0**.
- **Contract checks** — 185 assertions parsed off the DDL headers
  (`make gold-assert`): `not_null` 127 · `relationships` 23 · `unique` 17 ·
  `accepted_values` 12 · `range` 6. **0 violations** on 2026-08-22.

Two pieces of **DDL header prose are known to be stale** and are deliberately
left alone, because the headers are the frozen contract's own text and editing
them is a change-process matter, not a cleanup:

- `fact_service_request_zone_event` — `-- relationships: ... = 916`. The
  scheduling-era non-zero cell count measured **908** on 2026-08-19; the 916 was
  measured against the live Socrata API in 2026-08-09 and the event boundaries
  have since shifted (Open-Meteo revises its historical archive). The executed
  gate is a lower bound `>= 880`. See the L2 launch doc §4.9.
- `fact_winter_event_zone_load.load_score` — `-- note: Null when score_status
  != scored`. The build deliberately **does** score the 924 `partial_no_rank`
  cells on the disclosed 0.70 profile (decision O1, L3 launch §4.2), and a gate
  asserts it. Following the comment instead would blank 71.2% of the panel.

## [Unreleased]

- `bronze/raw/SRC-WPG-311/service_requests` — 55 daily shards (1.1% of 4,878)
  were rewritten on 2026-08-18 after the windowed Socrata fetcher was found to
  page without `$order=:id`. Unordered limit/offset paging both repeated and
  dropped rows at page boundaries, and the two defects **cancel out in the row
  count**, so neither a duplicate scan nor an upstream row-count
  reconciliation finds all of it alone. No schema changed; the same paths now
  hold different, correct records. Full account:
  [postmortem](docs/dev/postmortem/bronze-socrata-pagination-incident.md).
  **Migration**: anyone holding a copy of those Bronze days must re-pull them
  (`scripts/backfill/plan_wpg_311_pagination_repair.sh`, overwrite-in-place)
  and rebuild any Silver partition derived from them. The production
  `silver_service_request` full load was rebuilt from the repaired Bronze and
  needs nothing further.
- `silver_snowfall_event.accum_flag` (added 2026-08-15, `df921ab`) — new
  BOOLEAN column marking events admitted by the rolling-accumulation rule rather
  than by the single-day threshold (`peak_daily_snowfall_cm < threshold_cm`).
  Never null: with no accumulation criterion in play the value is `False`.
  **Migration**: none — no Silver snowfall-event partition had been produced in
  production yet. Rebuild via `dag_backfill_silver_weather_archive`, which is
  the only path that segments events correctly across window boundaries.
- `silver_snowfall_event.snowfall_event_id` (renamed from `event_id`,
  `cf10b13`) — renamed across the Gold and Silver schemas, contracts and ETL.
  **Migration**: none — same reason as above.
