-- fig_id: FIG-BO3-02
-- bo: BO-3
-- carrier: superset
-- schema: gold
-- criterion: 18 个雪季逐季 ≥ 2 个事件，最重与最轻相差一个数量级
-- caption: 2021-2022 是十八冬最重的一季：11 个事件、106.2 cm，是第二名的 1.6 倍。
--   2024-2025 只有 2 个事件。
-- must_not_say: 不得把「事件数少」读成「雪少」——两者在这张表里是两根轴，
--   2024-2025 两个事件仍有 19.3 cm。也不得跨季比较 severity_score 的平均值：
--   它的归一化跑在全部 99 个事件上，不是逐季重算的。
SELECT
    snow_season,
    COUNT(*) AS event_count,
    COUNT_IF(is_scheduling_era) AS scheduling_era_event_count,
    COUNT_IF(accum_flag) AS accum_only_event_count,
    ROUND(SUM(total_snowfall_cm), 1) AS season_snowfall_cm,
    ROUND(MAX(peak_daily_snowfall_cm), 1) AS max_daily_snowfall_cm,
    ROUND(MIN(min_temperature_c), 1) AS season_min_temperature_c
FROM dim_snowfall_event
GROUP BY snow_season
ORDER BY snow_season
