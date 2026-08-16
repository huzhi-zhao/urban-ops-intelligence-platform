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

## [Unreleased]

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
