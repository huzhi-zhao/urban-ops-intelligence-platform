-- dim_plow_zone — 25 zones from 82 boundary polygons.
--
-- geometry_wkt is a geometry *collection*: the member polygons keep their
-- internal boundaries (ADR 0010 D6), so this is a textual WKT assembly rather
-- than ST_Union. Gold needs no geometry functions at all — the spatial work
-- finished in Silver (ADR 0009) — and assembling the string keeps it that way.
-- The ORDER BY is not cosmetic: without it the concatenation order varies
-- between runs and a rebuild is no longer byte-identical.
--
-- has_plow_schedule is derived from whether the zone appears in the shift
-- table, never from a hardcoded list of the three that do not. Hardcoding
-- 'B/D' / 'X' / 'Downtown' would put city semantics in SQL and would go
-- unnoticed the year a fourth zone loses its schedule.
WITH latest_snapshot AS (
    SELECT MAX(snapshot_date) AS snapshot_date FROM {{ silver }}.silver_snow_clearing_address
),

addresses AS (
    -- silver_snow_clearing_address is a flat full overwrite holding exactly
    -- one collection day (CLAUDE.md). MAX(snapshot_date) is therefore a no-op
    -- today — it is written anyway so that the day the table becomes a real
    -- time series, address_count does not silently double. The build asserts
    -- COUNT(DISTINCT snapshot_date) = 1 separately (build_gold extra_gates).
    SELECT
        silver_snow_clearing_address.plow_zone,
        silver_snow_clearing_address.address_count,
        silver_snow_clearing_address.snapshot_date
    FROM {{ silver }}.silver_snow_clearing_address
    INNER JOIN
        latest_snapshot
        ON silver_snow_clearing_address.snapshot_date = latest_snapshot.snapshot_date
),

scheduled AS (
    SELECT DISTINCT plow_zone FROM {{ silver }}.silver_plow_shift
)

SELECT
    b.plow_zone,
    'GEOMETRYCOLLECTION (' || ARRAY_JOIN(
        ARRAY_AGG(
            b.geometry_wkt
            ORDER BY b.entity_id
        ), ', '
    ) || ')' AS geometry_wkt,
    MAX(s.plow_zone) IS NOT NULL AS has_plow_schedule,
    MAX(a.address_count) AS address_count,
    MAX(a.snapshot_date) AS address_count_snapshot_date,
    BOOL_OR(b.geometry_repaired) AS geometry_repaired,
    -- Largest deviation in either direction, keeping its sign: MAX(ABS(..))
    -- would report a shrink as a growth.
    MAX_BY(b.area_delta_pct, ABS(b.area_delta_pct)) AS area_delta_pct,
    '{etl_run_id}' AS etl_run_id,
    TIMESTAMP '{built_at}' AS built_at,
    CAST(MAX(b.loaded_at) AS DATE) AS source_max_ingest_date
FROM {{ silver }}.silver_plow_zone_boundary AS b
LEFT JOIN addresses AS a ON b.plow_zone = a.plow_zone
LEFT JOIN scheduled AS s ON b.plow_zone = s.plow_zone
GROUP BY b.plow_zone
