# `config/seeds/` — Gold dimension seed data

Hand-maintained CSV that seeds the Gold dimension tables which have no
upstream source. This directory is the *authority* for the business semantics
that the city-agnostic guardrail (CLAUDE.md §城市无关护栏) keeps out of
`ingestion/`, `spark/transforms/` and `dags/`: if a mapping would have to
change when the platform is pointed at another city, it belongs in a file
here, not in Python.

| file | target table | rows | note |
|---|---|---|---|
| `winter_category.csv` | `dim_winter_category` | 7 | load first — `dim_service_type` FKs to it |
| `service_type_keywords.csv` | `dim_service_type` | — | keyword → winter category dictionary; the 3,563 `type` values themselves come from Silver, not from a seed |
| `channel.csv` | `dim_channel` | 15 | includes the `Self Service + Mobile + SMS In → VOF` normalization and `is_comparable_pre_2022` |
| `recommendation_rules.csv` | `dim_recommendation_rules` | — | text templates + fallback. **Not a model — never call it AI** |

## Rules

- CSV, UTF-8, header row, one concept per file. Not JSON: these get reviewed
  by eye and edited by hand, and a diff has to be readable.
- A seed is data, so it is **version-controlled and reviewed** like data:
  changing a mapping is a PR, not an edit on the box.
- Loading is full rebuild (`INSERT OVERWRITE`) via `sql/dml/dim_*.sql` or the
  bootstrap script — never an incremental patch.
- `dim_service_type`'s build **fails on any uncovered `type` value**
  (anti-join must be 0). An unmatched value means the dictionary is behind the
  data; it must not resolve to a silent NULL category. The first build is
  expected to report a large batch at once
  (`docs/dev/launch/20260813-gold-silver-schema-derivation-launch.md` C13).
- Multi-keyword hits are arbitrated first-match-wins. **That rule is not yet
  validated** — the build prints every multi-hit value for a human pass
  (design §6 O3).
