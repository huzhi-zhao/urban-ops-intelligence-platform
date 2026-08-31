-- fig_id: FIG-BO1-02
-- bo: BO-1
-- carrier: echarts
-- schema: gold
-- criterion: 目标高度零膨胀——四分之一是 0、中位 2.9、最大 381
-- caption: 1,298 格里四分之一是 0，中位 2.9，最大 381。**均值在这张分布上没有意义**。
-- must_not_say: 🔴 不得用均值描述这张面板，也不得把 0 读成缺失——它是「那个事件里
--   那个分区确实没有冬季工单」。分位数与最大值必须同框，只画均值会把一条长尾分布
--   说成一个典型值。
WITH primary_version AS (
    -- 面板每个版本各一份 1,298 格，而 actual_count 是实测值、逐版本相同。
    -- 取一个版本是为了不把同一格数两次，不是在挑模型。
    SELECT MIN(model_version) AS model_version
    FROM fact_request_forecast
),

panel AS (
    SELECT f.actual_count
    FROM fact_request_forecast AS f
    INNER JOIN primary_version AS v ON f.model_version = v.model_version
)

SELECT
    COUNT(*) AS cells,
    COUNT_IF(actual_count = 0) AS zero_cells,
    ROUND(100.0 * COUNT_IF(actual_count = 0) / COUNT(*), 1) AS zero_pct,
    ROUND(APPROX_PERCENTILE(CAST(actual_count AS DOUBLE), 0.25), 2) AS p25,
    ROUND(APPROX_PERCENTILE(CAST(actual_count AS DOUBLE), 0.50), 2) AS median,
    ROUND(APPROX_PERCENTILE(CAST(actual_count AS DOUBLE), 0.75), 2) AS p75,
    ROUND(APPROX_PERCENTILE(CAST(actual_count AS DOUBLE), 0.95), 2) AS p95,
    MAX(actual_count) AS max_count,
    ROUND(AVG(CAST(actual_count AS DOUBLE)), 2) AS mean_do_not_plot_alone
FROM panel
