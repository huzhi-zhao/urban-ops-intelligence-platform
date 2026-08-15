-- Gold contract: contracts/gold-contracts/dim_service_type.yaml
-- Grain: type
-- Row count expectation: {'max': 3563}
--   311's raw type cardinality. Not every value needs a row day one — only ones observed in
-- ingested data — but every observed value must resolve (see O2 below).
-- Served by: BO-1
-- Primary key (informational only — Trino does not enforce PK/FK/UNIQUE): (type)
-- unique: [['type']]
-- accepted_values (priority_weight): [1, 2, 3]
-- relationships:
--   every distinct silver_service_request.type value present in a scored fact table
--   resolves to a row here — enforced as a build-time LEFT ANTI JOIN failure in the S4
--   dim_service_type build job, per O2

CREATE TABLE IF NOT EXISTS dim_service_type (
    -- not_null
    -- note: Raw 311 'type' string, verbatim.
    "type" VARCHAR,
    -- relationships -> dim_winter_category.winter_category
    -- note: Null for non-winter types. Six effective keyword classes hit 275,282 rows with zero
    -- false positives (%PLOUGH% matches 0 rows — kept only for portability). The loose %ICE%
    -- pattern was 99.8% false positive and is the reason this is a seed table, not a LIKE clause
    -- in Spark. 🟡 A `type` value can match more than one dim_winter_category keyword_pattern
    -- (e.g. containing both 'SNOW' and 'ICE CONTROL'). This column holds exactly one value, so
    -- the build job must adjudicate: first-match-wins in dim_winter_category's row order (SNOW >
    -- FROZEN > PLOW > SANDING > WINDROW > ICE_CONTROL > PLOUGH). Not yet validated against how
    -- often the ambiguity actually occurs — flagged, not resolved, by launch doc 20260813 A3.
    winter_category VARCHAR,
    -- accepted_values: [1, 2, 3]
    -- note: Parsed from 'Pr 2' / 'Priority 2' / 'P2' / '_vof' suffix variants embedded in type.
    -- P1=3, P2=2, P3=1 per BO-1's weighted-count definition.
    priority_weight INTEGER,
    -- not_null
    etl_run_id VARCHAR,
    -- not_null
    built_at TIMESTAMP(6),
    -- not_null
    source_max_ingest_date DATE
)
WITH (
    format = 'PARQUET',
    external_location = 's3a://{bucket}/gold/dim_service_type/'
);
