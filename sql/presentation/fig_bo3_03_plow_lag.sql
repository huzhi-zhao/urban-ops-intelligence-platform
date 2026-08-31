-- fig_id: FIG-BO3-03
-- bo: BO-3
-- carrier: echarts
-- schema: gold
-- criterion: 17 次可对齐的犁雪里 11 次开工早于降雪事件结束，且两次未对齐必须单独在图上
-- caption: 17 次可对齐的全市犁雪里，**11 次在降雪事件结束之前就已开工**（负值）。
--   多日降雪边下边犁是常态，不能读成「响应了多久」。起算点是事件**结束日**；
--   换成事件开始日同一件事会变成另一个数。19 次里 **2 次对不上任何降雪事件**
--   （2021-01-07 / 2026-02-26），单独标出——滚动累积判据把原先的四次减到两次，
--   有效但不充分。
-- must_not_say: 🔴 不得把这根轴叫「响应延迟」或「响应时长」。它是**相对事件结束日的
--   时间位置**，负值多数意味着雪还在下就已经在犁；锚点换成 start_date 会得到一组
--   完全不同的数，所以图上必须写出锚点是哪一天。两个未对齐的点也不得读成「漏犁」。
SELECT
    p.plow_event_id,
    CAST(p.first_shift_start_utc AS DATE) AS first_shift_date,
    p.is_aligned,
    p.matched_snowfall_event_id,
    e.start_date AS event_start_date,
    e.end_date AS event_end_date,
    e.duration_days,
    e.total_snowfall_cm,
    -- 🔴 锚点是事件结束日。负值 = 犁雪开工时这场雪还没下完。
    DATE_DIFF('day', e.end_date, CAST(p.first_shift_start_utc AS DATE)) AS days_from_event_end,
    DATE_DIFF('day', e.start_date, CAST(p.first_shift_start_utc AS DATE)) AS days_from_event_start
FROM dim_plow_event AS p
LEFT JOIN dim_snowfall_event AS e
    ON p.matched_snowfall_event_id = e.snowfall_event_id
ORDER BY p.first_shift_start_utc
