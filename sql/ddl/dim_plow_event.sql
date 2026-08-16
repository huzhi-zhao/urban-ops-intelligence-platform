-- Gold contract: contracts/gold-contracts/dim_plow_event.yaml
-- Grain: plow_event
-- Row count expectation: {'exact': 19}
-- Served by: BO-2
-- Primary key (informational only — Trino does not enforce PK/FK/UNIQUE): (plow_event_id)
-- unique: [['plow_event_id']]
-- relationships:
--   COUNT(*) = 19
--   COUNT(*) WHERE is_aligned = true = 17
--   COUNT(*) WHERE is_aligned = false = 2
--   COUNT(DISTINCT matched_snowfall_event_id) = COUNT(*) WHERE matched_snowfall_event_id
--   IS NOT NULL  -- guards fan-out when F6 joins in via this FK, per launch doc B1

CREATE TABLE IF NOT EXISTS dim_plow_event (
    -- not_null
    -- note: Sourced from silver_plow_shift.snow_ban_id (one plow_event per distinct ban that
    -- triggered a city-wide operation) — the only place this mapping was previously implicit.
    plow_event_id VARCHAR,
    -- not_null
    -- relationships -> fact_parking_ban.ban_id
    ban_id VARCHAR,
    -- not_null
    -- note: MIN(shift_start_utc) across this plow_event's 22 shifts — the operation's start, for
    -- ordering plow_events chronologically.
    first_shift_start_utc TIMESTAMP(6),
    -- relationships -> dim_snowfall_event.snowfall_event_id
    -- note: NULL for the two known-unaligned operations (2021-01-07, 2026-02-26). Unique when
    -- not null — see relationships test; this is what makes it safe to join into
    -- fact_winter_event_zone_load without fan-out.
    matched_snowfall_event_id VARCHAR,
    -- not_null
    -- note: True iff matched_snowfall_event_id is not null. Explicit column so 'unaligned' is
    -- queryable without an IS NULL check on a nullable FK.
    is_aligned BOOLEAN,
    -- not_null
    etl_run_id VARCHAR,
    -- not_null
    built_at TIMESTAMP(6),
    -- not_null
    source_max_ingest_date DATE
)
WITH (
    format = 'PARQUET',
    external_location = 's3a://{bucket}/gold/dim_plow_event/'
);
