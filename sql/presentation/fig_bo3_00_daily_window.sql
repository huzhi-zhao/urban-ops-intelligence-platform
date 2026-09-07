-- fig_id: FIG-BO3-00
-- bo: BO-3
-- carrier: echarts
-- schema: silver
-- criterion: 两类窗口各自选出——ambiguous_bursts（几个小雪日 + 至少一个 0 cm 间隔日 +
--   间隔后重新下雪，slide 20）与 accumulation_only（窗口内没有任何一天到 3 cm，
--   而这十四天的累计 >= 10 cm，slide 21）
-- caption: 十四个连续自然日的逐日降雪。窗口是从真实存档里按判据选出来的，不是画出来的形状。
--   window_kind = ambiguous_bursts 供 slide 20：它含有间隔日与间隔后的重新开始，
--   「这是一场雪、两场还是三场」正是留给观众的问题。
--   window_kind = accumulation_only 供 slide 21：其中没有任何一天越过单日阈值，
--   而累计仍然越线——它演示的是「小雪日会累加」这件事本身。
-- must_not_say: 不得在 slide 20 那张图上画阈值线或事件边界——那是 slide 21 的事，
--   也是这两张图分开的全部理由。不得说这十四天「是一次降雪事件」：事件是后加的规则，
--   不是数据自带的。window_rank 只是候选排序，不是严重程度排序；两类窗口之间不可比较，
--   accumulation_only 的总量天然低于 ambiguous_bursts，那不代表它「更轻」。
--   🔴 尤其不得说 accumulation_only 那个窗口「是那 8 个 accum_flag 事件之一」，
--   也不得说「按我们的规则它会被判为一次事件」——它的判据是十四天累计，
--   而生产规则量的是十天。同一行里的 max_trailing_10d_cm 就是给人复核这一点的：
--   实测首选窗口 2022-12-16 的十天累计是 9.45，差 0.55 不到线。见
--   docs/dev/design/20260906-final-deck-figure-slots.md §3.4。
--
-- 🔴 扫描范围被刻意限制在两个雪季内。silver_weather_archive 是日分区表，
--   全历史 9,747 个分区（2000-01-01 → 2026-09-07 实测，一天不缺），而
--   .claude/rules/gold-sql.md R1 实测 4,878 个分区的整表扫描会在 Trino 的
--   S3 客户端上超时。这里选 2021-11 → 2023-04 共约 517 个分区。
--
-- 🔴 每个自然月只留最好的一个候选。不加这一条，相邻起始日会各自成为一个「候选」
--   （实测 2022-11-06 与 11-07 同时入选，逐日数据几乎一样），5 个候选里只有 3 个
--   互不重叠的窗口，而挑图的人无从看出这一点。
WITH days AS (
    SELECT
        weather_date,
        COALESCE(snowfall_sum_cm, 0.0) AS snowfall_cm,
        SUM(COALESCE(snowfall_sum_cm, 0.0)) OVER (
            ORDER BY weather_date ROWS BETWEEN 9 PRECEDING AND CURRENT ROW
        ) AS trailing_10d_cm
    FROM silver_weather_archive
    WHERE "date" >= '2021-11-01' AND "date" < '2023-04-01'
),

windows AS (
    SELECT
        weather_date AS window_start_date,
        COUNT(*) OVER w14 AS days_in_window,
        SUM(snowfall_cm) OVER w14 AS window_snowfall_cm,
        MAX(snowfall_cm) OVER w14 AS max_daily_cm,
        COUNT_IF(snowfall_cm > 0.0 AND snowfall_cm < 3.0) OVER w14 AS small_snow_days,
        COUNT_IF(snowfall_cm = 0.0) OVER w14 AS zero_days,
        COUNT_IF(snowfall_cm >= 3.0) OVER w14 AS threshold_days,
        -- trailing_10d_cm at offset +9..+13 is the only span whose whole ten
        -- days sit inside this window. Taking the global column unfiltered
        -- would credit the window with snow that fell before it started.
        MAX(trailing_10d_cm) OVER (
            ORDER BY weather_date ROWS BETWEEN 9 FOLLOWING AND 13 FOLLOWING
        ) AS max_trailing_10d_cm
    FROM days
    WINDOW w14 AS (ORDER BY weather_date ROWS BETWEEN CURRENT ROW AND 13 FOLLOWING)
),

classified AS (
    SELECT
        window_start_date,
        window_snowfall_cm,
        max_daily_cm,
        max_trailing_10d_cm,
        small_snow_days,
        zero_days,
        threshold_days,
        CASE
            -- 🔴 十四天累计，不是十天。生产的滚动判据量的是十天，但满足
            -- 「十天内累计到 10 cm 且这十天没有任何一天到 3 cm」的窗口在
            -- 2021-11 → 2023-04 实测为零：真实的 accum 事件都是踩着一个越过
            -- 单日阈值的日子累起来的。这一列放宽到窗口全长，是为了让 slide 21
            -- 有一段真实存档可画；严格判据下会不会触发，由 max_trailing_10d_cm
            -- 如实报出，不藏。
            WHEN max_daily_cm < 3.0 AND window_snowfall_cm >= 10.0 THEN 'accumulation_only'
            WHEN small_snow_days >= 3 AND zero_days >= 1 THEN 'ambiguous_bursts'
        END AS window_kind
    FROM windows
    WHERE days_in_window = 14
),

best_per_month AS (
    SELECT
        window_start_date,
        window_snowfall_cm,
        max_daily_cm,
        max_trailing_10d_cm,
        small_snow_days,
        zero_days,
        threshold_days,
        window_kind,
        ROW_NUMBER() OVER (
            PARTITION BY window_kind, DATE_TRUNC('month', window_start_date)
            ORDER BY small_snow_days DESC, window_snowfall_cm DESC, window_start_date ASC
        ) AS within_month
    FROM classified
    WHERE window_kind IS NOT NULL
),

candidates AS (
    SELECT
        window_start_date,
        window_snowfall_cm,
        max_daily_cm,
        max_trailing_10d_cm,
        small_snow_days,
        zero_days,
        threshold_days,
        window_kind,
        ROW_NUMBER() OVER (
            PARTITION BY window_kind
            ORDER BY small_snow_days DESC, window_snowfall_cm DESC, window_start_date ASC
        ) AS window_rank
    FROM best_per_month
    WHERE within_month = 1
)

SELECT
    c.window_kind,
    c.window_rank,
    c.window_start_date,
    ROUND(c.window_snowfall_cm, 1) AS window_snowfall_cm,
    ROUND(c.max_daily_cm, 2) AS max_daily_cm,
    ROUND(c.max_trailing_10d_cm, 1) AS max_trailing_10d_cm,
    c.small_snow_days,
    c.zero_days,
    c.threshold_days,
    d.weather_date,
    DATE_DIFF('day', c.window_start_date, d.weather_date) + 1 AS day_index,
    ROUND(d.snowfall_cm, 2) AS snowfall_cm,
    ROUND(
        SUM(d.snowfall_cm) OVER (
            PARTITION BY c.window_kind, c.window_rank ORDER BY d.weather_date
        ),
        2
    ) AS running_total_cm
FROM candidates AS c
INNER JOIN days AS d
    ON d.weather_date BETWEEN c.window_start_date AND c.window_start_date + INTERVAL '13' DAY
WHERE c.window_rank <= 3
ORDER BY c.window_kind ASC, c.window_rank ASC, d.weather_date ASC
