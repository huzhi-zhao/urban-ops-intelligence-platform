-- fig_id: FIG-BO1-04
-- bo: BO-1
-- carrier: echarts
-- schema: gold
-- criterion: 冬季工单在六个有效 category 上的分布——WINDROW 的 0 是实测零，不是缺口
-- caption: 99 个降雪事件 × 22 个有排班的分区上的冬季工单，按 category 分。六类各扫过
--   同样的 2,178 格,所以 WINDROW 的 0 是「一条工单都没落进来」,不是「没统计到」。
--   它对应的那个请求类型在整个观测窗口里无人使用。
-- must_not_say: 🔴 不得把 WINDROW 的 0 读成管道故障或分类失效——同一列里
--   ICE_CONTROL 拿到 847,说明多命中仲裁在工作。不得把这里的合计说成「冬季工单总数」:
--   它只数落在降雪事件窗口内、且归得到有排班分区的工单,Silver 的冬季子集是另一个数。
--   不得按 category 之间的高低给服务排优先级——这是市民报了什么,不是该先清什么。
WITH effective AS (
    -- 只取 is_effective 的六类。第七类 PLOUGH 是给英式拼写城市留的可移植性行,
    -- 在本市 type 取值上匹配 0 行,画进来会是一根解释不了的空柱。
    SELECT winter_category
    FROM dim_winter_category
    WHERE is_effective = TRUE
),

by_category AS (
    SELECT
        f.winter_category,
        COUNT(*) AS cells,
        SUM(f.request_count) AS requests,
        SUM(f.weighted_request_count) AS weighted_requests,
        COUNT_IF(f.request_count > 0) AS cells_with_any_request
    FROM fact_service_request_zone_event AS f
    INNER JOIN effective AS e ON f.winter_category = e.winter_category
    GROUP BY f.winter_category
)

SELECT
    winter_category,
    -- 🔴 cells 和 requests 必须同框。F1 是满笛卡尔积,每一类都必然有 2,178 格,
    -- 所以「某类 cells = 0」这条判据永远不会触发——20260827 篇 §4.9 记的就是
    -- 这个:判据问错了列。要量的是 requests,而 cells 是它的分母,证明这一类
    -- 确实被扫过。
    cells,
    requests,
    cells_with_any_request,
    ROUND(weighted_requests, 1) AS weighted_requests,
    ROUND(100.0 * requests / SUM(requests) OVER (), 1) AS pct_of_all_requests
FROM by_category
ORDER BY requests DESC, winter_category ASC
