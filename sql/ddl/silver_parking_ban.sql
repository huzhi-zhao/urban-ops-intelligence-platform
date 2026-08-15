-- Silver contract: contracts/silver-contracts/silver_parking_ban.yaml
-- Grain: ban
-- Row count expectation: {'exact': 49}
--   19 of 49 have a matching plow_shift row (38.8%); the other 30 are bans without a city-
-- wide clearing operation — expected, not a gap.
-- Primary key (informational only — Trino does not enforce PK/FK/UNIQUE): (id)
-- unique: [['id']]
-- relationships:
--   COUNT(*) = 49
--   COUNT(DISTINCT id) matched by silver_plow_shift.snow_ban_id = 19

CREATE TABLE IF NOT EXISTS silver_parking_ban (
    -- not_null
    id VARCHAR,
    -- not_null
    ban_start_utc TIMESTAMP(6),
    ban_end_utc TIMESTAMP(6),
    -- not_null
    ban_type_id VARCHAR,
    -- not_null
    source_id VARCHAR,
    -- not_null
    loaded_at TIMESTAMP(6)
)
WITH (
    format = 'PARQUET',
    external_location = 's3a://{bucket}/silver/parking_ban/'
);
