-- Silver contract: contracts/silver-contracts/silver_snowfall_event.yaml
-- Grain: event
-- Row count expectation: {'exact_by_rule_version': {'v1-3cm-or-10d10cm': 159},
--                         'probe_scope_subset': {'v1-3cm-or-10d10cm': 99}}
--   159 over the full Bronze history (2000-01-01 .. 2026-08-18, every calendar month), measured
-- 2026-08-17. The 99 recorded here before that run was the *probe* scope, not the table's:
-- scripts/analysis/snowfall_events.py starts at FIRST_WINTER=2008 and reads only Nov 1 -> May 1.
-- Filtering the table to that scope returns exactly 99, so the pipeline agrees with the probe —
-- the mismatch was scope, not a defect. 59 of those 99 fall in the scheduling era (2015-12+) and
-- are the ones with a defined plow-zone rank; the rest exist for M1's long-horizon demand
-- training only.
-- Primary key (informational only — Trino does not enforce PK/FK/UNIQUE): (snowfall_event_id)
-- unique: [['snowfall_event_id']]
-- accepted_values (event_rule_version): ['v1-3cm-or-10d10cm']
-- relationships:
--   COUNT(*) WHERE event_rule_version = 'v1-3cm-or-10d10cm' = 159
--   COUNT(*) WHERE event_rule_version = 'v1-3cm-or-10d10cm'
--     AND start_date >= DATE '2008-11-01' AND MONTH(start_date) IN (11,12,1,2,3,4) = 99

CREATE TABLE IF NOT EXISTS silver_snowfall_event (
    -- not_null
    snowfall_event_id VARCHAR,
    -- not_null
    start_date DATE,
    -- not_null
    end_date DATE,
    -- not_null
    -- note: Median 1.0 day even with the rolling-accumulation criterion — an 'event' is
    -- functionally a snow day, not a multi-day storm. Do not narrate as multi-day.
    duration_days INTEGER,
    -- not_null
    total_snowfall_cm DOUBLE,
    -- not_null
    peak_daily_snowfall_cm DOUBLE,
    min_temperature_c DOUBLE,
    -- not_null
    -- accepted_values: ['v1-3cm-or-10d10cm']
    -- note: 🆕 Not yet in SNOWFALL_EVENT_SCHEMA. Semantic versioning per ADR 0010 §5 Q4: single-
    -- day >= 3cm OR 10-day rolling window >= 10cm. Add to the StructType before contract freeze
    -- (S3) — future threshold changes get a new domain value, never overwrite the meaning of an
    -- existing one.
    event_rule_version VARCHAR,
    -- not_null
    -- note: 🆕 Not yet in SNOWFALL_EVENT_SCHEMA (launch doc 20260813 B5). True iff no single day
    -- in the event met the single-day threshold on its own (peak_daily_snowfall_cm < threshold)
    -- — the event exists only because the rolling 10-day accumulation criterion pulled it in.
    -- Sourced from scripts.analysis.snowfall_events.Event.accum_triggered, same derivation. This
    -- is the column that turns the "4 plow operations matched no snowfall event until
    -- subthreshold accumulation was found" finding (BO-3 ③) into a queryable fact instead of a
    -- probe-only result.
    accum_flag BOOLEAN,
    -- not_null
    source_id VARCHAR,
    -- not_null
    loaded_at TIMESTAMP(6)
)
WITH (
    format = 'PARQUET',
    external_location = 's3a://{bucket}/silver/snowfall_event/'
);
