-- Gold contract: contracts/gold-contracts/dim_snowfall_event.yaml
-- Grain: event
-- Row count expectation: {'exact_by_rule_version': {'v1-3cm-or-10d10cm': 99}}
-- Served by: BO-1, BO-3, BO-6
-- Primary key (informational only — Trino does not enforce PK/FK/UNIQUE): (event_id)
-- unique: [['event_id']]
-- accepted_values (event_rule_version): ['v1-3cm-or-10d10cm']
-- relationships:
--   COUNT(*) = 99
--   COUNT(*) WHERE is_scheduling_era = true = 59

CREATE TABLE IF NOT EXISTS dim_snowfall_event (
    -- not_null
    event_id VARCHAR,
    -- not_null
    start_date DATE,
    -- not_null
    end_date DATE,
    -- not_null
    total_snowfall_cm DOUBLE,
    -- not_null
    -- note: Restored — dropped from Silver→Gold with no record (launch doc 20260813 B5). Median
    -- 1.0 even under the rolling-accumulation criterion; do not narrate as multi-day.
    duration_days INTEGER,
    -- not_null
    -- note: Restored (B5) — the core number behind BO-3 ③'s subthreshold-accumulation finding
    -- (21-day accumulation held 76% of the aligned median vs. 26% for single-day peak). Without
    -- it Gold cannot reproduce that finding.
    peak_daily_snowfall_cm DOUBLE,
    -- note: Restored (B5). Nullable in Silver — see C16 for the coalesce rule severity_score's
    -- build depends on.
    min_temperature_c DOUBLE,
    -- not_null
    -- note: 1:1 from silver.snowfall_events.accum_flag (B5). True iff no single day in the event
    -- reached the single-day threshold — see that column's note.
    accum_flag BOOLEAN,
    -- not_null
    -- range: [0, 1]
    -- note: Normalized composite of snowfall + low temperature. Its variance is 99.4% between-
    -- event / 0.6% within-event — it sets how bad the storm scores overall, does not drive
    -- intra-event zone ordering (BO-6 caveat 1).
    severity_score DOUBLE,
    -- not_null
    -- note: Winter season label, e.g. '2015-2016'. Used to bucket into the pre/post scheduling-
    -- era split.
    snow_season VARCHAR,
    -- not_null
    -- note: True from 2015-12 (supply-side data start) onward — 59 of 99 events. Pre-era events
    -- feed M1's long-horizon training only; they never get a load score (BO-6 'effective
    -- analysis window').
    is_scheduling_era BOOLEAN,
    -- not_null
    -- accepted_values: ['v1-3cm-or-10d10cm']
    event_rule_version VARCHAR,
    -- not_null
    etl_run_id VARCHAR,
    -- not_null
    built_at TIMESTAMP(6),
    -- not_null
    source_max_ingest_date DATE
)
WITH (
    format = 'PARQUET',
    external_location = 's3a://{bucket}/gold/dim_snowfall_event/'
);
