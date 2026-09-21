-- fig_id: FIG-BO2-04
-- bo: BO-2
-- carrier: echarts
-- schema: gold
-- criterion: 顺位 × 地址数的交叉验证——十年 r = +0.491、2021 年起 r = +0.403，方向为正
-- caption: 反证：后排分区是不是户数更多？**方向相反**——地址数与平均顺位正相关
--   （r = +0.49 全期 / +0.40 自 2021 年起），户数多的分区排得更靠后，
--   不是被优先照顾。
-- must_not_say: 不得把正相关读成因果（住址多所以排后面）——本图只反驳一条替代解释。
SELECT
    z.plow_zone,
    z.address_count,
    ROUND(AVG(CAST(r.shift_number AS DOUBLE)), 3) AS mean_shift_all,
    ROUND(
        AVG(
            CASE
                WHEN e.first_shift_start_utc >= TIMESTAMP '2021-01-01 00:00:00'
                    THEN CAST(r.shift_number AS DOUBLE)
            END
        ),
        3
    ) AS mean_shift_since_2021
FROM fact_event_zone_rank AS r
INNER JOIN dim_plow_zone AS z ON r.plow_zone = z.plow_zone
INNER JOIN dim_plow_event AS e ON r.plow_event_id = e.plow_event_id
GROUP BY z.plow_zone, z.address_count
ORDER BY z.address_count DESC
