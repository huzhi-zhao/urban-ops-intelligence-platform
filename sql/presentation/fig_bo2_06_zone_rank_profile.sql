-- fig_id: FIG-BO2-06
-- bo: BO-2
-- carrier: lookup
-- schema: gold
-- criterion: 「为什么我这里扫得慢」——每个分区的历次顺位分布 + 前后期漂移 + 近期频率，25 行覆盖全部分区
-- caption: 逐分区的排班顺位档案，19 次全市犁雪作业（2015-12 起）。每行给出平均 / 最快 /
--   最慢班次、1–5 班各自的次数、前 9 与后 10 次作业的均值，以及最近 11 次里排进前两班的次数。
--   驱动表是 dim_plow_zone 的**全部 25 个分区**而不是顺位面板的 22 个：B/D · X · Downtown
--   三个无排班分区在这里是 operations = 0 的行，**不是缺数据**，它们占地址数 6.02%。
-- must_not_say: 不得读成实际完成时间——排班是计划不是记录（ADR 0008）；不得读成街道级答案，
--   粒度是分区；不得说顺位「十年没变」（mean_early / mean_late 两列就是反例）；
--   不得把靠后的顺位读成不公平——21/22 个分区都当过首班。
WITH seq AS (
    SELECT
        plow_event_id,
        ROW_NUMBER() OVER (ORDER BY first_shift_start_utc) AS event_seq,
        COUNT(*) OVER () AS event_total
    FROM dim_plow_event
),

panel AS (
    SELECT
        r.plow_zone,
        r.shift_number,
        e.event_seq,
        e.event_total
    FROM fact_event_zone_rank AS r
    INNER JOIN seq AS e ON r.plow_event_id = e.plow_event_id
)

SELECT
    z.plow_zone,
    z.has_plow_schedule,
    z.address_count,
    COUNT(p.event_seq) AS operations,
    ROUND(AVG(CAST(p.shift_number AS DOUBLE)), 2) AS mean_shift,
    MIN(p.shift_number) AS min_shift,
    MAX(p.shift_number) AS max_shift,
    ROUND(AVG(CASE WHEN p.event_seq <= 9 THEN CAST(p.shift_number AS DOUBLE) END), 2) AS mean_early,
    ROUND(AVG(CASE WHEN p.event_seq > 9 THEN CAST(p.shift_number AS DOUBLE) END), 2) AS mean_late,
    COUNT(CASE WHEN p.shift_number = 1 THEN 1 END) AS shift_1_count,
    COUNT(CASE WHEN p.shift_number = 2 THEN 1 END) AS shift_2_count,
    COUNT(CASE WHEN p.shift_number = 3 THEN 1 END) AS shift_3_count,
    COUNT(CASE WHEN p.shift_number = 4 THEN 1 END) AS shift_4_count,
    COUNT(CASE WHEN p.shift_number = 5 THEN 1 END) AS shift_5_count,
    COUNT(CASE WHEN p.event_seq > p.event_total - 11 THEN 1 END) AS recent_operations,
    COUNT(
        CASE WHEN p.event_seq > p.event_total - 11 AND p.shift_number <= 2 THEN 1 END
    ) AS recent_top2_count,
    MAX(CASE WHEN p.event_seq = p.event_total THEN p.shift_number END) AS last_shift
FROM dim_plow_zone AS z
LEFT JOIN panel AS p ON z.plow_zone = p.plow_zone
GROUP BY z.plow_zone, z.has_plow_schedule, z.address_count
ORDER BY mean_shift ASC NULLS LAST, z.plow_zone ASC
