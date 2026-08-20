-- chunked_by: calendar year (2008..2026), on dim_snowfall_event.start_date
-- combine: disjoint (an event starts in exactly one calendar year, so each
--   panel cell is produced by exactly one chunk — no anti-join needed)
--
-- fact_service_request_zone_event (F1) — the full 13,068-cell panel.
--
-- 22 zones (has_plow_schedule only) x 99 events x 6 effective winter
-- categories. The skeleton is built by CROSS JOIN from the three dimensions
-- and the counts are LEFT JOINed onto it: **a cell with no requests is stored
-- as 0**, not omitted. M1 reads the zero as a training signal, and a table
-- that only holds non-zero cells would make "no requests" indistinguishable
-- from "event not covered".
--
-- 🔴 The event list comes from dim_snowfall_event and nowhere else. Its
-- 2008-11 / winter-months filter is what makes this table 13,068 rows instead
-- of 20,988, and re-deriving the events from Silver here would put that
-- filter in two places, one of which would eventually be wrong without
-- raising (design §6.6, §6.10).
--
-- 🔴 Chunked for R1/O13: a request-side scan across the whole history is
-- 4,878 partitions and times out. Chunking by the event's calendar year keeps
-- each chunk near one year of partitions. The request window runs 45 days
-- past the chunk end because an event may start in December and finish in
-- January — the longest observed event is 11 days, so 45 is slack, and the
-- BETWEEN against the event's own dates is what decides attribution. Without
-- the tail the January days of a New Year event would be silently dropped.
--
-- A request belongs to an event when its local open date falls inside
-- [start_date, end_date] — the same rule the BO-3 probe panels use
-- (scripts/analysis/snowfall_events.py: event_days).
--
-- weighted_request_count sums dim_service_type.priority_weight, defaulting to
-- 1 where the type carries no parseable priority. 1 rather than 0: an
-- unparsed priority means "we could not read it", and dropping such a request
-- out of the weighted measure while keeping it in request_count would make
-- the two columns disagree for a reason nobody could see.
WITH event AS (
    SELECT
        e.snowfall_event_id,
        e.start_date,
        e.end_date
    FROM dim_snowfall_event AS e
    WHERE
        e.start_date >= DATE '{chunk_start}'
        AND e.start_date < DATE '{chunk_end}'
),

zone AS (
    SELECT z.plow_zone
    FROM dim_plow_zone AS z
    WHERE z.has_plow_schedule
),

category AS (
    SELECT c.winter_category
    FROM dim_winter_category AS c
    WHERE c.is_effective
),

skeleton AS (
    SELECT
        e.snowfall_event_id,
        z.plow_zone,
        c.winter_category
    FROM event AS e
    CROSS JOIN zone AS z
    CROSS JOIN category AS c
),

winter_type AS (
    SELECT
        t."type",
        t.winter_category,
        COALESCE(t.priority_weight, 1) AS priority_weight
    FROM dim_service_type AS t
    WHERE t.winter_category IS NOT NULL
),

request AS (
    SELECT
        s."type",
        s.plow_zone,
        s.open_date_local,
        s.loaded_at
    FROM {{ silver }}.silver_service_request AS s
    WHERE
        s.open_date_local >= DATE '{chunk_start}'
        AND s.open_date_local < DATE '{chunk_end}' + INTERVAL '45' DAY
        AND s.plow_zone IS NOT NULL
),

counted AS (
    SELECT
        e.snowfall_event_id,
        r.plow_zone,
        t.winter_category,
        CAST(COUNT(*) AS INTEGER) AS request_count,
        CAST(SUM(t.priority_weight) AS DOUBLE) AS weighted_request_count
    FROM request AS r
    INNER JOIN winter_type AS t ON r."type" = t."type"
    INNER JOIN event AS e
        ON r.open_date_local BETWEEN e.start_date AND e.end_date
    GROUP BY e.snowfall_event_id, r.plow_zone, t.winter_category
),

lineage AS (
    -- One value for the whole chunk. Taken from the request window rather
    -- than from `counted`, so that a chunk whose cells are all zero still
    -- carries a real upstream date instead of NULL.
    SELECT MAX(r.loaded_at) AS loaded_at
    FROM request AS r
)

SELECT
    sk.snowfall_event_id,
    sk.plow_zone,
    sk.winter_category,
    COALESCE(c.request_count, 0) AS request_count,
    COALESCE(c.weighted_request_count, 0.0) AS weighted_request_count,
    '{etl_run_id}' AS etl_run_id,
    TIMESTAMP '{built_at}' AS built_at,
    CAST(l.loaded_at AS DATE) AS source_max_ingest_date
FROM skeleton AS sk
CROSS JOIN lineage AS l
LEFT JOIN counted AS c
    ON
        sk.snowfall_event_id = c.snowfall_event_id
        AND sk.plow_zone = c.plow_zone
        AND sk.winter_category = c.winter_category
