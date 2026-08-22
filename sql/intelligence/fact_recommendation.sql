-- F7 · Backtest ranking and attribution, one row per (event, zone, model_version).
-- Design: docs/dev/design/20260820-scoring-chain-and-m1.md §6.3
--
-- chunked_by: model_version (one chunk per artefact in fact_request_forecast)
-- combine: disjoint (each chunk emits its own version's rows, never another's)
--
-- 🔴 Why this table can be rebuilt per version while F6 cannot: of the three
-- factors, only the demand factor depends on the M1 version. The rank and
-- weather factors are read straight off F6, which is scored against one
-- serving version but whose *other two* columns are version-independent. So a
-- past version's backtest stays reproducible without keeping a second F6.
--
-- Covers the 374 scored cells only (F7's contract): a partial_no_rank row has
-- no schedule ordering, and ranking zones by a score that silently omits the
-- 0.30 rank term would compare two different measurements.

WITH panel AS (
    SELECT
        l.snowfall_event_id,
        l.plow_zone,
        l.rank_factor,
        l.weather_severity_factor,
        l.source_max_ingest_date
    FROM fact_winter_event_zone_load AS l
    WHERE l.score_status = 'scored'
),

-- This chunk's version. The demand factor is renormalised over this version's
-- own predictions so that a retrain is scored on its own scale, exactly as the
-- serving version is in F6.
version_forecast AS (
    SELECT
        snowfall_event_id,
        plow_zone,
        model_version,
        predicted_count,
        baseline_count,
        source_max_ingest_date
    FROM fact_request_forecast
    WHERE model_version = '{model_version}'
),

demand AS (
    SELECT
        f.snowfall_event_id,
        f.plow_zone,
        f.model_version,
        f.baseline_count,
        f.predicted_count / CAST(NULLIF(z.address_count, 0) AS DOUBLE) * 1000 AS demand_density,
        GREATEST(f.source_max_ingest_date, z.source_max_ingest_date) AS source_max_ingest_date
    FROM version_forecast AS f
    INNER JOIN dim_plow_zone AS z ON f.plow_zone = z.plow_zone
),

-- Min-max over the whole panel, same rule and same reason as F6: load_level and
-- the ranking are compared across events, so the scale must be too.
scaled AS (
    SELECT
        snowfall_event_id,
        plow_zone,
        model_version,
        baseline_count,
        source_max_ingest_date,
        CASE
            WHEN MAX(demand_density) OVER () = MIN(demand_density) OVER () THEN 0.0
            ELSE
                (demand_density - MIN(demand_density) OVER ())
                / (MAX(demand_density) OVER () - MIN(demand_density) OVER ())
        END AS request_forecast_factor
    FROM demand
),

contributions AS (
    SELECT
        p.snowfall_event_id,
        p.plow_zone,
        s.model_version,
        s.baseline_count,
        GREATEST(p.source_max_ingest_date, s.source_max_ingest_date) AS source_max_ingest_date,
        -- Weighted contributions, in the 0..1 score space the weights live in —
        -- not the 0..100 display space. The BALANCED threshold below is stated
        -- in the same units BO-6 reported its effect sizes in (0.300 / 0.270 /
        -- 0.167), so the two can be read against each other.
        0.40 * s.request_forecast_factor AS contribution_requests,
        0.30 * p.rank_factor AS contribution_rank,
        0.30 * p.weather_severity_factor AS contribution_weather
    FROM panel AS p
    INNER JOIN scaled AS s
        ON
            p.snowfall_event_id = s.snowfall_event_id
            AND p.plow_zone = s.plow_zone
),

ranked AS (
    SELECT
        snowfall_event_id,
        plow_zone,
        model_version,
        source_max_ingest_date,
        contribution_requests,
        contribution_rank,
        contribution_weather,
        GREATEST(contribution_requests, contribution_rank, contribution_weather) AS top_share,
        -- The runner-up, so "no single driver dominates" can be decided by a
        -- margin rather than by a bare argmax.
        contribution_requests + contribution_rank + contribution_weather
        - GREATEST(contribution_requests, contribution_rank, contribution_weather)
        - LEAST(contribution_requests, contribution_rank, contribution_weather) AS second_share,
        -- ROW_NUMBER, not RANK: the gate asks for a permutation of 1..22 within
        -- each event, and RANK leaves holes on a tie. plow_zone breaks ties so
        -- two builds over the same data order them the same way.
        ROW_NUMBER() OVER (
            PARTITION BY snowfall_event_id
            ORDER BY
                contribution_requests + contribution_rank + contribution_weather DESC,
                plow_zone ASC
        ) AS rank_model,
        -- The baseline is M1's own stored baseline_count — the same causal
        -- expanding mean scored in metrics.json — never recomputed live
        -- (ADR 0010 D5).
        ROW_NUMBER() OVER (
            PARTITION BY snowfall_event_id
            ORDER BY baseline_count DESC, plow_zone ASC
        ) AS rank_baseline
    FROM contributions
),

observed_requests AS (
    SELECT
        snowfall_event_id,
        plow_zone,
        SUM(request_count) AS request_count
    FROM fact_service_request_zone_event
    GROUP BY snowfall_event_id, plow_zone
),

attributed AS (
    SELECT
        snowfall_event_id,
        plow_zone,
        model_version,
        source_max_ingest_date,
        rank_model,
        rank_baseline,
        CASE
            -- 0.05 in weighted-score units. Below it the top two drivers are
            -- indistinguishable and naming one of them would be a claim the
            -- numbers do not support.
            WHEN top_share - second_share < 0.05 THEN 'RULE-BALANCED'
            WHEN top_share = contribution_rank THEN 'RULE-RANK-DOMINANT'
            WHEN top_share = contribution_requests THEN 'RULE-REQUESTS-DOMINANT'
            ELSE 'RULE-WEATHER-DOMINANT'
        END AS attribution_rule_id
    FROM ranked
)

SELECT
    a.snowfall_event_id,
    a.plow_zone,
    a.model_version,
    a.rank_model,
    a.rank_baseline,
    a.rank_baseline - a.rank_model AS rank_delta,
    a.attribution_rule_id,
    -- Template substitution, not generation: every placeholder is replaced by a
    -- measured value from Gold. See design §9 for how this may be described
    -- externally — "rule-based attribution", never "AI-generated explanation".
    REPLACE(
        REPLACE(
            REPLACE(
                REPLACE(r.template_text, '{plow_zone}', a.plow_zone),
                '{shift_number}', COALESCE(CAST(z.shift_number AS VARCHAR), 'n/a')
            ),
            '{request_count}', COALESCE(CAST(o.request_count AS VARCHAR), '0')
        ),
        '{snowfall_cm}', CAST(ROUND(e.total_snowfall_cm, 1) AS VARCHAR)
    ) AS attribution_text,
    '{etl_run_id}' AS etl_run_id,
    TIMESTAMP '{built_at}' AS built_at,
    a.source_max_ingest_date
FROM attributed AS a
INNER JOIN dim_recommendation_rules AS r ON a.attribution_rule_id = r.rule_id
INNER JOIN dim_snowfall_event AS e ON a.snowfall_event_id = e.snowfall_event_id
LEFT JOIN fact_event_zone_rank AS z
    ON
        a.snowfall_event_id = z.matched_snowfall_event_id
        AND a.plow_zone = z.plow_zone
LEFT JOIN observed_requests AS o
    ON
        a.snowfall_event_id = o.snowfall_event_id
        AND a.plow_zone = o.plow_zone
