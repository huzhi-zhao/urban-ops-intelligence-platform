# Data Sources

UOIP combines three kinds of evidence: reported demand, planned residential
plowing, and weather. Geographic reference data connects them. Understanding
what each source measures is the first step in interpreting the results.

The registry below reflects the source configurations in this repository.
Dataset sizes are dated observations, not promises about today's upstream API.

## Registered sources

| Source ID | Dataset and publisher ID | Collection strategy | Role in H1 |
|---|---|---|---|
| `SRC-WPG-311` | `service_requests`, Winnipeg `u7f6-5326` | Daily files with a lookback | Reported demand |
| `SRC-WPG-PLOW-SHIFT` | `plow_shifts`, Winnipeg `tix9-r5tc` | Whole-table static refresh | Planned residential shifts |
| `SRC-WPG-PARKING-BAN` | `parking_bans`, Winnipeg `mfzv-893p` | Whole-table static refresh | Ban calendar and type |
| `SRC-WPG-PLOW-ZONE` | `plow_zones`, Winnipeg `39ur-higg` | Whole-table static refresh | Work-zone geometry |
| `SRC-WPG-SNOW` | `snow_clearing_status`, Winnipeg `g3p4-h83y` | Snapshot by collection date | Current address denominator and forward archive |
| `SRC-Open-Meteo` | `weather_archive` | Daily files | Historical event features |
| `SRC-Open-Meteo` | `weather_forecast` | Snapshot by collection date | Registered forecast input; not H1's evaluated model input |

The machine-readable definitions are in [config/sources/](../../config/sources/).
Read them for endpoints, fields and per-dataset strategy overrides. Small static
reference sources are refreshed explicitly, not by imaginary monthly ingest DAGs.

