-- fig_id: FIG-BO6-03
-- bo: BO-6
-- carrier: superset
-- schema: gold
-- criterion: 两个 profile 的分布形状根本不同——partial 88.1% 是 LOW，scored 只有 12.3%
-- caption: `load_level` 分布，**两个 profile 各一个坐标系**。partial 的 CRITICAL 门槛是
--   **52.5**（阈值按各自 ceiling 缩放），当前 **0 格**到达，而最高分只差 **2.23**。
--   partial 88.1% 是 LOW，scored 只有 12.3%。
-- must_not_say: 🔴 不得并轴，也不得跨 profile 比较 `load_level`——同名不同尺。
--   🔴 「924 格没有 CRITICAL」是**经验事实不是结构保证**，最高分离门槛只有 2.23 分，
--   重建后可能出现；不得表述成「永远不可能到 CRITICAL」。
SELECT
    score_weight_profile,
    load_level,
    COUNT(*) AS cells,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY score_weight_profile), 1)
        AS pct_within_profile,
    ROUND(MIN(load_score), 2) AS min_score,
    ROUND(MAX(load_score), 2) AS max_score
FROM fact_winter_event_zone_load
GROUP BY score_weight_profile, load_level
ORDER BY score_weight_profile, max_score
