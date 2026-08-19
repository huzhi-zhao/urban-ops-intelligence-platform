-- dim_plow_event — 19 city-wide plow operations.
--
-- One plow_event per distinct silver_plow_shift.snow_ban_id: the ban that
-- triggered the operation is the operation's identity, a mapping that was
-- implicit everywhere before this table existed.
--
-- 🔴 The alignment lag is **7 days** and that number is load-bearing. The
-- BO-3 probe was run at both 3d and 7d and they produce different name lists;
-- the ledger carried the wrong four dates for a while because of exactly that
-- (CLAUDE.md, corrected 2026-08-09). At 7d, 17 of the 19 operations align and
-- 2 do not — 2021-01-07 and 2026-02-26. The other two never-aligned dates
-- (2021-11-27, 2022-11-24) fall outside these 19 operations altogether.
--
-- The match takes the snowfall event that ended *latest* before the operation
-- started. Two operations collapsing onto one snowfall event would double
-- L3's F6 join; that is guarded by the DDL's fan-out relationship gate rather
-- than assumed away here.
--
-- ban_id comes from silver_parking_ban, not from snow_ban_id directly: the
-- join is what proves the FK resolves. An operation whose ban is missing
-- upstream drops out and fails the COUNT(*) = 19 gate, which is the intended
-- way to find out — a NULL in a not_null FK column would not raise anywhere.
WITH operations AS (
    SELECT
        s.snow_ban_id,
        MIN(s.shift_start_utc) AS first_shift_start_utc,
        MAX(s.loaded_at) AS loaded_at
    FROM silver_plow_shift AS s
    WHERE s.snow_ban_id IS NOT NULL
    GROUP BY s.snow_ban_id
),

with_ban AS (
    SELECT
        o.snow_ban_id AS plow_event_id,
        b.id AS ban_id,
        o.first_shift_start_utc,
        GREATEST(o.loaded_at, b.loaded_at) AS loaded_at
    FROM operations AS o
    INNER JOIN silver_parking_ban AS b ON o.snow_ban_id = b.id
),

aligned AS (
    SELECT
        o.plow_event_id,
        MAX_BY(e.snowfall_event_id, e.end_date) AS matched_snowfall_event_id
    FROM with_ban AS o
    INNER JOIN dim_snowfall_event AS e
        ON
            e.start_date <= CAST(o.first_shift_start_utc AS DATE)
            AND e.end_date >= CAST(o.first_shift_start_utc AS DATE) - INTERVAL '7' DAY
    GROUP BY o.plow_event_id
)

SELECT
    o.plow_event_id,
    o.ban_id,
    o.first_shift_start_utc,
    a.matched_snowfall_event_id,
    a.matched_snowfall_event_id IS NOT NULL AS is_aligned,
    '{etl_run_id}' AS etl_run_id,
    TIMESTAMP '{built_at}' AS built_at,
    CAST(MAX(o.loaded_at) OVER () AS DATE) AS source_max_ingest_date
FROM with_ban AS o
LEFT JOIN aligned AS a ON o.plow_event_id = a.plow_event_id
