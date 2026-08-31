-- fig_id: FIG-BO4-01
-- bo: BO-4
-- carrier: echarts
-- schema: gold
-- criterion: 作业分区与 ward 不嵌套——每个分区的主导 ward 面积份额中位数明显低于 1.0
-- caption: 25 个作业分区 × 15 个选区，格值是面积占比。**只有 T 和 N 两个分区完整落在
--   一个选区内**；V 横跨 10 个。两套划分依据不同——一套按选举人口，一套按作业路线
--   ——**两边都没划错**，但它们不能互相代替。
-- must_not_say: 不得说「行政区划分得不好」——两套划分依据不同（一套按选举、一套按作业），
--   两边都没错。图注讲的是后果：按 ward 打分会把同一个作业分区的工作量拆到几个 ward 里。
SELECT
    c.plow_zone,
    c.label_id AS ward,
    ROUND(c.weight, 4) AS area_share,
    c.is_dominant
FROM dim_region_crosswalk AS c
WHERE c.label_type = 'ward'
ORDER BY c.plow_zone ASC, c.weight DESC
