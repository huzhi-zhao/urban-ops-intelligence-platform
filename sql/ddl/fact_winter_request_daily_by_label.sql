-- Gold contract: contracts/gold-contracts/fact_winter_request_daily_by_label.yaml
-- Grain: (date, label_type, label_id)
-- Row count expectation: {'approx': 1600000}
--   ~6,600 days x 252 labels in the dense case — second-largest Gold table after F1. No
-- partitioning/freshness declared here beyond C6's blanket rebuild note; revisit if this
-- table is ever built and the row count is confirmed.
-- Served by: BO-1
-- Primary key (informational only — Trino does not enforce PK/FK/UNIQUE): (date, label_type,
-- label_id)
-- unique: [['date', 'label_type', 'label_id']]
-- accepted_values (label_type): ['ward', 'neighbourhood']

CREATE TABLE IF NOT EXISTS fact_winter_request_daily_by_label (
    -- not_null
    "date" DATE,
    -- not_null
    -- accepted_values: ['ward', 'neighbourhood']
    label_type VARCHAR,
    -- not_null
    -- relationships -> dim_admin_label.label_id
    label_id VARCHAR,
    -- not_null
    request_count INTEGER,
    -- not_null
    etl_run_id VARCHAR,
    -- not_null
    built_at TIMESTAMP(6),
    -- not_null
    source_max_ingest_date DATE
)
WITH (
    format = 'PARQUET',
    external_location = 's3a://{bucket}/gold/fact_winter_request_daily_by_label/'
);
