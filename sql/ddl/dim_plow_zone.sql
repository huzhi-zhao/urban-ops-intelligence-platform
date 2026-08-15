-- Gold contract: contracts/gold-contracts/dim_plow_zone.yaml
-- Grain: plow_zone
-- Row count expectation: {'exact': 25}
-- Served by: BO-2, BO-4
-- Primary key (informational only — Trino does not enforce PK/FK/UNIQUE): (plow_zone)
-- unique: [['plow_zone']]
-- accepted_values (has_plow_schedule): [True, False]
-- relationships:
--   COUNT(*) = 25
--   COUNT(*) WHERE has_plow_schedule = false = 3
--   COUNT(*) WHERE geometry_repaired = true = 8

CREATE TABLE IF NOT EXISTS dim_plow_zone (
    -- not_null
    -- cardinality: 25
    plow_zone VARCHAR,
    -- not_null
    -- note: Union of silver_plow_zone_boundary rows sharing this plow_zone, kept as a geometry
    -- *collection* (internal boundaries remain) — not ST_Union. ADR 0010 D6.
    geometry_wkt VARCHAR,
    -- not_null
    -- note: False for exactly 3 values: B/D, X, Downtown. ADR 0010 §5 Q5 — used to filter, never
    -- to physically exclude these zones from fact tables.
    has_plow_schedule BOOLEAN,
    -- not_null
    -- note: BO-6's normalization denominator. Sourced from
    -- silver_snow_clearing_address.address_count at its latest snapshot_date, not directly from
    -- SRC-WPG-SNOW — S4 build order: silver_plow_zone_boundary -> silver_snow_clearing_address
    -- -> dim_plow_zone, same point-in-polygon join family as silver_service_request.plow_zone.
    -- Point-in-time snapshot used to normalize a decade of historical events — a declared, not a
    -- hidden, limitation (see address_count_snapshot_date). The three no-schedule zones' counts
    -- are already known (B/D 11,150 · X 2,590 · Downtown 574), so all 25 rows being non-null is
    -- achievable.
    address_count INTEGER,
    -- not_null
    -- note: Not optional (ADR 0010 D3). The date g3p4-h83y was pulled; makes the snapshot-vs-
    -- history mismatch visible as data, not a slide footnote.
    address_count_snapshot_date DATE,
    -- not_null
    -- note: True for exactly 8/25 zones (A, B, B/D, D, Downtown, E, R, S — the ones with an OGC-
    -- invalid source polygon).
    geometry_repaired BOOLEAN,
    -- note: Null unless geometry_repaired = true.
    area_delta_pct DOUBLE,
    -- not_null
    etl_run_id VARCHAR,
    -- not_null
    built_at TIMESTAMP(6),
    -- not_null
    source_max_ingest_date DATE
)
WITH (
    format = 'PARQUET',
    external_location = 's3a://{bucket}/gold/dim_plow_zone/'
);
