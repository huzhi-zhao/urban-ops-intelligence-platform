-- F6 · Winter operational load score, one row per (snowfall event, plow zone).
-- Design: docs/dev/design/20260820-scoring-chain-and-m1.md §6.1/§6.2
--
-- Reads Gold only — not one Silver row — so gold-sql.md R1's date predicate is
-- satisfied by construction rather than by a WHERE clause.
--
-- forecast_version: the single M1 version this build scores against. F6's grain
-- has no model_version, so exactly one version can drive it; build_gold refuses
-- to pick when fact_request_forecast holds more than one and none was named.

WITH forecast AS (
    SELECT
        snowfall_event_id,
        plow_zone,
        model_version,
        predicted_count,
        source_max_ingest_date
    FROM fact_request_forecast
    WHERE model_version = '{forecast_version}'
),

-- Requests per 1,000 addresses, never raw counts. BO-6 measured the denominator
-- as load-bearing: without it r(rank, requests) inflates from +0.017 to +0.139,
-- and the rank term stops being the independent second factor the weighting
-- assumes.
demand AS (
    SELECT
        f.snowfall_event_id,
        f.plow_zone,
        f.model_version,
        f.predicted_count / CAST(NULLIF(z.address_count, 0) AS DOUBLE) * 1000 AS demand_density,
        GREATEST(f.source_max_ingest_date, z.source_max_ingest_date) AS source_max_ingest_date
    FROM forecast AS f
    INNER JOIN dim_plow_zone AS z ON f.plow_zone = z.plow_zone
),

-- 🔴 Min-max over the WHOLE 1,298-cell panel, not within each event.
-- Per-event normalisation would put exactly one 1.0 and one 0.0 in every event,
-- which destroys cross-event comparability — and load_level is compared across
-- events. The BO-6 probe (score_collinearity.normalise) normalised the same way.
--
-- A constant input maps to 0, not 0.5: a factor with no variation contributes
-- nothing to the ordering, and saying so with 0 is more honest than inflating
-- every weighted sum by half the weight.
scaled_demand AS (
    SELECT
        snowfall_event_id,
        plow_zone,
        model_version,
        source_max_ingest_date,
        CASE
            WHEN MAX(demand_density) OVER () = MIN(demand_density) OVER () THEN 0.0
            ELSE
                (demand_density - MIN(demand_density) OVER ())
                / (MAX(demand_density) OVER () - MIN(demand_density) OVER ())
        END AS request_forecast_factor
    FROM demand
),

factors AS (
    SELECT
        d.snowfall_event_id,
        d.plow_zone,
        d.model_version,
        GREATEST(d.source_max_ingest_date, e.source_max_ingest_date) AS source_max_ingest_date,
        d.request_forecast_factor,
        -- NULL when this (event, zone) has no matched plow operation. Never 0:
        -- shift_number's domain floor is 1, so 0 could only ever be a fabricated
        -- stand-in for "missing", which ADR 0008 §2.3 rejects.
        r.rank_factor,
        -- 🟡 H1 degradation (launch 20260813 A2): a single citywide weather
        -- archive, so this is an event-level constant repeated across zones.
        -- The column is zone-grained for forward compatibility only.
        e.severity_score AS weather_severity_factor
    FROM scaled_demand AS d
    INNER JOIN dim_snowfall_event AS e ON d.snowfall_event_id = e.snowfall_event_id
    LEFT JOIN fact_event_zone_rank AS r
        ON
            d.snowfall_event_id = r.matched_snowfall_event_id
            AND d.plow_zone = r.plow_zone
),

-- 🔴 Weights are NOT renormalised when the rank factor is missing. BO-6's
-- "effective analysis window" forbids it: a demand_weather_only row sums to
-- 0.70 and its score is systematically lower than a full_3factor row at equal
-- severity. score_weight_profile is that fact written into the data, and
-- load_level's thresholds scale with each profile's own ceiling C.
scored AS (
    SELECT
        snowfall_event_id,
        plow_zone,
        model_version,
        source_max_ingest_date,
        request_forecast_factor,
        rank_factor,
        weather_severity_factor,
        CASE WHEN rank_factor IS NULL THEN 'partial_no_rank' ELSE 'scored' END AS score_status,
        CASE
            WHEN rank_factor IS NULL THEN 'demand_weather_only'
            ELSE 'full_3factor'
        END AS score_weight_profile,
        CASE WHEN rank_factor IS NULL THEN 70.0 ELSE 100.0 END AS score_ceiling,
        100.0 * (
            0.40 * request_forecast_factor
            + 0.30 * COALESCE(rank_factor, 0.0)
            + 0.30 * weather_severity_factor
        ) AS load_score
    FROM factors
)

SELECT
    snowfall_event_id,
    plow_zone,
    load_score,
    -- Fixed fractions of the profile's own ceiling, never data quantiles:
    -- quantiles would make a rebuild's output depend on the data being
    -- unchanged, and one new event would shift historical events' levels.
    CASE
        WHEN load_score < 0.25 * score_ceiling THEN 'LOW'
        WHEN load_score < 0.50 * score_ceiling THEN 'MED'
        WHEN load_score < 0.75 * score_ceiling THEN 'HIGH'
        ELSE 'CRITICAL'
    END AS load_level,
    score_weight_profile,
    score_status,
    request_forecast_factor,
    rank_factor,
    weather_severity_factor,
    model_version AS forecast_model_version,
    '{etl_run_id}' AS etl_run_id,
    TIMESTAMP '{built_at}' AS built_at,
    -- The newest input's date, not the run date: this column answers "how old
    -- is the input", built_at answers "when did we run" (ADR 0010 D7).
    MAX(source_max_ingest_date) OVER () AS source_max_ingest_date
FROM scored
