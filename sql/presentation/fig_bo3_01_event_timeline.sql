-- fig_id: FIG-BO3-01
-- bo: BO-3
-- carrier: echarts
-- schema: gold
-- criterion: 99 个事件、18 季无空档，且「只因累积成立」与「没有工单」两类点必须看得出来
-- caption: 99 个降雪事件，2008-11 至 2026-04，判据是**单日 ≥ 3 cm 或 10 日累计 ≥ 10 cm**。
--   空心点的 11 个事件在任何分区都没有冬季工单——早年 311 数据稀薄，不是没下雪。
--   浅色的 8 个只因累积判据成立，没有哪一天单独过线；其中三个全期降雪不足 1.5 cm，
--   是前一场雪的尾巴被切开，**不是新的一场雪**。
-- must_not_say: 不得把空心点读成「那几场雪没引发问题」——它是 311 覆盖的稀薄，
--   不是需求的缺席。也不得只写「≥ 3 cm」：省掉累积判据就解释不了那 8 个浅色点。
WITH requested AS (
    SELECT
        snowfall_event_id,
        SUM(request_count) AS event_request_count
    FROM fact_service_request_zone_event
    GROUP BY snowfall_event_id
)

SELECT
    e.snowfall_event_id,
    e.snow_season,
    e.start_date,
    e.end_date,
    e.duration_days,
    e.total_snowfall_cm,
    e.peak_daily_snowfall_cm,
    e.min_temperature_c,
    e.severity_score,
    e.accum_flag,
    e.is_scheduling_era,
    COALESCE(r.event_request_count, 0) AS event_request_count,
    COALESCE(r.event_request_count, 0) = 0 AS has_no_winter_request
FROM dim_snowfall_event AS e
LEFT JOIN requested AS r ON e.snowfall_event_id = r.snowfall_event_id
ORDER BY e.start_date
