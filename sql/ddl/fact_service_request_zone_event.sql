-- Gold contract: contracts/gold-contracts/fact_service_request_zone_event.yaml
-- Grain: (event_id, plow_zone, winter_category)
-- Row count expectation: {'full_panel': True, 'exact': 13068, 'panel_cells': 2178,
-- 'scheduling_era_panel_cells': 1298, 'category_count': 6}
--   ✅ Full panel, all 99 snowfall events — not just the 59 scheduling-era ones (launch doc
-- 20260813 B2: F1 is M1's *only* training panel, and dim_snowfall_event.yaml commits pre-era
-- events to feeding M1's long-horizon training; that commitment has nowhere to live if F1
-- stops at 59). 2178 = 22 zones x 99 events; 13068 = 2178 x 6 *effective* winter_category
-- values (dim_winter_category.yaml, launch doc 20260813 A3; PLOUGH excluded — a portability
-- row with 0 real matches). The scoring chain (fact_winter_event_zone_load, F6) still reads
-- only the 1298-cell scheduling-era subset — F1 being larger than F6 is intended, not a
-- grain mismatch. Do NOT filter to zero-request cells — M1 needs the zero as a training
-- signal, not a gap to be backfilled downstream. The 70.57% / 916-cell non-zero figure
-- (design doc §6.1) is defined and asserted at the (event_id, plow_zone) grain **within the
-- scheduling-era subset only**, summed across categories — see the relationships test below,
-- not at this table's own row grain or its full 2178-cell extent.
-- Served by: BO-1
-- Primary key (informational only — Trino does not enforce PK/FK/UNIQUE): (event_id,
-- plow_zone, winter_category)
-- unique: [['event_id', 'plow_zone', 'winter_category']]
-- relationships:
--   event_id -> dim_snowfall_event.event_id
--   plow_zone -> dim_plow_zone.plow_zone WHERE has_plow_schedule = true
--   winter_category -> dim_winter_category.winter_category WHERE is_effective = true
--   COUNT(*) = 13068
--   COUNT(DISTINCT (event_id, plow_zone)) = 2178
--   COUNT(DISTINCT (event_id, plow_zone)) WHERE event_id IN (SELECT event_id FROM
--   dim_snowfall_event WHERE is_scheduling_era = true) = 1298
--   COUNT(*) FROM (SELECT event_id, plow_zone FROM fact_service_request_zone_event WHERE
--   event_id IN (SELECT event_id FROM dim_snowfall_event WHERE is_scheduling_era = true)
--   GROUP BY event_id, plow_zone HAVING SUM(request_count) > 0) = 916
-- forbidden_columns (ADR 0010 D2 — admin units never enter a fact key): ['region_type',
-- 'ward', 'neighbourhood']

CREATE TABLE IF NOT EXISTS fact_service_request_zone_event (
    -- not_null
    -- relationships -> dim_snowfall_event.event_id
    event_id VARCHAR,
    -- not_null
    -- relationships -> dim_plow_zone.plow_zone
    -- note: 22 values only — has_plow_schedule = true. Not 25; see dim_plow_zone.
    plow_zone VARCHAR,
    -- not_null
    -- relationships -> dim_winter_category.winter_category
    winter_category VARCHAR,
    -- not_null
    -- note: Zero is a valid, meaningful value — not a placeholder for missing.
    request_count INTEGER,
    -- not_null
    -- note: request_count weighted by dim_service_type.priority_weight.
    weighted_request_count DOUBLE,
    -- not_null
    etl_run_id VARCHAR,
    -- not_null
    built_at TIMESTAMP(6),
    -- not_null
    source_max_ingest_date DATE
)
WITH (
    format = 'PARQUET',
    external_location = 's3a://{bucket}/gold/fact_service_request_zone_event/'
);
