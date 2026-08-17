# `sql/intelligence/` — load score, drivers, recommendations

Trino SQL for the scoring chain: the three-factor Winter Operational Load
Score and the recommendations derived from it. Everything here reads Gold and
writes Gold; nothing here touches Silver or geometry.

Target tables (`docs/dev/design/20260817-etl-implementation.md` §3 E5):

| file | table | note |
|---|---|---|
| `fact_winter_event_zone_load.sql` | F6 | the score itself |
| `fact_recommendation.sql` | F7 | PK includes `model_version` |

`fact_request_forecast` (F5) is **not** here — it is written by the M1
training/prediction code in Python, not by SQL.

## Rules

Everything in `../dml/README.md` applies. On top of it:

- **Missing inputs are expressed, not defaulted.** The panel is full
  (1,298 cells); a cell with no rank factor carries `score_status`, and
  `rank_factor` stays NULL. `rank_factor = 0` must never appear — a zero is a
  measured last-place ranking, not an absence.
- `score_weight_profile` is bound to `score_status`: a row scored without the
  rank factor is `demand_weather_only`, never `full_3factor` with a hole in it.
- The weather factor is an **event-level constant** within H1 (A2 option ②) —
  assert that it takes the same value across every zone of one event.
- The recommendation text is a **rule-driven template with a fallback**
  (`dim_recommendation_rules`). It is not a model and must not be described as
  AI anywhere — in code, comments, docs or the talk.
- Calibration windows are written into columns (`calibration_window`), never
  buried in a SQL literal: the zone ordering is not stable over time
  (ρ = +0.591 between the two halves of the decade), so which window a number
  came from is part of the number.
