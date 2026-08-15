-- Silver contract: contracts/silver-contracts/silver_weather_forecast.yaml
-- Grain: hour
-- Row count expectation: {}
--   No fixed expectation — grows one ingest_date partition per storage-node collection day.
-- Freshness is the meaningful check, not row count.
-- Primary key (informational only — Trino does not enforce PK/FK/UNIQUE): (time_utc, ingest_date)

CREATE TABLE IF NOT EXISTS silver_weather_forecast (
    -- not_null
    time_utc TIMESTAMP(6),
    -- not_null
    -- note: Partition column of time_utc, YYYY-MM-DD. Distinct from ingest_date — the same
    -- time_utc reappears under multiple ingest_date partitions as the forecast is revised; that
    -- is the forecast-revision series, not duplication.
    "date" VARCHAR,
    temperature_2m DOUBLE,
    precipitation DOUBLE,
    snowfall DOUBLE,
    windspeed_10m DOUBLE,
    -- not_null
    source_id VARCHAR,
    -- not_null
    loaded_at TIMESTAMP(6),
    -- Partition column: last in the list, as the Hive connector requires.
    -- Declared after the contract's column order for that reason alone.
    -- not_null
    -- note: Collection date (snapshot partition), not a fact about the forecast content.
    ingest_date VARCHAR
)
WITH (
    format = 'PARQUET',
    external_location = 's3a://{bucket}/silver/weather_forecast/',
    partitioned_by = ARRAY['ingest_date']
);
