-- Gold contract: contracts/gold-contracts/fact_parking_ban.yaml
-- Grain: ban
-- Row count expectation: {'exact': 49}
-- Served by: BO-2
-- Primary key (informational only — Trino does not enforce PK/FK/UNIQUE): (ban_id)
-- unique: [['ban_id']]
-- relationships:
--   COUNT(*) = 49
--   COUNT(*) WHERE matched_plow_event_id IS NOT NULL = 19
--   COUNT(*) WHERE matched_plow_event_id IS NULL = 30

CREATE TABLE IF NOT EXISTS fact_parking_ban (
    -- not_null
    ban_id VARCHAR,
    -- not_null
    ban_start_utc TIMESTAMP(6),
    ban_end_utc TIMESTAMP(6),
    -- not_null
    ban_type_id VARCHAR,
    -- relationships -> dim_plow_event.plow_event_id
    -- note: NULL for 30/49 rows — a ban without a city-wide clearing operation. This is
    -- semantics, not a data gap. Never inner-join on this.
    matched_plow_event_id VARCHAR,
    -- not_null
    etl_run_id VARCHAR,
    -- not_null
    built_at TIMESTAMP(6),
    -- not_null
    source_max_ingest_date DATE
)
WITH (
    format = 'PARQUET',
    external_location = 's3a://{bucket}/gold/fact_parking_ban/'
);
