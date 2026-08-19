-- dim_region_crosswalk — plow_zone -> (ward | neighbourhood), weighted.
--
-- Direction is fixed zone -> label. weight is the share of that zone's winter
-- service requests landing on the label, so SUM(weight) within every
-- (plow_zone, label_type) group is 1 by construction — the build gates it
-- anyway, because "by construction" stops being true the moment a NULL label
-- sneaks into the numerator but not the denominator.
--
-- 🔴 The calibration window is the **last three snow seasons**, not a ten-year
-- mean (O2). Rank drift between the first and second half of the history is
-- rho = +0.591, with zones V and M moving more than a whole shift; a ten-year
-- weight would average across a re-ordering that has already happened. The
-- window is written into the calibration_window column rather than left
-- implicit in the date literals below: which window a number came from is
-- part of the number.
--
-- ⚠️ The two date literals and the calibration_window string are one fact
-- written twice and must be changed together. They are literals rather than
-- parameters because they are business semantics (which seasons were
-- calibrated on), not an execution date — and they double as the R1 partition
-- predicate, ~540 day partitions rather than 4,878.
-- 🟡 O5 stands: three seasons is an assumption, not a measured optimum. S6
-- compares weight stability across window lengths before this is locked into
-- the contract's accepted values.
--
-- Winter is decided by dim_service_type.winter_category being non-null — one
-- classification, built once, never a LIKE clause repeated per consumer.
-- label_id is casefolded to match dim_admin_label's canonical form (O10);
-- 242 raw neighbourhood values fold to 237.
WITH winter_type AS (
    SELECT "type"
    FROM dim_service_type
    WHERE winter_category IS NOT NULL
),

winter_request AS (
    SELECT
        s.plow_zone,
        LOWER(TRIM(s.ward_raw)) AS ward_id,
        LOWER(TRIM(s.neighbourhood_raw)) AS neighbourhood_id,
        s.loaded_at
    FROM {{ silver }}.silver_service_request AS s
    INNER JOIN winter_type AS w ON s."type" = w."type"
    WHERE
        s.open_date_local >= DATE '2023-11-01'
        AND s.open_date_local < DATE '2026-05-01'
        AND s.plow_zone IS NOT NULL
),

labelled AS (
    SELECT
        plow_zone,
        'ward' AS label_type,
        ward_id AS label_id,
        loaded_at
    FROM winter_request
    WHERE ward_id IS NOT NULL AND ward_id <> ''

    UNION ALL

    SELECT
        plow_zone,
        'neighbourhood' AS label_type,
        neighbourhood_id AS label_id,
        loaded_at
    FROM winter_request
    WHERE neighbourhood_id IS NOT NULL AND neighbourhood_id <> ''
),

counted AS (
    SELECT
        plow_zone,
        label_type,
        label_id,
        COUNT(*) AS request_count,
        MAX(loaded_at) AS loaded_at
    FROM labelled
    GROUP BY plow_zone, label_type, label_id
),

weighted AS (
    SELECT
        c.plow_zone,
        c.label_type,
        c.label_id,
        c.loaded_at,
        CAST(c.request_count AS DOUBLE)
        / SUM(c.request_count) OVER (PARTITION BY c.plow_zone, c.label_type) AS weight,
        -- Ties break on the lexicographically smallest label_id (O3). Explicit
        -- so that is_dominant is reproducible; a silent tie-break is as
        -- dangerous as none.
        ROW_NUMBER() OVER (
            PARTITION BY c.plow_zone, c.label_type
            ORDER BY c.request_count DESC, c.label_id ASC
        ) AS dominance_rank
    FROM counted AS c
)

SELECT
    w.plow_zone,
    w.label_type,
    w.label_id,
    w.weight,
    w.dominance_rank = 1 AS is_dominant,
    '2023-2024..2025-2026' AS calibration_window,
    '{etl_run_id}' AS etl_run_id,
    TIMESTAMP '{built_at}' AS built_at,
    CAST(MAX(w.loaded_at) OVER () AS DATE) AS source_max_ingest_date
FROM weighted AS w
