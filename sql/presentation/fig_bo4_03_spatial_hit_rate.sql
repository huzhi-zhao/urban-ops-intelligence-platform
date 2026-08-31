-- fig_id: FIG-BO4-03
-- bo: BO-4
-- carrier: superset
-- schema: meta
-- criterion: 空间命中率约 99.9%，且**分母必须与它同框**
-- caption: 工单落进作业分区的比率：**99.9%**，分母 **7,566** 条带坐标工单（当日窗口）。
--   🔴 分母与数字同框——上游 79% 的工单本就没有坐标，不带分母的命中率读不出来。
-- must_not_say: 不得只报百分比。「命中率完美」和「窗口里只有三行带坐标」在没有分母时
--   长得一模一样——这正是 DQ 第二批修掉的那个缺陷。
SELECT
    run_id,
    ROUND(observed, 4) AS hit_rate_pct,
    rows_checked AS has_geo_denominator,
    expected,
    comparator,
    passed,
    checked_at
FROM dq_audit_log
WHERE rule_id = 'SILVER-BIZ-SPATIAL-HIT-RATE'
ORDER BY checked_at DESC
LIMIT 7
