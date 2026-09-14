-- fig_id: FIG-BO2-07
-- bo: BO-2
-- carrier: lookup
-- schema: gold
-- criterion: 「下次是不是轮到我优先」——「上次的班次」逐次回测下次的班次，18 次转移 × 22 分区 = 396 对
-- caption: 逐分区的班次转移计数，粒度 (plow_zone, prev_shift, next_shift)。按
--   first_shift_start_utc 排序后，每个分区的 19 次作业产生 18 对相邻转移，全市 396 对。
--   命中率（prev = next 精确、|prev − next| <= 1 为 ±1 以内）与「市里是不是在轮换」
--   都从这张表**加总**得出，不在 SQL 里先算比率——按分区平均比率会让每个分区等权，
--   与 R3 同一个理由。
-- must_not_say: 不得把命中率读成预测模型的精度——它是「照抄上次」这条规则的回测，不是模型输出；
--   不得把 18 次转移当作足以下结论的样本量；不得读成对下一次作业的承诺，
--   排班是计划不是记录（ADR 0008）。
WITH seq AS (
    SELECT
        plow_event_id,
        ROW_NUMBER() OVER (ORDER BY first_shift_start_utc) AS event_seq
    FROM dim_plow_event
),

panel AS (
    SELECT
        r.plow_zone,
        r.shift_number,
        e.event_seq
    FROM fact_event_zone_rank AS r
    INNER JOIN seq AS e ON r.plow_event_id = e.plow_event_id
),

pairs AS (
    SELECT
        plow_zone,
        shift_number AS next_shift,
        LAG(shift_number) OVER (PARTITION BY plow_zone ORDER BY event_seq) AS prev_shift
    FROM panel
)

SELECT
    plow_zone,
    prev_shift,
    next_shift,
    COUNT(*) AS transitions
FROM pairs
WHERE prev_shift IS NOT NULL
GROUP BY plow_zone, prev_shift, next_shift
ORDER BY plow_zone ASC, prev_shift ASC, next_shift ASC
