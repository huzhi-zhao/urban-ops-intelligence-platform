-- fig_id: FIG-BO6-02
-- bo: BO-6
-- carrier: echarts
-- schema: gold
-- criterion: 画加权后的实测展幅，不画名义权重——名义 0.40/0.30/0.30 与实际影响序不是一回事
-- caption: 三个因子加权后的**实测**区间：需求 0–0.40 · 顺位 0.06–0.30 而 **89.6%
--   落在 0.06–0.18** · 天气 0.020–0.269（= 0.30 × [0.0682, 0.8978]）。
--   **名义权重 0.40/0.30/0.30 与实际影响序不是一回事。**
-- must_not_say: 🔴 不得把名义权重画成贡献。也不得把顺位因子画成均匀五档——
--   第四、五班合计只有 39/374 格，前三班占 89.6%。天气因子是事件级常量，
--   它决定评分高低而几乎不影响事件内排序。
WITH scored AS (
    SELECT
        request_forecast_factor,
        rank_factor,
        weather_severity_factor
    FROM fact_winter_event_zone_load
    WHERE score_weight_profile = 'full_3factor'
),

weighted AS (
    SELECT
        'demand' AS factor,
        0.40 * request_forecast_factor AS contribution
    FROM scored
    UNION ALL
    SELECT
        'rank' AS factor,
        0.30 * rank_factor AS contribution
    FROM scored
    UNION ALL
    SELECT
        'weather' AS factor,
        0.30 * weather_severity_factor AS contribution
    FROM scored
)

SELECT
    factor,
    COUNT(*) AS cells,
    ROUND(MIN(contribution), 4) AS min_contribution,
    ROUND(APPROX_PERCENTILE(contribution, 0.25), 4) AS p25,
    ROUND(APPROX_PERCENTILE(contribution, 0.50), 4) AS median,
    ROUND(APPROX_PERCENTILE(contribution, 0.75), 4) AS p75,
    ROUND(MAX(contribution), 4) AS max_contribution,
    ROUND(MAX(contribution) - MIN(contribution), 4) AS observed_spread
FROM weighted
GROUP BY factor
ORDER BY observed_spread DESC
