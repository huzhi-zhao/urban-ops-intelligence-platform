-- Gold contract: contracts/gold-contracts/dim_recommendation_rules.yaml
-- Grain: rule_id
-- Served by: BO-8
-- Primary key (informational only — Trino does not enforce PK/FK/UNIQUE): (rule_id)
-- unique: [['rule_id']]

CREATE TABLE IF NOT EXISTS dim_recommendation_rules (
    -- not_null
    rule_id VARCHAR,
    -- not_null
    -- note: Human-readable attribution template, e.g. 'this zone scores high mainly due to
    -- schedule ordering, not snowfall volume'.
    template_text VARCHAR,
    -- not_null
    -- note: True for rules used when M1's output or an input is missing — the deterministic
    -- degrade path, not the primary logic.
    is_fallback BOOLEAN,
    -- not_null
    etl_run_id VARCHAR,
    -- not_null
    built_at TIMESTAMP(6),
    -- not_null
    source_max_ingest_date DATE
)
WITH (
    format = 'PARQUET',
    external_location = 's3a://{bucket}/gold/dim_recommendation_rules/'
);
