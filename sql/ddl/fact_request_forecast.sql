-- Gold contract: contracts/gold-contracts/fact_request_forecast.yaml
-- Grain: (snowfall_event_id, plow_zone, model_version)
-- Row count expectation: {}
--   One full panel (<=1298 rows, scheduling-era subset only — see predicted_count note) per
-- distinct model_version. Grows with every retrain, by design — old versions are never
-- overwritten.
-- Served by: BO-1
-- Primary key (informational only — Trino does not enforce PK/FK/UNIQUE): (snowfall_event_id,
-- plow_zone, model_version)
-- unique: [['snowfall_event_id', 'plow_zone', 'model_version']]
-- relationships:
--   snowfall_event_id -> dim_snowfall_event.snowfall_event_id
--   plow_zone -> dim_plow_zone.plow_zone

CREATE TABLE IF NOT EXISTS fact_request_forecast (
    -- not_null
    -- relationships -> dim_snowfall_event.snowfall_event_id
    snowfall_event_id VARCHAR,
    -- not_null
    -- relationships -> dim_plow_zone.plow_zone
    plow_zone VARCHAR,
    -- not_null
    -- note: Never overwritten in place — every retrain is a new row set, enabling BO-8's
    -- historical backtest requirement.
    model_version VARCHAR,
    -- not_null
    -- note: SUM(request_count) across all 6 effective winter_category values for this
    -- (snowfall_event_id, plow_zone) — see grain_relationship_to_f1 above.
    predicted_count DOUBLE,
    -- note: Seasonal-naive baseline for the same (snowfall_event_id, plow_zone) — the
    -- "historical average request volume" method (§4.4 evaluation protocol). Stored on the
    -- same row per ADR 0010 D5's "no model metric without a baseline, and never recomputed
    -- live" discipline (already applied to fact_recommendation.rank_baseline; this closes
    -- the same gap for M1's own MAE).
    -- Null only for the earliest events with no prior history to average over.
    baseline_count DOUBLE,
    -- note: Backfilled once ground truth (fact_service_request_zone_event) is known for the
    -- event; null for future/unelapsed events.
    actual_count INTEGER,
    -- not_null
    etl_run_id VARCHAR,
    -- not_null
    built_at TIMESTAMP(6),
    -- not_null
    source_max_ingest_date DATE
)
WITH (
    format = 'PARQUET',
    external_location = 's3a://{bucket}/gold/fact_request_forecast/'
);
