-- Silver contract: contracts/silver-contracts/silver_plow_zone_boundary.yaml
-- Grain: polygon
-- Row count expectation: {'exact': 82}
--   Full-table static pull, no incremental window. Any deviation from 82 is an upstream
-- schema change to escalate, not a partial load.
-- Primary key (informational only — Trino does not enforce PK/FK/UNIQUE): (entity_id)
-- unique: [['entity_id']]
-- accepted_values (city_area): ['East', 'North', 'South']
-- relationships:
--   geometry_repaired_count

CREATE TABLE IF NOT EXISTS silver_plow_zone_boundary (
    -- not_null
    -- unique
    entity_id VARCHAR,
    -- not_null
    -- cardinality: 25
    plow_zone VARCHAR,
    -- accepted_values: ['East', 'North', 'South']
    city_area VARCHAR,
    -- not_null
    -- note: ST_MakeValid'd. A collection, not a dissolved union — internal boundaries between
    -- polygons of the same zone remain (ADR 0010 D6).
    geometry_wkt VARCHAR,
    -- not_null
    -- note: True for 8/82 rows across zones A/B/B-D/D/Downtown/E/R/S — the OGC-invalid ones.
    geometry_repaired BOOLEAN,
    -- note: Null unless geometry_repaired=true. Area change from ST_MakeValid.
    area_delta_pct DOUBLE,
    -- not_null
    source_id VARCHAR,
    -- not_null
    loaded_at TIMESTAMP(6)
)
WITH (
    format = 'PARQUET',
    external_location = 's3a://{bucket}/silver/plow_zone_boundary/'
);
