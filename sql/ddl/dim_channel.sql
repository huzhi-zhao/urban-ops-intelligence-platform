-- Gold contract: contracts/gold-contracts/dim_channel.yaml
-- Grain: channel
-- Row count expectation: {'exact': 15}
-- Served by: BO-1
-- Primary key (informational only — Trino does not enforce PK/FK/UNIQUE): (channel_raw)
-- unique: [['channel_raw']]
-- relationships:
--   COUNT(*) = 15

CREATE TABLE IF NOT EXISTS dim_channel (
    -- not_null
    channel_raw VARCHAR,
    -- not_null
    -- note: Self Service + Mobile + SMS In all map to VOF (2022 reporting-boundary migration,
    -- not a behavior change).
    channel_normalized VARCHAR,
    -- not_null
    -- note: False for channel_raw in {Self Service, Mobile, SMS In}: their pre/post-2022
    -- channel-structure comparison is invalid, though total-volume comparison remains valid.
    is_comparable_pre_2022 BOOLEAN,
    -- not_null
    etl_run_id VARCHAR,
    -- not_null
    built_at TIMESTAMP(6),
    -- not_null
    source_max_ingest_date DATE
)
WITH (
    format = 'PARQUET',
    external_location = 's3a://{bucket}/gold/dim_channel/'
);
