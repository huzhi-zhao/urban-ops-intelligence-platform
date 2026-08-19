-- dim_snowfall_event — the 99-event probe cohort, not Silver's 159.
--
-- 🔴 The WHERE clause below is load-bearing and its absence raises nothing.
-- Silver cuts events from 2000-01-01 across every month; the probe cohort
-- (scripts/analysis/snowfall_events.py, FIRST_WINTER = 2008) takes only
-- 2008-11 onward and only Nov..Apr. 159 - 52 (2000-2007) - 1 - 7 = 99.
-- Drop the filter and the table becomes 159 rows, F1's panel becomes
-- 22 x 159 x 6 = 20,988 instead of 13,068, and that number walks into M1's
-- training set. Design §6.6 / O9 — revisit before M1 trains, not here.
--
-- severity_score follows BO-3's definition as implemented in
-- scripts/analysis/score_collinearity.severity(): normalise each half over the
-- observed events, then mix. snow_weight = 0.5 is that probe's default, and
-- every measurement signed off so far (BO-6 independence, the 0.167 score-unit
-- influence) was taken at 0.5 — choosing another value silently invalidates
-- them. Normalisation runs over the 99 kept events, never the 159.
WITH cohort AS (
    SELECT
        snowfall_event_id,
        start_date,
        end_date,
        total_snowfall_cm,
        duration_days,
        peak_daily_snowfall_cm,
        min_temperature_c,
        accum_flag,
        event_rule_version,
        loaded_at,
        -- C16: min_temperature_c is nullable in Silver. The probe's rule is
        -- `float(cold) if cold is not None else 0.0` — a missing reading
        -- contributes no cold severity rather than an invented one.
        GREATEST(0.0, -COALESCE(min_temperature_c, 0.0)) AS cold_c
    FROM {{ silver }}.silver_snowfall_event
    WHERE
        start_date >= DATE '2008-11-01'
        AND MONTH(start_date) IN (11, 12, 1, 2, 3, 4)
),

bounds AS (
    SELECT
        MIN(total_snowfall_cm) AS snow_lo,
        MAX(total_snowfall_cm) AS snow_hi,
        MIN(cold_c) AS cold_lo,
        MAX(cold_c) AS cold_hi
    FROM cohort
)

SELECT
    c.snowfall_event_id,
    c.start_date,
    c.end_date,
    c.total_snowfall_cm,
    c.duration_days,
    c.peak_daily_snowfall_cm,
    c.min_temperature_c,
    c.accum_flag,
    0.5 * (CASE
        WHEN b.snow_hi = b.snow_lo THEN 0.0
        ELSE (c.total_snowfall_cm - b.snow_lo) / (b.snow_hi - b.snow_lo)
    END)
    + 0.5 * (CASE
        WHEN b.cold_hi = b.cold_lo THEN 0.0
        ELSE (c.cold_c - b.cold_lo) / (b.cold_hi - b.cold_lo)
    END)
        AS severity_score,
    -- A snow season spans a year boundary, so November onward names the season
    -- starting that year and everything else the one that started the year
    -- before. Calendar years are not the analytical unit here (gold-sql.md R2).
    CASE
        WHEN MONTH(c.start_date) >= 11
            THEN FORMAT('%d-%d', YEAR(c.start_date), YEAR(c.start_date) + 1)
        ELSE FORMAT('%d-%d', YEAR(c.start_date) - 1, YEAR(c.start_date))
    END AS snow_season,
    c.start_date >= DATE '2015-12-01' AS is_scheduling_era,
    c.event_rule_version,
    '{etl_run_id}' AS etl_run_id,
    TIMESTAMP '{built_at}' AS built_at,
    CAST(MAX(c.loaded_at) OVER () AS DATE) AS source_max_ingest_date
FROM cohort AS c
CROSS JOIN bounds AS b
