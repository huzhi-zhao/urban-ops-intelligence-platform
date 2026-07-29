# Source Configuration

> Machine-readable source registry. **Single source of truth** for any code that
> touches an upstream data source — backfill scripts, Airflow DAGs, Spark jobs,
> dashboards.
>
> The human-readable companion is `docs/guide/data-sources.md`.
> When you change either, change both.

## Layout

```
config/sources/
  README.md           # this file
  nyc_311.yaml        # SRC-NYC-311
  nypd.yaml           # SRC-NYPD (4 datasets)
  open_meteo.yaml     # SRC-Open-Meteo
  dcp.yaml            # SRC-DCP
```

One file per source. A single source may contain multiple `datasets`
(e.g. NYPD has collisions, complaint historic, complaint current, shooting).

## Schema

```yaml
source:
  id: SRC-NYC-XXX            # required, regex: ^SRC-[A-Za-z0-9-]+$
  name: <human-readable>     # required
  type: <SourceType>         # required, see enum below
  owner: <team-slug>         # required
  priority: P0               # required, regex: ^P[0-3]$
  status: production         # required, one of: production | staging | deprecated
  partition_strategy: daily  # optional, default: monthly. See Partition strategies below.
  description: <text>        # optional

datasets:
  - name: <dataset-slug>     # required, regex: ^[a-z0-9_]+$
    description: <text>      # optional
    api_type: <ApiType>      # required, see enum below
    timestamp_field: <name>  # required for incremental; null for static
                             # REQUIRED for every dataset when partition_strategy=daily

    # --- socrata / socrata_geojson ---
    resource_id: erm2-nwe9   # required for api_type ∈ {socrata, socrata_geojson}
    domain: data.cityofnewyork.us

    # --- socrata_geojson only ---
    format: geojson          # required for api_type = socrata_geojson

    # --- open_meteo / generic_rest ---
    endpoint: https://...     # required for api_type ∈ {open_meteo, generic_rest}
    query_params:            # optional, free-form key/value
      latitude: 40.7143
      longitude: -74.006
```

### Enums

| Field | Allowed values |
|---|---|
| `source.type` | `rest_api_socrata` · `rest_api` · `geojson_static` |
| `source.partition_strategy` | `daily` · `monthly` (default: `monthly`) |
| `datasets[].api_type` | `socrata` · `socrata_geojson` · `open_meteo` · `generic_rest` |

### Partition strategies

The Bronze layer uses two GCS path layouts, chosen per source:

| Strategy | Path layout | Used by |
|---|---|---|
| `daily` | `bronze/raw/{source_id}/{dataset_name}/{YYYY-MM}/data_{YYYY-MM-DD}.json` + `manifest.json` | NYC 311, Open-Meteo |
| `monthly` | `bronze/raw/{source_id}/{dataset_name}/data_{YYYY-MM}.json` + `manifest_{YYYY-MM}.json` | NYPD, DCP |

When `partition_strategy: daily`, every dataset **must** declare a
`timestamp_field` — the loader uses it to split records into per-day files.
Monthly sources may also have `timestamp_field` (used for the fetch window)
but the loader does not split by it on write.

### Cross-field validation

Pydantic enforces these in `ingestion/config/source_config.py`:

| `api_type` | Required fields | Forbidden extras |
|---|---|---|
| `socrata` | `resource_id`, `domain` | `endpoint`, `format` |
| `socrata_geojson` | `resource_id`, `domain`, `format=geojson` | `endpoint` |
| `open_meteo` | `endpoint` | `resource_id`, `domain`, `format` |
| `generic_rest` | `endpoint` | `resource_id`, `domain`, `format` |

Unknown top-level fields are rejected (`extra="forbid"`).

## Adding a new source

1. Create `config/sources/<slug>.yaml` matching the schema above.
2. Add the entry to `docs/guide/data-sources.md` (human version).
3. Run `uv run python -c "from ingestion.config import load_all_sources; print(load_all_sources())"`
   to verify the loader picks it up and Pydantic validates it.

## Loading

```python
from ingestion.config import load_source_config, load_all_sources

# One source
cfg = load_source_config("SRC-NYC-311")
print(cfg.datasets[0].resource_id)  # "erm2-nwe9"

# All sources
all_sources = load_all_sources()
for sid, cfg in all_sources.items():
    print(sid, cfg.source.name, [d.name for d in cfg.datasets])
```

Override config directory for tests / alternative environments:

```bash
export NYC_UOIP_CONFIG_DIR=/path/to/alt/config/sources
```
