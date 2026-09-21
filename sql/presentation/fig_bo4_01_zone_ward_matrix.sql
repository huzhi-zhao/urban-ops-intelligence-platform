-- fig_id: FIG-BO4-01
-- bo: BO-4
-- carrier: echarts
-- schema: gold
-- criterion: 作业分区与 ward 不嵌套——每个分区的主导 ward 请求份额中位数明显低于 1.0
-- caption: 25 个作业分区 × 15 个选区，格值是**该分区的冬季工单落在各选区的占比**
--   （口径：2023-11 → 2026-05 三个雪季）。**只有 T 和 N 两个分区的工单完整落在
--   一个选区内**；V 的工单分散在 10 个选区。两套划分依据不同——一套按选举人口，
--   一套按作业路线——**两边都没划错**，但它们不能互相代替。
-- must_not_say: 🔴 **不得把格值说成面积占比或几何重叠**。dim_region_crosswalk.weight 是
--   `该 (分区, 选区) 的冬季工单数 / 该分区冬季工单总数`，从头到尾没有碰过 ward 几何
--   （仓库里也没有 ward 几何）。也不得说「行政区划分得不好」——两套划分依据不同，
--   两边都没错。图注讲的是后果：按 ward 打分会把同一个作业分区的工作量拆到几个 ward 里。
SELECT
    c.plow_zone,
    c.label_id AS ward,
    ROUND(c.weight, 4) AS request_share,
    c.is_dominant
FROM dim_region_crosswalk AS c
WHERE c.label_type = 'ward'
ORDER BY c.plow_zone ASC, c.weight DESC
