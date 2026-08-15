-- Gold contract: contracts/gold-contracts/fact_winter_event_zone_load.yaml
-- Grain: (event_id, plow_zone)
-- Row count expectation: {'full_panel': True, 'panel_cells': 1298}
-- Served by: BO-6
-- Primary key (informational only — Trino does not enforce PK/FK/UNIQUE): (event_id, plow_zone)
-- unique: [['event_id', 'plow_zone']]
-- accepted_values (score_status): ['scored', 'partial_no_rank', 'no_schedule_era']
-- accepted_values (load_level): ['LOW', 'MED', 'HIGH', 'CRITICAL']
-- accepted_values (score_weight_profile): ['full_3factor', 'demand_weather_only']
-- relationships:
--   COUNT(*) = 1298
--   COUNT(*) WHERE rank_factor = 0 = 0
--   COUNT(*) WHERE event_id IN (SELECT event_id FROM dim_snowfall_event WHERE
--   is_scheduling_era = false) = 0
--   COUNT(*) WHERE score_status = 'scored' AND score_weight_profile != 'full_3factor' = 0
--   COUNT(*) WHERE score_status = 'partial_no_rank' AND score_weight_profile !=
--   'demand_weather_only' = 0
--   COUNT(DISTINCT weather_severity_factor) GROUP BY event_id <= 1  -- H1 degradation
--   (A2): constant across zones within an event
-- forbidden_columns (ADR 0010 D2 — admin units never enter a fact key): ['region_type',
-- 'ward', 'neighbourhood']

CREATE TABLE IF NOT EXISTS fact_winter_event_zone_load (
    -- not_null
    -- relationships -> dim_snowfall_event.event_id
    event_id VARCHAR,
    -- not_null
    -- relationships -> dim_plow_zone.plow_zone
    plow_zone VARCHAR,
    -- range: [0, 100]
    -- note: Null when score_status != scored — never a fabricated 0.
    load_score DOUBLE,
    -- accepted_values: ['LOW', 'MED', 'HIGH', 'CRITICAL']
    -- note: 🔴 Comparable only within the same score_weight_profile — see that column. 71.2% of
    -- the panel (partial_no_rank) is scored on a 0.70 weight sum, not 1.0, so its
    -- load_score/load_level are systematically lower than a scored row's would be at equal
    -- underlying severity.
    load_level VARCHAR,
    -- not_null
    -- accepted_values: ['full_3factor', 'demand_weather_only']
    -- note: full_3factor = request forecast (0.40) + rank (0.30) + weather (0.30), 1:1 with
    -- score_status = 'scored'. demand_weather_only = request forecast + weather only, weights
    -- NOT renormalized to sum to 1 (BO-6 'effective analysis window' forbids silent
    -- renormalization), 1:1 with score_status = 'partial_no_rank'. load_score and load_level
    -- must never be compared or thresholded across the two profiles.
    score_weight_profile VARCHAR,
    -- not_null
    -- accepted_values: ['scored', 'partial_no_rank', 'no_schedule_era']
    -- note: Three concrete states: 'scored' = all three factors present (event is one of the 19
    -- known plow operations); 'partial_no_rank' = snowfall event within the scheduling era
    -- (2015-12+) but not one of the 19 known plow operations — the far more common case, since
    -- only 59 of the panel's events even have a matching plow operation to draw rank from — rank
    -- factor NULL, score computed from BO-1 + BO-3 only, weighting disclosed not silently
    -- renormalized; 'no_schedule_era' reserved for a future zone that loses schedule coverage
    -- entirely. Pre-2015-12 events never appear in this table at all (BO-6 'effective analysis
    -- window') — panel completeness within the 1,298-cell scheduling-era panel is expressed by
    -- this column, not by which rows exist.
    score_status VARCHAR,
    -- range: [0, 1]
    request_forecast_factor DOUBLE,
    -- range: [0.2, 1]
    -- note: Formula: shift_number / 5 (fixed denominator — see fact_event_zone_rank.rank_factor,
    -- launch doc 20260813 A1). NULL when no schedule data exists for this (event, zone); never 0
    -- by construction, since shift_number's domain floor is 1.
    rank_factor DOUBLE,
    -- range: [0, 1]
    -- note: 🟡 H1: degraded to an event-level constant, equal to
    -- dim_snowfall_event.severity_score for every plow_zone under the same event_id —
    -- silver_weather_archive is a single citywide point, no zone-grained archive exists yet
    -- (launch doc 20260813 A2, chose explicit degradation over building TBL-S8 for H1). Column
    -- is grained at (event_id, plow_zone) for forward compatibility with a future zone-level
    -- archive, not because H1's values actually vary by zone.
    weather_severity_factor DOUBLE,
    -- relationships -> fact_request_forecast.model_version
    -- note: Foreign key, not an inlined predicted_count — ADR 0010 D5. Lets a model version
    -- change be traced without breaking past scores.
    forecast_model_version VARCHAR,
    -- not_null
    etl_run_id VARCHAR,
    -- not_null
    built_at TIMESTAMP(6),
    -- not_null
    source_max_ingest_date DATE
)
WITH (
    format = 'PARQUET',
    external_location = 's3a://{bucket}/gold/fact_winter_event_zone_load/'
);
