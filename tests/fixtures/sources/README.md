# Synthetic source registry (test fixtures)

Role-named, city-agnostic source definitions used by the unit tests that
exercise generic machinery — the backfill facade, bulk slicing, the CLI
dispatch and the loader itself.

**Why these exist.** Those tests used to run against the real
`config/sources/*.yaml`, which meant the generic layer's test coverage was
anchored to whichever cities happened to be deployed. Retiring a city then
turned a pile of unrelated tests red, and the guardrail in CLAUDE.md §1
("no city-specific literals in the generic layer") could not be checked by
the suite that was supposed to protect it.

One source per partition strategy, named after the **role** it plays, never
after a city or a real dataset:

| file | source id | strategy | stands in for |
|---|---|---|---|
| `daily_socrata.yaml` | `SRC-TEST-DAILY` | `daily` | a high-volume service-request stream, per-day Socrata queries |
| `daily_wide_fetch.yaml` | `SRC-TEST-WIDE` | `daily` | a weather API whose one call covers the whole window |
| `monthly.yaml` | `SRC-TEST-MONTHLY` | `monthly` | a lower-volume stream with several datasets sharing one token |
| `static_geojson.yaml` | `SRC-TEST-STATIC` | `static` | administrative boundaries that change rarely |
| `snapshot.yaml` | `SRC-TEST-SNAPSHOT` | `snapshot` | an overwrite-in-place upstream with no time field |

Point the loader at this directory with the `synthetic_sources` fixture in
`tests/unit/conftest.py`, which sets `UOIP_CONFIG_DIR`.

The resource ids and domains here are deliberately fake. Nothing in this
directory is ever fetched — every test that uses it mocks the fetcher or the
facade.
