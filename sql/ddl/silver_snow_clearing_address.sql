-- Silver contract: contracts/silver-contracts/silver_snow_clearing_address.yaml
-- Grain: plow_zone
-- Row count expectation: {'exact': 25}
--   One row per plow_zone per snapshot_date processed. dim_plow_zone reads the latest
-- snapshot_date only.
-- Primary key (informational only — Trino does not enforce PK/FK/UNIQUE): (plow_zone,
-- snapshot_date)
-- unique: [['plow_zone', 'snapshot_date']]
-- relationships:
--   COUNT(*) WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM
--   silver_snow_clearing_address) = 25
--   SUM(address_count) WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM
--   silver_snow_clearing_address) <= 237867  -- upper bound: total g3p4-h83y address
--   records with location, some may fall outside all 25 polygons

CREATE TABLE IF NOT EXISTS silver_snow_clearing_address (
    -- not_null
    -- cardinality: 25
    -- note: Point-in-polygon join of every g3p4-h83y address record against
    -- silver_plow_zone_boundary — same join family as silver_service_request.plow_zone
    -- (request_point_in_zone), not a separate implementation.
    plow_zone VARCHAR,
    -- not_null
    -- note: Count of address points whose geometry falls inside this zone's polygon.
    address_count INTEGER,
    -- not_null
    -- note: The g3p4-h83y ingest_date this count was computed from — SRC-WPG-SNOW is a snapshot
    -- source (config/sources/), so this is a point-in-time count, not a time series.
    snapshot_date DATE,
    -- not_null
    source_id VARCHAR,
    -- not_null
    loaded_at TIMESTAMP(6)
)
WITH (
    format = 'PARQUET',
    external_location = 's3a://{bucket}/silver/snow_clearing_address/'
);
