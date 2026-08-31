-- fig_id: FIG-BO8-02
-- bo: BO-8
-- carrier: superset
-- schema: gold
-- criterion: RULE-BALANCED 占 431/748 = 57.6%，六条规则里两条 0 命中
-- caption: **57.6% 的格子没有单一主导因素**（`RULE-BALANCED` 431/748）。换 M1 版本只搬动了
--   一类归因：WEATHER-DOMINANT ↔ BALANCED 之间 31 格，而 RANK-DOMINANT(54) 与
--   REQUESTS-DOMINANT(43) 两版逐个相同。
-- must_not_say: 🔴 六条规则里 `RULE-NO-SCHEDULE` 与 `RULE-FALLBACK` 各 0 次，那是
--   **未实现功能的接口，不是坏件**——图里要么不画，要么标注。也不得把 BALANCED 读成
--   「三个因素同等重要」：它的定义是没有哪一个够得上主导阈值。
SELECT
    attribution_rule_id,
    COUNT(*) AS cells,
    COUNT_IF(model_version = 'v1') AS cells_v1,
    COUNT_IF(model_version <> 'v1') AS cells_other_version,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct_of_all_cells,
    COUNT(DISTINCT attribution_text) AS distinct_sentences
FROM fact_recommendation
GROUP BY attribution_rule_id
ORDER BY cells DESC
