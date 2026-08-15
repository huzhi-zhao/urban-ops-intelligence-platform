-- Silver contract: contracts/silver-contracts/silver_service_request.yaml
-- Grain: interaction
-- Row count expectation: {'full_table_min': 18000000, 'winter_subset_approx': 275282}
--   Upstream 18,375,656 as of 2026-08-09; grows daily. Track ratio (winter subset ≈ 1.5% of
-- full table), not the absolute count.
-- Primary key (informational only — Trino does not enforce PK/FK/UNIQUE): (case_id, interaction_id)
-- unique: [['case_id', 'interaction_id']]
-- accepted_values (geo_match_status): ['matched', 'unmatched', 'no_geo']

CREATE TABLE IF NOT EXISTS silver_service_request (
    -- not_null
    -- note: Not unique alone — see primary_key.
    case_id VARCHAR,
    -- not_null
    interaction_id VARCHAR,
    -- not_null
    -- note: Source is a Socrata floating timestamp (America/Winnipeg wall-clock, no offset).
    open_ts_utc TIMESTAMP(6),
    -- note: closed_date semantics (case closure vs. actual clearing) unverified — BO-1 §2.5. Do
    -- not derive M2 features from it beyond duration.
    closed_ts_utc TIMESTAMP(6),
    -- not_null
    -- note: Raw 311 type string. winter_category / priority are resolved in Gold via
    -- dim_service_type, not here.
    "type" VARCHAR,
    -- not_null
    -- note: Raw channel string. Normalization (Self Service + Mobile + SMS In → VOF) happens in
    -- Gold via dim_channel, not here — business semantics stay out of Silver.
    channel_raw VARCHAR,
    -- not_null
    -- note: Required per CLAUDE.md 'Escalate to human' guardrail: spatial NULL-rate alerts must
    -- use the has_geo subset as denominator, not the full table (79% of all rows have no
    -- coordinates by upstream design).
    has_geo BOOLEAN,
    -- not_null
    -- accepted_values: ['matched', 'unmatched', 'no_geo']
    -- note: Three-value, not a bare NULL (design doc §3, row 3). 'unmatched' means has_geo=true
    -- but the point fell outside all 25 plow_zone polygons (measured 0.1% of geo-bearing winter
    -- rows) — excluded from scoring per BO-4, kept for descriptive stats.
    geo_match_status VARCHAR,
    -- cardinality: 25
    -- note: Populated only when geo_match_status = matched. Point-in-polygon join against
    -- silver_plow_zone_boundary, not a text lookup on ward/neighbourhood.
    plow_zone VARCHAR,
    -- note: Free-text field on the source, not a dimension key. Casefold happens in Gold's
    -- dim_admin_label, not here.
    ward_raw VARCHAR,
    -- note: 242 raw values incl. 5 casefold-collision pairs (e.g. Mcmillan/McMillan) — see BO-1.
    -- Do not GROUP BY this column directly.
    neighbourhood_raw VARCHAR,
    -- not_null
    source_id VARCHAR,
    -- not_null
    loaded_at TIMESTAMP(6),
    -- Partition column: last in the list, as the Hive connector requires.
    -- Declared after the contract's column order for that reason alone.
    -- not_null
    -- note: Partition column. Must be the *local* calendar date, not the UTC date — partitioning
    -- on UTC date moves late-evening requests into the next day (design doc §3, row 2).
    open_date_local DATE
)
WITH (
    format = 'PARQUET',
    external_location = 's3a://{bucket}/silver/service_request/',
    partitioned_by = ARRAY['open_date_local']
);
