-- fig_id: FIG-BO6-00
-- bo: BO-6
-- carrier: echarts
-- schema: gold
-- criterion: 全部 374 个 scored 格，带三个原始观测值，按「三值视觉上分得开」排序
-- caption: 单事件 × 单分区那张图要放的三个数是原始观测值，不是评分因子：
--   降雪 cm（事件级）、冬季工单数（该分区该事件）、计划班次序号。374 个 scored 格全部返回，
--   排序只是候选顺序——挑哪一格是人的决定，SQL 不代为决定，也不做任何过滤式「优选」。
-- must_not_say: 不得把 load_score 或三个 factor 当成这三个数——factor 是加权归一化后的
--   贡献值，放到台上会被读成「降雪 0.14 cm」。也不得说排在前面的格「最有代表性」：
--   distinctness_rank 只衡量三个数在视觉上是否分得开，与这一格是否典型无关。
--
-- 🔴 这三个数各来自一张表，粒度也不同：降雪是事件级（每事件一个值），
--   工单数是事件 × 分区 × 类别（这里对类别求和），班次是犁雪作业 × 分区。
--   放在一张图上是有意的收敛，但它们不是同一个观测——deck 自己的话是
--   「三个观测，不是一个原因」。
WITH request_totals AS (
    SELECT
        snowfall_event_id,
        plow_zone,
        SUM(request_count) AS winter_request_count
    FROM fact_service_request_zone_event
    GROUP BY snowfall_event_id, plow_zone
),

scored_cells AS (
    SELECT
        l.snowfall_event_id,
        l.plow_zone,
        e.start_date AS event_start_date,
        e.snow_season,
        e.total_snowfall_cm,
        e.peak_daily_snowfall_cm,
        e.duration_days,
        e.accum_flag,
        COALESCE(r.winter_request_count, 0) AS winter_request_count,
        k.shift_number,
        l.load_score,
        l.load_level,
        l.score_weight_profile,
        l.etl_run_id
    FROM fact_winter_event_zone_load AS l
    INNER JOIN dim_snowfall_event AS e
        ON l.snowfall_event_id = e.snowfall_event_id
    INNER JOIN fact_event_zone_rank AS k
        ON
            l.snowfall_event_id = k.matched_snowfall_event_id
            AND l.plow_zone = k.plow_zone
    LEFT JOIN request_totals AS r
        ON
            l.snowfall_event_id = r.snowfall_event_id
            AND l.plow_zone = r.plow_zone
    WHERE l.score_status = 'scored'
)

SELECT
    snowfall_event_id,
    plow_zone,
    event_start_date,
    snow_season,
    ROUND(total_snowfall_cm, 1) AS total_snowfall_cm,
    ROUND(peak_daily_snowfall_cm, 1) AS peak_daily_snowfall_cm,
    duration_days,
    accum_flag,
    winter_request_count,
    shift_number,
    ROUND(load_score, 2) AS load_score,
    load_level,
    score_weight_profile,
    etl_run_id,
    ROW_NUMBER() OVER (
        -- 「视觉上分得开」= 三个数都不贴地板：足够的雪、非零且两位数的工单、
        -- 明确落在某个班次上。这只排序，不过滤——所有 374 格都在结果里。
        ORDER BY
            LEAST(total_snowfall_cm / 30.0, winter_request_count / 60.0) DESC,
            winter_request_count DESC,
            snowfall_event_id ASC,
            plow_zone ASC
    ) AS distinctness_rank
FROM scored_cells
ORDER BY distinctness_rank ASC
