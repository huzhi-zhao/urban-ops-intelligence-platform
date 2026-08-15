-- Silver contract: contracts/silver-contracts/silver_weather_archive.yaml
-- Grain: day
-- Row count expectation: {'min': 6500}
--   18 winters back to 2008-06-17, daily grain, single citywide point (multi-point zone
-- centroids are a Gold-layer BO-3 addition, not stored here yet).
-- Primary key (informational only — Trino does not enforce PK/FK/UNIQUE): (weather_date)
-- unique: [['weather_date']]

CREATE TABLE IF NOT EXISTS silver_weather_archive (
    -- not_null
    weather_date DATE,
    snowfall_sum_cm DOUBLE,
    temperature_2m_min_c DOUBLE,
    temperature_2m_max_c DOUBLE,
    -- not_null
    source_id VARCHAR,
    -- not_null
    loaded_at TIMESTAMP(6),
    -- Partition column: last in the list, as the Hive connector requires.
    -- Declared after the contract's column order for that reason alone.
    -- not_null
    -- note: Partition column, YYYY-MM-DD string form of weather_date.
    "date" VARCHAR
)
WITH (
    format = 'PARQUET',
    external_location = 's3a://{bucket}/silver/weather_archive/',
    partitioned_by = ARRAY['date']
);
