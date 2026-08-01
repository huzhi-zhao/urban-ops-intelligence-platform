# Source Configuration

> Machine-readable source registry. **Single source of truth** for any code that
> touches an upstream data source — backfill scripts, the snapshot collector,
> Airflow DAGs, Spark jobs, dashboards.
>
> The human-readable companion is `docs/guide/data-sources.md`.
> When you change either, change both.

## Layout

```
config/sources/
  README.md                      # this file
  winnipeg_snow_clearing.yaml    # SRC-WPG-SNOW
```

One file per source. A single source may contain multiple `datasets`.

> Files from a retired deployment against another city's open data may still be
> present. They are being removed and are not part of the current deployment.

## Schema

```yaml
source:
  id: SRC-XXX                # required, regex: ^SRC-[A-Za-z0-9-]+$
  name: <human-readable>     # required
  type: <SourceType>         # required, see enum below
  owner: <team-slug>         # required
  priority: P0               # required, regex: ^P[0-3]$
  status: production         # required, one of: production | staging | deprecated
  partition_strategy: daily  # optional, default: monthly. See below.
  description: <text>        # optional

datasets:
  - name: <dataset-slug>     # required, regex: ^[a-z0-9_]+$
    description: <text>      # optional
    api_type: <ApiType>      # required, see enum below
    timestamp_field: <name>  # required for every dataset when strategy = daily
                             # forbidden when strategy = static
                             # may be null when strategy = snapshot

    # --- socrata / socrata_geojson ---
    resource_id: g3p4-h83y   # required for api_type ∈ {socrata, socrata_geojson}
    domain: data.winnipeg.ca

    # --- socrata_geojson only ---
    format: geojson          # required for api_type = socrata_geojson

    # --- open_meteo / generic_rest ---
    endpoint: https://...    # required for api_type ∈ {open_meteo, generic_rest}
    query_params:            # optional, free-form key/value
      latitude: 49.895
      longitude: -97.138
```

### Enums

| Field | Allowed values |
|---|---|
| `source.type` | `rest_api_socrata` · `rest_api` · `geojson_static` |
| `source.partition_strategy` | `daily` · `monthly` (default) · `static` · `snapshot` |
| `datasets[].api_type` | `socrata` · `socrata_geojson` · `open_meteo` · `generic_rest` |

### Partition strategies

The strategy chooses the Bronze path layout under `bronze/raw/{source_id}/{dataset}/`
in object storage. All data files are gzipped NDJSON; manifests stay uncompressed.

| Strategy | Path layout | For |
|---|---|---|
| `daily` | `{YYYY-MM}/data_{YYYY-MM-DD}.ndjson.gz` + `{YYYY-MM}/manifest_{YYYY-MM-DD}.json` | High-volume event streams |
| `monthly` | `data_{YYYY-MM}.ndjson.gz` + `manifest_{YYYY-MM}.json` | Lower-volume event streams |
| `static` | `data_static.ndjson.gz` + `manifest_static.json` | Reference data with no time dimension |
| `snapshot` | `ingest_date={YYYY-MM-DD}/data.ndjson.gz` + `ingest_date={YYYY-MM-DD}/manifest.json` | Overwrite-in-place upstreams that keep no history |

`daily` splits records into per-day files by the date portion of `timestamp_field`, so
that field is mandatory on every dataset. Monthly sources may also declare it (it is used
for the fetch window) but the loader does not split by it on write.

`snapshot` partitions by **collection date rather than record date**, and is the only
strategy that permits `timestamp_field: null`. `static` would write one fixed filename and
overwrite yesterday — exactly what a snapshot source must avoid. Snapshot sources are
collected by a standalone timer, not by Airflow; see `docs/guide/snapshot-collection.md`.

> ⚠️ The `.gz` extension is mandatory and `Content-Encoding` must never be set. Spark's
> `s3a://` reader picks its codec from the file extension and ignores HTTP headers.

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
3. Run the loader to verify Pydantic accepts it:

```bash
uv run python -c "from ingestion.config import load_all_sources; print(load_all_sources())"
```

## Loading

```python
from ingestion.config import load_source_config, load_all_sources

# One source
cfg = load_source_config("SRC-WPG-SNOW")
print(cfg.datasets[0].resource_id)  # "g3p4-h83y"

# All sources
for sid, cfg in load_all_sources().items():
    print(sid, cfg.source.name, [d.name for d in cfg.datasets])
```

Never hardcode a source id from memory — read it from here.

Override the config directory for tests or alternative environments:

```bash
export NYC_UOIP_CONFIG_DIR=/path/to/alt/config/sources
```
