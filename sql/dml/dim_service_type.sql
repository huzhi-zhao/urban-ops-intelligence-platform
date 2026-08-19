-- chunked_by: calendar year (2008..2026)
-- combine: set union (each chunk inserts only `type` values no earlier chunk saw)
--
-- dim_service_type — every distinct 311 `type` ever observed, classified.
--
-- 🔴 Chunked for the same reason dim_admin_label is: enumerating the distinct
-- values of a real column over the whole Silver history is exactly the
-- 4,878-partition scan gold-sql.md R1 forbids (O13). The grain contains no
-- date, so the chunks overlap — the same `type` appears in most of the 19 of
-- them — and the anti-join against the rows earlier chunks already inserted is
-- what keeps the primary key unique. It works because the chunks run as
-- separate sequential statements. Do not merge them into one.
--
-- Two seed files reach this file as *string* placeholders rather than as
-- tables, and both are deliberate:
--
--   winter_category_order (placeholder)  the CSV's row order, which IS the multi-hit
--     arbitration rule (design §6.2 decision 2, re-decided 2026-08-19 to
--     most-specific-first: WINDROW > ICE_CONTROL > PLOW > PLOUGH > SANDING >
--     FROZEN > SNOW). dim_winter_category's schema is frozen and has no
--     priority column, and row order on a Parquet read is not a guarantee, so
--     the order has to arrive explicitly. Writing a CASE ladder here instead
--     would put city semantics in SQL, which the guardrails forbid.
--     Measured: all 13 multi-hit types contain SNOW; with SNOW first, WINDROW
--     (1 type) and ICE_CONTROL (3 types) resolve to zero types each and their
--     panel cells read as an empty data fact rather than as a rule's outcome.
--
--   service_type_keywords (placeholder)  the 9 priority patterns, `priority;regex;weight`
--     joined by `|`. `_vof` is deliberately NOT among them: 48 winter types
--     carry it and it is a *channel* marker (dim_channel's VOF), not a
--     priority one — see launch doc §4.5.
--
-- Both are substituted inside string literals so the whole file stays
-- lintable. A file with a bare {placeholder} in a clause parses nowhere, and
-- loses the SELECT * and partition-predicate checks along with it.
--
-- 🔴 winter_category is NULL for non-winter types and that is correct. The
-- build-time gate is "every observed type has a row", not "every row has a
-- category" — conflating them makes the first build fail forever (design
-- §6.2 decision 1).
WITH observed AS (
    SELECT
        s."type" AS service_type,
        MAX(s.loaded_at) AS loaded_at
    FROM silver_service_request AS s
    WHERE
        s.open_date_local >= DATE '{chunk_start}'
        AND s.open_date_local < DATE '{chunk_end}'
        AND s."type" IS NOT NULL
        AND TRIM(s."type") <> ''
    GROUP BY s."type"
),

fresh AS (
    SELECT
        o.service_type,
        o.loaded_at
    FROM observed AS o
    WHERE
        NOT EXISTS (
            SELECT 1
            FROM dim_service_type AS d
            WHERE d."type" = o.service_type
        )
),

category AS (
    -- First match wins, "first" being the seed's row order. MIN_BY over the
    -- position in that order states the arbitration once, for every multi-hit
    -- type, instead of once per class.
    SELECT
        f.service_type,
        MIN_BY(
            w.winter_category,
            ARRAY_POSITION(SPLIT('{winter_category_order}', ','), w.winter_category)
        ) AS winter_category
    FROM fresh AS f
    INNER JOIN dim_winter_category AS w
        ON UPPER(f.service_type) LIKE w.keyword_pattern
    GROUP BY f.service_type
),

keywords AS (
    SELECT
        CAST(SPLIT(t.entry, ';')[1] AS INTEGER) AS match_priority,
        SPLIT(t.entry, ';')[2] AS pattern_regex,
        CAST(SPLIT(t.entry, ';')[3] AS INTEGER) AS priority_weight
    FROM UNNEST(SPLIT('{service_type_keywords}', '|')) AS t (entry)
),

priority AS (
    -- Lowest match_priority wins: the spelled-out form (10/20/30) beats the
    -- abbreviated (11/21/31) beats the bare one (12/22/32), so a type reading
    -- "Priority 2 ... P3 Route" resolves the way a human reads it.
    SELECT
        f.service_type,
        MIN_BY(k.priority_weight, k.match_priority) AS priority_weight
    FROM fresh AS f
    INNER JOIN keywords AS k ON REGEXP_LIKE(f.service_type, k.pattern_regex)
    GROUP BY f.service_type
)

SELECT
    f.service_type AS "type",
    c.winter_category,
    p.priority_weight,
    '{etl_run_id}' AS etl_run_id,
    TIMESTAMP '{built_at}' AS built_at,
    CAST(f.loaded_at AS DATE) AS source_max_ingest_date
FROM fresh AS f
LEFT JOIN category AS c ON f.service_type = c.service_type
LEFT JOIN priority AS p ON f.service_type = p.service_type
