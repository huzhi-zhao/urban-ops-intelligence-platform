# Backfill

A backfill processes an explicit historical window. It uses the same collection
or transformation code as scheduled work, but its window and retry scope are
chosen by the operator. Start with a small window in your own bucket.

For installation and storage setup, see [Getting Started](getting-started.md).

## Preview a Bronze window

```bash
uv run python -m scripts.backfill.main \
  --source SRC-WPG-311 \
  --start 2024-01-15 --end 2024-01-16 \
  --dry-run --max-workers 1
```

The window is `[start, end)`: January 15 is included, January 16 is not. A dry
run calls the API and reports counts but writes no object or manifest. It checks
source access, not your storage permissions.

| Option | Effect |
|---|---|
| `--source` | Registered source ID from [Data Sources](data-sources.md#registered-sources) |
| `--bucket` | Destination bucket; otherwise uses `S3_BUCKET_NAME` |
| `--dry-run` | Fetch and report without uploading |
| `--action fetch` | Fetch-only alternative |
| `--max-workers` | Slice concurrency; start at 1 for an experiment |

Remove `--dry-run` to write the same window after configuring your storage.
Override `S3_ENDPOINT_URL` when the host shell needs a different address from
containers. Expect a data object and manifest under:

```text
bronze/raw/SRC-WPG-311/service_requests/2024-01/
    data_2024-01-15.ndjson.gz
    manifest_2024-01-15.json
```

Use storage readback to verify both objects. Repeating the command replaces the
same paths; it does not add a second copy. An upstream revision can change their
contents. See [Bronze rerun semantics](ingestion-bronze.md#what-idempotence-means-here).

## Static and snapshot sources

Static sources fetch a whole reference table; dates are accepted by the shared
CLI but do not restrict the pull. For example, to preview the boundary source:

```bash
uv run python -m scripts.backfill.main \
  --source SRC-WPG-PLOW-ZONE \
  --start 2024-01-15 --end 2024-01-16 --dry-run
```

The same distinction applies to shifts and parking bans. They have no dedicated
scheduled ingestion DAG in this baseline; use their CLI entries for refreshes.

Snapshot sources expose only the current observation. Passing an old date does
not retrieve history. Use [Snapshot Collection](snapshot-collection.md) for
clearing status and inspect the source-specific collector for forecast snapshots.
A second same-day collection can replace the first, so routine historical repair
instructions must not be applied to snapshots.

## Use Airflow where a DAG exists

The repository includes `dag_backfill_weather_archive` for Bronze and
`dag_backfill_silver_weather_archive` / `dag_backfill_silver_service_request`
for Silver. There is no generic backfill DAG for every registered source.

Trigger the appropriate DAG in the Airflow UI with its declared parameters.
A typical service-request Silver run accepts:

```json
{"start": "2024-01-15", "end": "2024-01-16", "bucket": "uoip"}
```

One run processes one window; it need not process the entire history. The
weather Bronze DAG caps a requested window at 365 days. Read the actual DAG's
parameters rather than assuming every CLI option is exposed in the UI.

## Prepare Silver inputs first

Service-request ETL needs the corresponding Bronze windows and Silver zone
boundaries for spatial assignment. In a new deployment:

1. Collect the zone boundary source and run `etl_plow_zone_boundary.py`.
2. Collect a small service-request window and run its Silver backfill DAG.
3. Verify output, rejected rows and the partition-metadata synchronization.
4. Expand to the historical plan once the bounded run works.

Reference jobs for shifts, bans and clearing-address counts are explicit Spark
entry points under [spark/jobs/](../../spark/jobs/), not pre-existing Airflow DAGs.
Run them with the same driver/worker and S3A configuration as the scheduled jobs.
The [Silver guide](silver-etl.md) explains the contracts and output paths.

Weather archive loading and event rebuilding are distinct. Prepare the full
intended Silver weather series before rebuilding events. For H1, explicitly use
`snowfall_threshold_cm: 3.0` in the Silver weather backfill DAG; its exposed
default is 2.0. The job also uses the rolling accumulation rule. A short source
sample will not reproduce the published 99-event analysis.

## Load the historical analysis

The [311 Bronze plan](../../scripts/backfill/plan_wpg_311_backfill.sh) defines
all-day coverage from August 2016 and winter-only coverage before that. Its
checkpoint is a completed window; a failed window reruns on resume.

Do not run the whole plan as a connectivity test. It calls the API across years
of data and writes your bucket. Coordinate ingestion and audit activity so they
do not concurrently refresh the same windows.

Silver plan scripts are under [scripts/backfill/](../../scripts/backfill/).
After loading Silver and refreshing reference inputs, the full analytical path
is Gold dimensions and facts → M1 artifacts → scoring → quality checks → exports.
[Operations](operations.md#refresh-an-analysis) covers that order.

## Recover a partial failure

A failing Bronze slice does not abort every other slice. The summary records
which slices failed, and the CLI exits nonzero. Correct the cause and rerun the
bounded window. A successful slice's path will be replaced, not duplicated.

For Silver, verify the write strategy and rerun the failed window; do not lower
row-count or spatial gates merely to make the run green. Preserve the failed
run's evidence before replacing outputs. A missing snapshot day is an archive
gap, not a failed backfill to retry.
