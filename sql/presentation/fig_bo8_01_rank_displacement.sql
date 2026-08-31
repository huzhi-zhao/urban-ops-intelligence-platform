-- fig_id: FIG-BO8-01
-- bo: BO-8
-- carrier: echarts
-- schema: gold
-- criterion: 每个事件内位移和恒为 0（34/34 组），且两个版本同为 188 格上移
-- caption: `rank_delta` 的**位移**分布，按 `model_version` 分面。① 每个事件内两列排名都是
--   1..22 的排列，位移和**恒为 0**，所以「188 格上移」必然对应「167 格下移」；
--   ② **故意训坏的 `nomonth` 同样是 188 格上移**。
-- must_not_say: 🔴 标题用「位移」不用「改进」。`rank_delta > 0` **不是**「模型优于基线」——
--   它是同一事件内两个 1..22 排列之间的位置差，和恒为 0；一个故意去掉月份特征的模型
--   产生的上移格数一模一样。
SELECT
    model_version,
    rank_delta,
    COUNT(*) AS cells,
    COUNT(DISTINCT snowfall_event_id) AS events,
    SUM(rank_delta) AS delta_sum_must_be_zero_over_all_deltas
FROM fact_recommendation
GROUP BY model_version, rank_delta
ORDER BY model_version, rank_delta
