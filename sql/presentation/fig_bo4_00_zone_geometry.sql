-- fig_id: FIG-BO4-00
-- bo: BO-4
-- carrier: echarts
-- schema: gold
-- criterion: 25 个分区各一行 WKT，其中 22 个有排班、3 个无排班，8 个几何被修复过
-- caption: 犁雪分区边界的 WKT，供地图类图位使用（分区 V 放大图、模型估计值分区着色图）。
--   25 行，每个分区一个 MultiPolygon，是 silver_plow_zone_boundary 里同名多边形的并集。
-- must_not_say: 不得把 has_plow_schedule = false 的 3 个分区画成「负荷为零」——
--   它们没有排班数据，不是没有工作量；按 deck 的规定这 3 个只画轮廓、不填色。
--   也不得把 geometry_repaired = true 的 8 个分区静默处理：面积被 make_valid 改动过，
--   area_delta_pct 记的就是改了多少。
SELECT
    plow_zone,
    has_plow_schedule,
    address_count,
    geometry_repaired,
    ROUND(area_delta_pct, 4) AS area_delta_pct,
    geometry_wkt
FROM dim_plow_zone
ORDER BY plow_zone ASC
