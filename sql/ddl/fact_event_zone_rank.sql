-- Gold contract: contracts/gold-contracts/fact_event_zone_rank.yaml
-- Grain: (plow_event_id, plow_zone)
-- Row count expectation: {'exact': 418}
--   19 events x 22 zones, zero missing.
-- Served by: BO-2
-- Primary key (informational only — Trino does not enforce PK/FK/UNIQUE): (plow_event_id,
-- plow_zone)
-- unique: [['plow_event_id', 'plow_zone']]
-- accepted_values (shift_number): [1, 2, 3, 4, 5]
-- relationships:
--   COUNT(*) = 418
--   COUNT(*) WHERE rank_factor = 0 = 0
--   COUNT(DISTINCT plow_event_id WHERE matched_snowfall_event_id IS NOT NULL) >= 17
--   COUNT(DISTINCT matched_snowfall_event_id) = COUNT(DISTINCT plow_event_id) WHERE
--   matched_snowfall_event_id IS NOT NULL  -- no two plow_events collapse into one
--   snowfall_event; fan-out guard for B1
-- forbidden_columns (ADR 0010 D2 — admin units never enter a fact key): ['region_type',
-- 'ward', 'neighbourhood']

CREATE TABLE IF NOT EXISTS fact_event_zone_rank (
    -- not_null
    -- note: One of the 19 city-wide plow operations, distinct from event_id (the ~99 snowfall
    -- events).
    plow_event_id VARCHAR,
    -- not_null
    -- cardinality: 22
    -- relationships -> dim_plow_zone.plow_zone
    plow_zone VARCHAR,
    -- not_null
    -- accepted_values: [1, 2, 3, 4, 5]
    shift_number INTEGER,
    -- range: [0.2, 1]
    -- note: Formula: shift_number / 5 (fixed denominator, not min-max over observed shift
    -- numbers — launch doc 20260813 A1). Range is [0.2, 1], never 0 — shift_number's domain
    -- floor of 1 makes 0 structurally unreachable, so the 'never 0' assertion holds by
    -- construction instead of by convention. NULL for any plow-event outside the 19 known ones
    -- or for dates before 2015-12 — a NULL here is the *only* correct missing representation
    -- (BO-6).
    rank_factor DOUBLE,
    -- relationships -> dim_snowfall_event.event_id
    -- note: NULL for the two known-unaligned operations (2021-01-07, 2026-02-26) — expected,
    -- must be surfaced not hidden. Alignment rate must be >= 17/19 (89.5%). Denormalized copy of
    -- dim_plow_event.matched_snowfall_event_id (launch doc 20260813 B1) — this table's own
    -- uniqueness test below is the fan-out guard; a consumer joining F6 in via this column must
    -- join through dim_plow_event, not this table directly, or verify the same uniqueness
    -- itself.
    matched_snowfall_event_id VARCHAR,
    -- not_null
    etl_run_id VARCHAR,
    -- not_null
    built_at TIMESTAMP(6),
    -- not_null
    source_max_ingest_date DATE
)
WITH (
    format = 'PARQUET',
    external_location = 's3a://{bucket}/gold/fact_event_zone_rank/'
);
