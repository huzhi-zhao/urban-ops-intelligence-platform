# Data Sources

Every upstream source is registered twice: once machine-readable in
`config/sources/<id>.yaml` (validated by Pydantic, read by the pipeline) and once
here for humans. **The YAML is authoritative** — if the two disagree, the YAML wins.

## Registered sources

The current deployment ingests New York City open data. Source ids are literal
values from `config/sources/` and are used in every storage path.

| Source ID | Datasets | API | Partition strategy | Priority |
|---|---|---|---|---|
| `SRC-NYC-311` | 311 service requests | Socrata (SODA) | daily | P0 |
| `SRC-NYPD` | collisions, complaints (historic + YTD), shooting incidents | Socrata (SODA) | monthly | P0 |
| `SRC-Open-Meteo` | hourly weather history + 7-day forecast | Open-Meteo REST | daily | P1 |
| `SRC-DCP` | borough boundary polygons | GeoJSON, static file | static | P2 |

### `SRC-NYC-311` — service requests

The primary demand signal: citizen-reported issues (noise, heating, potholes).

| | |
|---|---|
| Endpoint | `https://data.cityofnewyork.us/resource/erm2-nwe9.json` |
| Auth | Public; an app token raises the rate limit |
| Incremental key | `created_date` |
| Volume | ~8k–12k rows/day, ~35M rows total, history back to 2010 |
| Upstream timezone | America/New_York |

Known issues:
- **Late-arriving updates** — `closed_date` is often filled in days after
  creation, so ingestion uses a 7-day lookback window and downstream layers must
  deduplicate.
- **Missing coordinates** — roughly 5% of rows have no latitude/longitude.
  District attribution falls back to the text field, then to `NULL`.
- Socrata **omits keys entirely for null values** rather than sending `null`.
  Never assume a field is present on a given record.

### `SRC-NYPD` — public safety events

Four Socrata datasets under one source id. Collisions is the one wired into the
scoring model today; the others are ingested but not yet modelled.

| | |
|---|---|
| Collisions endpoint | `https://data.cityofnewyork.us/resource/h9gi-nx95.json` |
| Incremental key | `crash_date` (+ `crash_time` for the timestamp) |
| Volume | ~200–400 rows/day, ~2M rows total |

Known issues:
- **Late entry** — reports can appear 3+ days after the event. Ingestion uses a
  7-day lookback and deduplicates on the collision id.
- `contributing_factor_*` is free text with heavy `"Unspecified"` usage;
  it needs normalisation in Silver.

### `SRC-Open-Meteo` — weather

| | |
|---|---|
| Endpoint | `https://api.open-meteo.com/v1/forecast` |
| Auth | None |
| Limit | <10,000 requests/day (free, non-commercial tier) |
| Fetch pattern | One wide call per window, not one call per day |
| Fields | `temperature_2m`, `precipitation`, `snowfall`, `windspeed_10m` |

Known issue: **forecasts change over time.** Bronze partitions by the execution
date of the fetch so every forecast snapshot is preserved; the Silver job uses a
7-day sliding window so later corrections are absorbed.

### `SRC-DCP` — geographic boundaries

| | |
|---|---|
| Format | GeoJSON (MultiPolygon, WGS84) |
| Refresh | Manual — boundaries change on the order of decades |
| Silver output | 5 rows, geometry stored as WKT (`geometry_wkt`) |

Known issue: geometry must be WGS84. Any other projection has to be converted
before load, or spatial functions in the warehouse will reject it.

## Adding a source

1. Add `config/sources/<id>.yaml`. Pick a partition strategy:
   `daily` for high-volume event streams, `monthly` for lower-volume streams,
   `static` for reference data. `daily` requires a `timestamp_field` on every dataset.
2. Add an entry to the table above.
3. Add `scripts/backfill/backfill_<slug>.py`. It is auto-discovered — no registry
   edit is needed. See [Backfill](backfill.md).
4. Add the ingestion DAG under `dags/`.
5. Add the data contract under `contracts/`.

## Adding a city

Source ids, endpoints and boundary files are the only city-specific artifacts.
To onboard a new city, register its sources as above and supply a boundary
dataset for the geography dimension. No pipeline code changes are required.
