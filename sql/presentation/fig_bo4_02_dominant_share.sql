-- fig_id: FIG-BO4-02
-- bo: BO-4
-- carrier: echarts
-- schema: gold
-- criterion: 主导份额分布——「标签可以贴，数不能搬」
-- caption: 每个作业分区的主导选区占它多大面积。中位 **54.0%**，**10/25 不到一半**（含 3 个无排班分区）。
--   这就是评分统一到作业分区（ADR 0009）的理由：按选区打分会把同一条作业路线的
--   工作量拆到几个选区里。
-- must_not_say: 不得把主导份额读成「该 ward 承担了这么多除雪工作」——它是面积份额，
--   不是工作量份额。
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
ORDER BY dominant_share
