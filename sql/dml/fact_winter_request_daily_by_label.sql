-- chunked_by: calendar year (2008..2026), on silver_service_request.open_date_local
-- combine: disjoint (the grain contains the date, so chunks cannot overlap)
--
-- fact_winter_request_daily_by_label (F8) — 141,377 rows.
--
-- Winter requests per (day, ward|neighbourhood). This is the only Gold table
-- whose grain contains a date and the only one that spans the whole Silver
-- history, which is why it is the one R2 was written for: the year appears in
-- each chunk's WHERE and nowhere else. It does not become a column, a
-- partition key or a dimension — see gold-sql.md R2.
--
-- 🔴 141,377, not the ≈1.6 M the design estimated (O14, measured 2026-08-19).
-- That figure assumed a dense 6,600-day x 252-label panel. It is not dense:
-- ward_raw and neighbourhood_raw are NULL on 77.22% of rows, almost exactly
-- the rows that have no coordinates (77.21%) — the same missingness, not two
-- independent ones. Only ~23% of Silver can carry a label at all.
--
-- 🔴 Unlike F1 this table stores only the (day, label) pairs that actually
-- occur. There is no skeleton and no zero row: the grain has no event to make
-- a panel over, and materialising 6,600 x 252 empty cells would be inventing
-- 1.5 M rows to say nothing. F8 serves BO-1's descriptive slices and is **not
-- on the scoring chain** — it shares no column with F1 by design.
--
-- Winter is decided by dim_service_type.winter_category being non-null: one
-- classification, built once. label_id is casefolded to match
-- dim_admin_label's canonical form (O10), and joined to that table so the FK
-- is true by construction rather than by argument.
WITH winter_type AS (
    SELECT t."type"
    FROM dim_service_type AS t
    WHERE t.winter_category IS NOT NULL
),

request AS (
    SELECT
        s.open_date_local,
        LOWER(TRIM(s.ward_raw)) AS ward_id,
        LOWER(TRIM(s.neighbourhood_raw)) AS neighbourhood_id,
        s.loaded_at
    FROM {{ silver }}.silver_service_request AS s
    INNER JOIN winter_type AS w ON s."type" = w."type"
    WHERE
        s.open_date_local >= DATE '{chunk_start}'
        AND s.open_date_local < DATE '{chunk_end}'
),

labelled AS (
    SELECT
        open_date_local,
        'ward' AS label_type,
        ward_id AS label_id,
        loaded_at
    FROM request
    WHERE ward_id IS NOT NULL AND ward_id <> ''

    UNION ALL

    SELECT
        open_date_local,
        'neighbourhood' AS label_type,
        neighbourhood_id AS label_id,
        loaded_at
    FROM request
    WHERE neighbourhood_id IS NOT NULL AND neighbourhood_id <> ''
)

SELECT
    l.open_date_local AS "date",
    l.label_type,
    l.label_id,
    CAST(COUNT(*) AS INTEGER) AS request_count,
    '{etl_run_id}' AS etl_run_id,
    TIMESTAMP '{built_at}' AS built_at,
    CAST(MAX(MAX(l.loaded_at)) OVER () AS DATE) AS source_max_ingest_date
FROM labelled AS l
INNER JOIN dim_admin_label AS d
    ON l.label_type = d.label_type AND l.label_id = d.label_id
GROUP BY l.open_date_local, l.label_type, l.label_id
