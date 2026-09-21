-- fig_id: FIG-BO2-03
-- bo: BO-2
-- carrier: superset
-- schema: gold
-- criterion: 顺位序列 418 = 19 事件 × 22 分区，无缺失格
-- caption: 顺位面板全貌，418 格无缺无重。未对上降雪事件的 2 次作业
--   （2021-01-07 · 2026-02-26）单独标色，**不是缺数据**。
-- must_not_say: 不得把「未对上降雪事件」读成数据缺失——见 FIG-BO2-05。
SELECT
    e.plow_event_id,
    CAST(e.first_shift_start_utc AS DATE) AS first_shift_date,
    e.is_aligned,
    r.plow_zone,
    r.shift_number,
    r.rank_factor
FROM fact_event_zone_rank AS r
INNER JOIN dim_plow_event AS e ON r.plow_event_id = e.plow_event_id
ORDER BY e.first_shift_start_utc, r.plow_zone
