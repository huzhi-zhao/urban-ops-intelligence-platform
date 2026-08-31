-- fig_id: FIG-BO2-02
-- bo: BO-2
-- carrier: echarts
-- schema: gold
-- criterion: 大方向稳定但个别分区位移超过一整个班次——这张图不画就等于在说「十年没变」
-- caption: 同样 19 次作业按时间切成前 9 / 后 10。**V 后移 1.31 班、M 后移 1.02 班**，
--   均超过一整个班次。顺位不是固定的，**不能说「十年没变」**。切分口径是事件序号，
--   不是日期；探针 scripts.analysis.zone_schedule_rank 用的是 --since 2021-01-01，
--   两个数不可混讲。
-- must_not_say: 不得说顺位「十年没变」；也不得把位移读成排班规则被改过——
--   本图只测量结果，不指认原因。
WITH ev AS (
    SELECT
        plow_event_id,
        ROW_NUMBER() OVER (ORDER BY first_shift_start_utc) AS seq
    FROM dim_plow_event
),

tagged AS (
    SELECT
        r.plow_zone,
        CAST(r.shift_number AS DOUBLE) AS shift_number,
        CASE WHEN e.seq <= 9 THEN 'early' ELSE 'late' END AS half
    FROM fact_event_zone_rank AS r
    INNER JOIN ev AS e ON r.plow_event_id = e.plow_event_id
)

SELECT
    plow_zone,
    ROUND(AVG(CASE WHEN half = 'early' THEN shift_number END), 2) AS mean_early,
    ROUND(AVG(CASE WHEN half = 'late' THEN shift_number END), 2) AS mean_late,
    ROUND(
        AVG(CASE WHEN half = 'late' THEN shift_number END)
        - AVG(CASE WHEN half = 'early' THEN shift_number END),
        2
    ) AS drift
FROM tagged
GROUP BY plow_zone
ORDER BY drift
