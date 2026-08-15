-- Gold contract: contracts/gold-contracts/dim_region_crosswalk.yaml
-- Grain: (plow_zone, label_type, label_id)
-- Served by: BO-4
-- Primary key (informational only — Trino does not enforce PK/FK/UNIQUE): (plow_zone,
-- label_type, label_id)
-- unique: [['plow_zone', 'label_type', 'label_id']]
-- relationships:
--   SUM(weight) GROUP BY (plow_zone, label_type) ≈ 1.0 (± float tolerance)
--   plow_zone -> dim_plow_zone.plow_zone
--   (label_type, label_id) -> dim_admin_label.(label_type, label_id)

CREATE TABLE IF NOT EXISTS dim_region_crosswalk (
    -- not_null
    -- relationships -> dim_plow_zone.plow_zone
    plow_zone VARCHAR,
    -- not_null
    -- accepted_values: ['ward', 'neighbourhood']
    label_type VARCHAR,
    -- not_null
    -- relationships -> dim_admin_label.label_id
    label_id VARCHAR,
    -- not_null
    -- note: Share of this zone's winter service requests falling on this label. sum(weight) over
    -- label_type for a fixed plow_zone = 1.
    weight DOUBLE,
    -- not_null
    -- note: Explicit column, not computed downstream via ORDER BY weight LIMIT 1 (ADR 0010 D4) —
    -- any downstream single-value lookup that recomputes this is a defect per ADR 0009 §2.3.
    is_dominant BOOLEAN,
    -- not_null
    -- note: 🟡 ADR 0010 §5 O5: initial value is the trailing-5-season assumption, NOT a validated
    -- constant. rho=+0.591 between first/second-half rank only proves drift exists, not that 5
    -- seasons is the right scale. S6 must compare weight stability across window lengths before
    -- this value is locked in the contract's accepted-values list.
    calibration_window VARCHAR,
    -- not_null
    etl_run_id VARCHAR,
    -- not_null
    built_at TIMESTAMP(6),
    -- not_null
    source_max_ingest_date DATE
)
WITH (
    format = 'PARQUET',
    external_location = 's3a://{bucket}/gold/dim_region_crosswalk/'
);