Winnipeg portal data and Open-Meteo data have their respective publisher terms;
the repository's Apache license does not relicense the datasets. See the
[Winnipeg portal](https://data.winnipeg.ca/), source configurations and
[contracts](../../contracts/) for source context.

## Demand is what residents report

Winnipeg 311 contains information requests as well as service requests. An
upstream probe counted approximately **18.3 million rows** in July 2026; that is
the portal's total, not this project's processed volume.

The fields also require interpretation:

| Field | Meaning for this analysis |
|---|---|
| `subject` | Broad business nature, including information and service requests |
| `reason` | Responsible department, despite its name |
| `type` | Request vocabulary used to identify winter categories |
| `case_id`, `interaction_id` | Composite interaction key; `case_id` alone is not unique |
| `open_date` | Winnipeg local wall-clock timestamp, without a UTC offset |
| `closed_date` | Ticket closure field; not verified as actual clearing completion |

The upstream vocabulary contains thousands of request types. Gold applies the
winter dictionary and normalizes channel labels. Silver preserves raw business
labels so a later dictionary change can be investigated without re-fetching.

### Coverage and reporting limitations

- Roughly 79% of **all** upstream requests lack geographic information, while
  about 80% of the winter subset carries it. State which population a percentage
  describes; never apply the whole-source rate to a filtered panel.
- Channel labels changed around 2022. `Self Service`, `Mobile` and `SMS In` map
  to `VOF` for comparison; the label change is not itself a behavior change.
- Neighbourhood names contain case variants. Raw spellings are retained, then
  normalized for analytical labels.
- Reporting frequency reflects access to and use of 311 as well as street
  conditions. Address normalization does not remove demographic reporting bias.
- Recent records can be updated after creation. A lookback helps absorb those
  changes; it is not a guarantee that every arbitrarily late update is captured.

The detailed measurements and their dates are in the
[source research](../dev/requirements/winnipeg-data-sources.md) and
[metric feasibility audit](../dev/requirements/metric-feasibility-audit.md).

## Scheduled work is a plan

The measured shift table has **418 rows: 19 operations × 22 zones**. Each
operation schedules zones into five 12-hour shifts. Rows within a shift share
planned times. They do not establish per-zone completion times or durations.

The 49 parking-ban records include different ban types. Only the 19 residential
bans correspond to the zone-level plow schedule; the others should not be treated
as missing shift data. The join is `plow_shifts.snow_ban_id` to `parking_bans.id`.

The boundary source contains **82 polygon records representing 25 zone values**.
`B/D`, `X` and `Downtown` have no corresponding shift history. The analysis
retains that distinction and scores the 22 scheduled zones. See
[Results](results.md) and [ADR 0008](../dev/adr/0008-plow-schedule-is-a-plan-not-a-record.md).

## Weather defines the event calendar

The current [weather configuration](../../config/sources/open_meteo.yaml) uses
one Winnipeg coordinate. It does **not** collect one historical series per zone
centroid. H1's weather factor is therefore constant across zones within an event.

Historical daily snowfall and temperature define snow events and supply M1's
event characteristics. Archive values may be revised by the publisher, so a
later rebuild can move event boundaries or counts. Preserve the rule version and
input snapshot when reproducing a published analysis.

Forecast snapshots preserve what was available on each collection day. They
cannot be backfilled by treating historical archive values as old forecasts.
The forecast source's registration does not demonstrate that the H1 scoring
chain consumed it; the published evaluation uses archive features.

## Clearing snapshots preserve a new observation

The clearing-status source exposes current address-level state without a record
time field. A measured pull contained about 238,000 records. The collector saves
each day's observation under its collection date, beginning August 2, 2026 in
the recorded deployment.

One selected snapshot supplies address counts for zone normalization. The
forward archive could support later analysis of changing status, but daily
observations cannot reveal an exact completion time between two collections.
Missing days cannot be recovered from today's endpoint.
[Snapshot Collection](snapshot-collection.md) explains the operational consequences.

## Source scope and analysis scope

| Stage | Scope in the recorded baseline |
|---|---|
| Upstream 311 endpoint | Approximately 18.3 million records at the exploratory measurement date |
| Bronze historical plan | All days from August 1, 2016; November–March winter windows before that, starting November 2008 |
| Silver v1.0 checkpoint | 12,477,414 service-request rows across 4,878 day partitions |
| Gold demand panel | Winter categories, with explicit event and geography eligibility |
| M1 input / stored prediction panel | 2,178 cells / 1,298 cells per model version |
| Complete three-factor scores | 374 cells with matched scheduling evidence |

These are different populations. Do not reconcile the portal's all-time count
directly against Silver or interpret a filtered panel as dropped records.
The [backfill plan](../../scripts/backfill/plan_wpg_311_backfill.sh) defines the
collection windows; the [changelog](../../CHANGELOG.md) records the v1.0 checkpoint.

Administrative labels are derived from Silver requests; their crosswalk weights
are request shares, not polygon-area shares. Seed dictionaries enter the Gold
build from [config/seeds/](../../config/seeds/); the build definitions live in
[scripts/gold/build_gold.py](../../scripts/gold/build_gold.py). Proposed census controls and additional road or traffic
sources must not be described as implemented bias correction. Their adoption
status and activation work belong in the
[source portfolio](../dev/requirements/data-source-portfolio.md).

## Add a source

1. Define its endpoint, fields and partition strategy in `config/sources/`.
   Check dataset overrides as well as source defaults.
2. Define the source contract and interaction key, if applicable.
3. Add a thin `scripts/backfill/backfill_<slug>.py` dispatch entry, reusing the
   existing fetcher where possible. Add a fetcher if the API shape is new.
4. Choose a schedule only if the source needs one; use the independent collector
   for observations that cannot be replayed.
5. Verify a small read-only pull, then a write to your own bucket. Update this
   registry when the source is actually available.

For Socrata, use stable ordering when paginating. A `type` filter on `%ICE%`
also matches words such as “Service”; classification requires tested vocabulary
rather than a loose substring. [Data Quality](data-quality.md) explains how those
mistakes reach otherwise plausible results.

## Next steps

[Ingestion and Bronze](ingestion-bronze.md) explains storage;
[Silver ETL](silver-etl.md) follows the records into typed tables.
