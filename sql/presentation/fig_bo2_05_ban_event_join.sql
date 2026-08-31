-- fig_id: FIG-BO2-05
-- bo: BO-2
-- carrier: superset
-- schema: gold
-- criterion: 49 条停车禁令 → 19 次犁雪作业，30 条未匹配是另一种禁令而不是缺数据
-- caption: 49 条停车禁令 → 19 次犁雪事件。落差**不是丢数据**：匹配的 19 条同属一个
--   禁令类型，另外 30 条属于另外两类。
-- must_not_say: 不得把「61% 未匹配」读成数据质量问题——禁令与全市犁雪不是一一对应关系。
SELECT
    b.ban_id,
    b.ban_type_id,
    CAST(b.ban_start_utc AS DATE) AS ban_start_date,
    CAST(b.ban_end_utc AS DATE) AS ban_end_date,
    b.matched_plow_event_id,
    CASE WHEN b.matched_plow_event_id IS NULL THEN 'unmatched' ELSE 'matched' END AS match_state
FROM fact_parking_ban AS b
ORDER BY b.ban_start_utc
