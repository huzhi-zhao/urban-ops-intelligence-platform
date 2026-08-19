-- chunked_by: calendar year (2008..2026)
-- combine: set union (each chunk inserts only labels no earlier chunk saw)
--
-- dim_admin_label — 15 wards + 237 neighbourhoods.
--
-- 🔴 This is the one dimension that has to enumerate values across the whole
-- Silver history, which is exactly the scan gold-sql.md R1 forbids: 4,878
-- partitions in one statement times out (O13). It is therefore chunked by
-- calendar year like F8 — but unlike F8 its grain contains no date, so the
-- chunks overlap: the same ward appears in all 19 of them. The anti-join
-- against the rows already inserted is what keeps the primary key unique.
-- It works because chunks run as separate sequential statements, so each one
-- sees what its predecessors committed. Do not "optimise" it into one
-- statement.
--
-- label_id stores the casefolded value (O10, signed off 2026-08-19): a join
-- key must be canonical. 242 raw neighbourhood values fold to 237 — five case
-- collisions, of which McMillan/Mcmillan is the one that would otherwise split
-- a reporting unit in two. Display casing is Superset's problem; storing a
-- second readable form here would be the same concept written twice.
WITH observed AS (
    SELECT DISTINCT
        'ward' AS label_type,
        LOWER(TRIM(s.ward_raw)) AS label_id,
        s.loaded_at
    FROM silver_service_request AS s
    WHERE
        s.open_date_local >= DATE '{chunk_start}'
        AND s.open_date_local < DATE '{chunk_end}'
        AND s.ward_raw IS NOT NULL
        AND TRIM(s.ward_raw) <> ''

    UNION DISTINCT

    SELECT DISTINCT
        'neighbourhood' AS label_type,
        LOWER(TRIM(s.neighbourhood_raw)) AS label_id,
        s.loaded_at
    FROM silver_service_request AS s
    WHERE
        s.open_date_local >= DATE '{chunk_start}'
        AND s.open_date_local < DATE '{chunk_end}'
        AND s.neighbourhood_raw IS NOT NULL
        AND TRIM(s.neighbourhood_raw) <> ''
)

SELECT
    o.label_type,
    o.label_id,
    '{etl_run_id}' AS etl_run_id,
    TIMESTAMP '{built_at}' AS built_at,
    CAST(MAX(o.loaded_at) AS DATE) AS source_max_ingest_date
FROM observed AS o
WHERE
    NOT EXISTS (
        SELECT 1
        FROM dim_admin_label AS d
        WHERE d.label_type = o.label_type AND d.label_id = o.label_id
    )
GROUP BY o.label_type, o.label_id
