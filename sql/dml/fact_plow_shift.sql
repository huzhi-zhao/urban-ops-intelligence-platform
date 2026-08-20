-- fact_plow_shift (F3) — 418 planned shifts = 19 operations x 22 zones.
--
-- A pass-through of silver_plow_shift with the FK to dim_plow_event resolved.
-- The join is INNER on purpose: plow_event_id is not_null, so a shift whose
-- operation is not in dim_plow_event has to disappear and break the 418 gate
-- rather than land as a NULL FK that nothing would notice.
--
-- 🔴 shift_end_utc is the *planned* end of the window, not a completion time
-- (ADR 0008, and the tix9-r5tc source is a plan not a record). Nothing here
-- derives a duration from it, and nothing downstream may either.
--
-- The 418 equalling fact_event_zone_rank's 418 is not a coincidence to be
-- exploited: it holds because each operation schedules exactly one shift per
-- zone. The two tables are built from the same rows but at different grains.
SELECT
    s.id AS shift_id,
    e.plow_event_id,
    s.plow_zone,
    s.shift_number,
    s.shift_start_utc,
    s.shift_end_utc,
    '{etl_run_id}' AS etl_run_id,
    TIMESTAMP '{built_at}' AS built_at,
    CAST(MAX(s.loaded_at) OVER () AS DATE) AS source_max_ingest_date
FROM {{ silver }}.silver_plow_shift AS s
INNER JOIN dim_plow_event AS e ON s.snow_ban_id = e.plow_event_id
