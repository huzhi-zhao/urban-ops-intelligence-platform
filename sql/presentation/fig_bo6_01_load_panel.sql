-- fig_id: FIG-BO6-01
-- bo: BO-6
-- carrier: echarts
-- schema: gold
-- criterion: 1,298 格 = 374 scored + 924 partial_no_rank，两个 profile 必须分开画
-- caption: 评分面板 59 事件 × 22 分区 = 1,298 格。**71.2% 是 `partial_no_rank`**
--   ——那 924 格没有排班数据，用的是两因子 0.70 制；另外 374 格才是三因子满分制。
--   两块分开画或分两根色标。
-- must_not_say: 🔴 不得把两个 profile 画在同一根色标上。374 格与 924 格的尺子不同，
--   同色标会把「没有排班数据」画成「不忙」。也不得说面板满分 100——天气因子在排班期内
--   最高 0.8978，`full_3factor` 的实际可达上限约 96.9。
SELECT
    l.snowfall_event_id,
    e.start_date AS event_start_date,
    e.snow_season,
    l.plow_zone,
    l.score_weight_profile,
    l.score_status,
    l.load_score,
    l.load_level,
    l.request_forecast_factor,
    l.rank_factor,
    l.weather_severity_factor,
    l.forecast_model_version
FROM fact_winter_event_zone_load AS l
INNER JOIN dim_snowfall_event AS e ON l.snowfall_event_id = e.snowfall_event_id
ORDER BY e.start_date, l.plow_zone
