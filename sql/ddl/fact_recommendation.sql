-- Gold contract: contracts/gold-contracts/fact_recommendation.yaml
-- Grain: (event_id, plow_zone, model_version)
-- Row count expectation: {}
--   Backtest output — one row per (event, zone, model_version) the recommendation layer was
-- run against. Not required to cover the full 1,298-cell panel; runs against scored events
-- (fact_winter_event_zone_load.score_status = scored) only. model_version joined into the PK
-- (launch doc 20260813 B4, aligning with F5's PK) so a rank_model retrain does not silently
-- overwrite the previous version's backtest — BO-8's acceptance criterion is "backtest
-- against historical events", which requires the old backtest to still be queryable after a
-- retrain, the exact case ADR 0010 D5 was written to cover.
-- Served by: BO-8
-- Primary key (informational only — Trino does not enforce PK/FK/UNIQUE): (event_id,
-- plow_zone, model_version)
-- unique: [['event_id', 'plow_zone', 'model_version']]
-- relationships:
--   attribution_rule_id -> dim_recommendation_rules.rule_id
--   event_id -> fact_winter_event_zone_load.event_id WHERE score_status = 'scored'
-- forbidden_columns (ADR 0010 D2 — admin units never enter a fact key): ['region_type',
-- 'ward', 'neighbourhood']

CREATE TABLE IF NOT EXISTS fact_recommendation (
    -- not_null
    -- relationships -> dim_snowfall_event.event_id
    event_id VARCHAR,
    -- not_null
    -- relationships -> dim_plow_zone.plow_zone
    plow_zone VARCHAR,
    -- not_null
    -- relationships -> fact_request_forecast.model_version
    -- note: Which M1 version's forecast drove this recommendation — never overwritten in place,
    -- same discipline as fact_request_forecast.model_version.
    model_version VARCHAR,
    -- not_null
    -- note: This event's zones ordered by the model-driven recommendation.
    rank_model INTEGER,
    -- not_null
    -- note: Same event's zones ordered by the 'historical average request volume' baseline (§4.4
    -- evaluation protocol) — stored on the same row, never recomputed live.
    rank_baseline INTEGER,
    -- not_null
    -- note: rank_baseline - rank_model. 'Beats baseline' is an internal target only as of
    -- 2026-08-11 — not an external claim (BO-8 §0.2.2).
    rank_delta INTEGER,
    -- not_null
    -- relationships -> dim_recommendation_rules.rule_id
    attribution_rule_id VARCHAR,
    -- not_null
    -- note: Must not read as an unfairness claim — BO-8 表述纪律 #3. Attributes to schedule ordering
    -- / weather severity / predicted demand only, never to policy failure.
    attribution_text VARCHAR,
    -- not_null
    etl_run_id VARCHAR,
    -- not_null
    built_at TIMESTAMP(6),
    -- not_null
    source_max_ingest_date DATE
)
WITH (
    format = 'PARQUET',
    external_location = 's3a://{bucket}/gold/fact_recommendation/'
);
