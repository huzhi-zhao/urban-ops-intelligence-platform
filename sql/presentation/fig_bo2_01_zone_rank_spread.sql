-- fig_id: FIG-BO2-01
-- bo: BO-2
-- carrier: echarts
-- schema: gold
-- criterion: 分区平均顺位与轮候时长——首尾 mean_shift 1.26 / 3.47，mean_wait_hours 极差约 26 h
-- caption: 22 个作业分区的平均排班顺位，2015-12 以来 19 次全市犁雪作业。两端相差 2.21 个班次
--   ≈ **26 小时**（班次时长 12 h）。**须**是各分区实际到过的最快 / 最慢班次：
--   **除 K 外每个分区都当过首班**，包括平均最慢的 C。
-- must_not_say: 不得说「十年没变」——本图是十年均值，位移见 FIG-BO2-02；
--   不得把顺位差异表述为不公平，作业分区顺位是作业批次序，不是服务水平承诺。
SELECT
    plow_zone,
    COUNT(*) AS events,
    ROUND(AVG(CAST(shift_number AS DOUBLE)), 2) AS mean_shift,
    MIN(shift_number) AS min_shift,
    MAX(shift_number) AS max_shift,
    ROUND(STDDEV_SAMP(CAST(shift_number AS DOUBLE)), 2) AS sd_shift,
    ROUND((AVG(CAST(shift_number AS DOUBLE)) - 1) * 12, 1) AS mean_wait_hours
FROM fact_event_zone_rank
GROUP BY plow_zone
ORDER BY mean_shift
