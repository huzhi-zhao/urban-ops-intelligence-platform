-- Gold contract: contracts/gold-contracts/dim_admin_label.yaml
-- Grain: (label_type, label_id)
-- Row count expectation: {'exact': 252}
--   15 ward + 237 neighbourhood, casefolded. Raw neighbourhood text has 242 values incl. 5
-- casefold-collision pairs — collapse before this table, not after.
-- Served by: BO-1, BO-4, BO-6
-- Primary key (informational only — Trino does not enforce PK/FK/UNIQUE): (label_type, label_id)
-- unique: [['label_type', 'label_id']]
-- accepted_values (label_type): ['ward', 'neighbourhood']
-- relationships:
--   COUNT(*) WHERE label_type = 'ward' = 15
--   COUNT(*) WHERE label_type = 'neighbourhood' = 237

CREATE TABLE IF NOT EXISTS dim_admin_label (
    -- not_null
    -- accepted_values: ['ward', 'neighbourhood']
    label_type VARCHAR,
    -- not_null
    -- note: Casefolded. e.g. 'Daniel Mcintyre' and 'Daniel McIntyre' collapse to one row.
    label_id VARCHAR,
    -- not_null
    etl_run_id VARCHAR,
    -- not_null
    built_at TIMESTAMP(6),
    -- not_null
    source_max_ingest_date DATE
)
WITH (
    format = 'PARQUET',
    external_location = 's3a://{bucket}/gold/dim_admin_label/'
);
