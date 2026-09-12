-- fig_id: FIG-BO4-02
-- bo: BO-4
-- carrier: echarts
-- schema: gold
-- criterion: 主导份额分布——「标签可以贴，数不能搬」
-- caption: 每个作业分区的冬季工单，最集中的那个选区占了多大一份。中位 **54.0%**，
--   **10/25 不到一半**（含 3 个无排班分区）。这就是评分统一到作业分区（ADR 0009）的
--   理由：按选区打分会把同一条作业路线的工作量拆到几个选区里。
-- must_not_say: 🔴 **不得把主导份额读成面积份额或几何重叠**——它是
--   `该选区的冬季工单数 / 该分区冬季工单总数`，没有用过 ward 几何。也不得反过来读成
--   「该选区承担了这么多除雪工作」：除雪按作业分区排班，工单是居民**报修**的落点，
--   不是**作业**的落点。
SELECT
    c1.plow_zone,
    c1.label_id AS dominant_ward,
    ROUND(c1.weight, 4) AS dominant_share,
    (
        SELECT COUNT(*)
        FROM dim_region_crosswalk AS c2
        WHERE c2.plow_zone = c1.plow_zone AND c2.label_type = 'ward'
    ) AS wards_touched
FROM dim_region_crosswalk AS c1
WHERE c1.label_type = 'ward' AND c1.is_dominant
ORDER BY dominant_share ASC, plow_zone ASC
