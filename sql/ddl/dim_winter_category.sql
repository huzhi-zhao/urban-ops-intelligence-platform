-- Gold contract: contracts/gold-contracts/dim_winter_category.yaml
-- Grain: winter_category
-- Row count expectation: {'exact': 7}
--   6 effective classes (SNOW, FROZEN, PLOW, SANDING, WINDROW, ICE_CONTROL — 275,282 rows,
-- zero false positives) + 1 kept for portability (PLOUGH — matches 0 rows against Winnipeg's
-- `type` values, retained because the loose %ICE% pattern it replaced was 99.8% false
-- positive on a different city's data; dropping PLOUGH entirely would repeat that mistake
-- for whichever city has British spelling upstream). See dim_service_type.yaml's
-- winter_category note for the source measurement.
-- Served by: BO-1
-- Primary key (informational only — Trino does not enforce PK/FK/UNIQUE): (winter_category)
-- unique: [['winter_category']]
-- relationships:
--   COUNT(*) = 7
--   COUNT(*) WHERE is_effective = true = 6

CREATE TABLE IF NOT EXISTS dim_winter_category (
    -- not_null
    -- accepted_values: ['SNOW', 'FROZEN', 'PLOW', 'SANDING', 'WINDROW', 'ICE_CONTROL', 'PLOUGH']
    winter_category VARCHAR,
    -- not_null
    -- note: SQL LIKE pattern matched against silver_service_request.type, e.g. '%SNOW%'. One
    -- category, one pattern — no compound OR patterns.
    keyword_pattern VARCHAR,
    -- not_null
    -- note: False only for PLOUGH (0 observed matches for the currently deployed city).
    -- fact_service_request_zone_event's panel_cells count (A4) multiplies 1298 by COUNT(*) WHERE
    -- is_effective = true, i.e. 6, not 7.
    is_effective BOOLEAN,
    -- not_null
    etl_run_id VARCHAR,
    -- not_null
    built_at TIMESTAMP(6),
    -- not_null
    source_max_ingest_date DATE
)
WITH (
    format = 'PARQUET',
    external_location = 's3a://{bucket}/gold/dim_winter_category/'
);
