-- fact_parking_ban (F4) — 49 bans, 19 of them matched to a plow operation.
--
-- 🔴 The join to dim_plow_event is LEFT and must stay LEFT. 30 of the 49 rows
-- have no matched_plow_event_id and that is **semantics, not a gap**: a
-- parking ban can be declared without a city-wide clearing operation
-- following it. An inner join here would silently delete 61% of the table and
-- every count downstream would still look plausible. The DDL gates both
-- halves (19 matched / 30 unmatched) precisely so this cannot regress
-- quietly.
--
-- dim_plow_event.ban_id is the resolved FK — matching on it rather than
-- re-deriving the ban/operation mapping from silver_plow_shift.snow_ban_id
-- keeps that mapping stated in exactly one place (dim_plow_event's DML).
SELECT
    b.id AS ban_id,
    b.ban_start_utc,
    b.ban_end_utc,
    b.ban_type_id,
    e.plow_event_id AS matched_plow_event_id,
    '{etl_run_id}' AS etl_run_id,
    TIMESTAMP '{built_at}' AS built_at,
    CAST(MAX(b.loaded_at) OVER () AS DATE) AS source_max_ingest_date
FROM {{ silver }}.silver_parking_ban AS b
LEFT JOIN dim_plow_event AS e ON b.id = e.ban_id
