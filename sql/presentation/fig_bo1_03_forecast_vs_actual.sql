-- fig_id: FIG-BO1-03
-- bo: BO-1
-- carrier: echarts
-- schema: gold
-- criterion: 三条线同框——v1 与故意训坏的 nomonth 只差 0.57 个 MAE，两者与基线都差约 16
-- caption: 留出季 7 个事件、154 格。三条线都画：`nomonth` 是**故意去掉月份特征的对照**，
--   它与 v1 只差 0.57 个 MAE，而两者与基线都差约 16。**这张图不支持「模型优于基线」
--   的结论**——更可能是基线太弱。
-- must_not_say: 🔴 不得把这张图说成「模型优于基线」。留出季只有 7 个事件、目标高度
--   零膨胀，且对照模型只差 7.8%——差距更像出在基线太弱。也不得只画 v1 与基线：
--   拿掉 nomonth 那条线就是选择性呈现。
SELECT
    f.model_version,
    f.snowfall_event_id,
    e.snow_season,
    e.start_date AS event_start_date,
    f.plow_zone,
    f.actual_count,
    f.predicted_count,
    f.baseline_count,
    ABS(f.predicted_count - f.actual_count) AS model_abs_error,
    ABS(f.baseline_count - f.actual_count) AS baseline_abs_error
FROM fact_request_forecast AS f
INNER JOIN dim_snowfall_event AS e ON f.snowfall_event_id = e.snowfall_event_id
WHERE e.snow_season = '2025-2026'
ORDER BY f.model_version, e.start_date, f.plow_zone
