-- fig_id: FIG-BO3-00
-- bo: BO-3
-- carrier: echarts
-- schema: silver
-- criterion: 候选窗口逐日降雪，至少 3 个小雪日 + 至少 1 个 0 cm 间隔日 + 间隔日之后仍有降雪
-- caption: 十四个连续自然日的逐日降雪。窗口是从真实存档里按判据选出来的，不是画出来的形状：
--   它必须同时含有几个小雪日、一个降雪为 0 的间隔日、以及间隔日之后重新开始的降雪。
--   这张图上不画任何事件边界——「这是一场雪、两场还是三场」正是它要留给观众的问题。
-- must_not_say: 不得在这张图上画阈值线或事件边界，那是下一张图（FIG-BO3-00b）的事；
--   也不得说这十四天「是一次降雪事件」——事件是后加的规则，不是数据自带的。
--   返回的是多个候选窗口，window_rank 只是候选排序，不是严重程度排序。
--
-- 🔴 扫描范围被刻意限制在两个雪季内。silver_weather_archive 是日分区表，
--   全历史约 6,600 个分区，而 .claude/rules/gold-sql.md R1 实测 4,878 个分区的
--   整表扫描会在 Trino 的 S3 客户端上超时。这里选 2021-11 → 2023-04：
--   2021-2022 是十八冬最重的一季（FIG-BO3-02 实测 11 个事件 / 106.2 cm），
--   雪日多则候选窗口多，不必扩大扫描面。
WITH days AS (
    SELECT
        weather_date,
        COALESCE(snowfall_sum_cm, 0.0) AS snowfall_cm
    FROM silver_weather_archive
    WHERE "date" >= '2021-11-01' AND "date" < '2023-04-01'
),

windows AS (
    SELECT
        weather_date AS window_start_date,
        COUNT(*) OVER w AS days_in_window,
        SUM(snowfall_cm) OVER w AS window_snowfall_cm,
        COUNT_IF(snowfall_cm > 0.0 AND snowfall_cm < 3.0) OVER w AS small_snow_days,
        COUNT_IF(snowfall_cm = 0.0) OVER w AS zero_days,
        COUNT_IF(snowfall_cm >= 3.0) OVER w AS threshold_days
    FROM days
    WINDOW w AS (ORDER BY weather_date ROWS BETWEEN CURRENT ROW AND 13 FOLLOWING)
),

candidates AS (
    SELECT
        window_start_date,
        window_snowfall_cm,
        small_snow_days,
        zero_days,
        threshold_days,
        ROW_NUMBER() OVER (
            ORDER BY small_snow_days DESC, window_snowfall_cm DESC, window_start_date ASC
        ) AS window_rank
    FROM windows
    WHERE
        days_in_window = 14
        AND small_snow_days >= 3
        AND zero_days >= 1
)

SELECT
    c.window_rank,
    c.window_start_date,
    ROUND(c.window_snowfall_cm, 1) AS window_snowfall_cm,
    c.small_snow_days,
    c.zero_days,
    c.threshold_days,
    d.weather_date,
    DATE_DIFF('day', c.window_start_date, d.weather_date) + 1 AS day_index,
    ROUND(d.snowfall_cm, 2) AS snowfall_cm,
    ROUND(
        SUM(d.snowfall_cm) OVER (
            PARTITION BY c.window_rank ORDER BY d.weather_date
        ),
        2
    ) AS running_total_cm
FROM candidates AS c
INNER JOIN days AS d
    ON d.weather_date BETWEEN c.window_start_date AND c.window_start_date + INTERVAL '13' DAY
WHERE c.window_rank <= 5
ORDER BY c.window_rank, d.weather_date
