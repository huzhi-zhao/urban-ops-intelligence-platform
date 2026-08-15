-- Gold contract: contracts/gold-contracts/fact_plow_shift.yaml
-- Grain: shift
-- Row count expectation: {'exact': 418}
-- Served by: BO-2
-- Primary key (informational only — Trino does not enforce PK/FK/UNIQUE): (shift_id)
-- unique: [['shift_id']]
-- relationships:
--   COUNT(*) = 418
--   plow_event_id -> dim_plow_event.plow_event_id

CREATE TABLE IF NOT EXISTS fact_plow_shift (
    -- not_null
    shift_id VARCHAR,
    -- not_null
    -- relationships -> dim_plow_event.plow_event_id
    plow_event_id VARCHAR,
    -- not_null
    -- relationships -> dim_plow_zone.plow_zone
    plow_zone VARCHAR,
    -- not_null
    -- accepted_values: [1, 2, 3, 4, 5]
    shift_number INTEGER,
    -- not_null
    shift_start_utc TIMESTAMP(6),
    -- not_null
    -- note: Planned end of window, not completion. Do not derive a duration or completion-time
    -- metric from this — ADR 0008.
    shift_end_utc TIMESTAMP(6),
    -- not_null
    etl_run_id VARCHAR,
    -- not_null
    built_at TIMESTAMP(6),
    -- not_null
    source_max_ingest_date DATE
)
WITH (
    format = 'PARQUET',
    external_location = 's3a://{bucket}/gold/fact_plow_shift/'
);
