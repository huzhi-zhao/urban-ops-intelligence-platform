-- fig_id: FIG-BO1-01
-- bo: BO-1
-- carrier: superset
-- schema: gold
-- criterion: 18 个年份，ward 逐年恰好 15 个标签，2019 年起 ward 的次数开始高于 neighbourhood
-- caption: 按**标签出现次数**计，不是工单数——一条同时带 ward 和 neighbourhood 的工单
--   产生两行。两条线在 2019 年前逐年相同，之后 ward 略高，差额最大 16/年。
-- must_not_say: 🔴 不得把纵轴读成「工单数」。它是标签次数，两条线相加没有意义。
--   2019 年起的差额成因未查（launch L10），不得解释成「工单变多」或「标签规则改过」。
SELECT
    YEAR("date") AS request_year,
    label_type,
    SUM(request_count) AS label_request_count,
    COUNT(DISTINCT label_id) AS distinct_labels,
    COUNT(*) AS grain_rows
FROM fact_winter_request_daily_by_label
GROUP BY YEAR("date"), label_type
ORDER BY request_year, label_type
