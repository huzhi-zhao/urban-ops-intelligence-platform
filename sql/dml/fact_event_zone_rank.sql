-- fact_event_zone_rank (F2) — 418 rows, one per (operation, zone).
--
-- rank_factor = shift_number / 5, a **fixed denominator**, not a min-max
-- normalisation over the observed shift numbers (launch doc 20260813 A1).
-- The domain of shift_number is 1..5, so the value range is [0.2, 1] and
-- rank_factor = 0 is structurally unreachable — the DDL's "= 0 never
-- happens" gate holds by construction, not by convention. Normalising over
-- observed values instead would make an operation that happened to use only
-- shifts 1-3 produce a different scale from its neighbours, and the scores
-- would stop being comparable across events.
--
-- 🔴 O2 (rank is not constant across the decade) does **not** apply here.
-- This column is each operation's own measured shift_number, not a ten-year
-- mean; the drift O2 found constrains dim_region_crosswalk's calibration
-- window and L3's weights, not this table (design §6.10).
--
-- matched_snowfall_event_id is a denormalised copy of
-- dim_plow_event.matched_snowfall_event_id — NULL for the two known
-- unaligned operations (2021-01-07, 2026-02-26), which must stay visible
-- rather than be dropped. The fan-out guard (no two operations collapsing
-- onto one snowfall event) is gated on the built table; a consumer that wants
-- to reach F6 through this column has to verify that itself or join via
-- dim_plow_event.
SELECT
    e.plow_event_id,
    s.plow_zone,
    s.shift_number,
    CAST(s.shift_number AS DOUBLE) / 5.0 AS rank_factor,
    e.matched_snowfall_event_id,
    '{etl_run_id}' AS etl_run_id,
    TIMESTAMP '{built_at}' AS built_at,
    CAST(MAX(s.loaded_at) OVER () AS DATE) AS source_max_ingest_date
FROM {{ silver }}.silver_plow_shift AS s
INNER JOIN dim_plow_event AS e ON s.snow_ban_id = e.plow_event_id
