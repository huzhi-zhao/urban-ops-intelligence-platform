-- fig_id: FIG-BO8-03
-- bo: BO-8
-- carrier: lookup
-- schema: gold
-- criterion: 需求估计与公开计划并排（ADR 0015 · H2-R6）——每个 model_version 1,298 格，
--   374 格同时有计划与估计（17 个事件），154 格留出（7 个事件），其中有计划的恰好 22 格
-- caption: 粒度 (snowfall_event_id, plow_zone, model_version)，即 F5 的粒度：59 个排班期事件
--   × 22 个分区。需求一栏是 M1 的预测请求数，计划一栏是那次作业的计划班次（1–5），两者
--   **不合成任何分数**。每千地址率与复核提示由页面构建步骤算，SQL 只出计数与分母。
--   `fit_role` 标出每格的估计是留出（模型没见过这一季）还是样本内拟合（见过实际计数）。
-- must_not_say: 不得把复核提示读成「计划排错了」，也不得把靠后的班次读成不公平（BO-8 表述纪律 3）；
--   不得把预测请求数叫作预报——它由天气存档回测得出；不得说「计划最近更新于」——排班源
--   没有更新时间字段，能给的只有作业开始时间与本项目的采集日期；`shift_number` 为空的格是
--   「这场雪没有公布的全市作业」，不是零差距，也不是没有清雪（ADR 0014 §1.1）；
--   `fit_role = in_sample` 的格不得拿来出提示或证明模型准确。
SELECT
    f.model_version,
    f.snowfall_event_id,
    e.start_date AS event_start_date,
    e.end_date AS event_end_date,
    e.total_snowfall_cm,
    e.snow_season,
    e.event_rule_version,
    CASE WHEN e.snow_season = '2025-2026' THEN 'holdout' ELSE 'in_sample' END AS fit_role,
    f.plow_zone,
    z.address_count,
    z.address_count_snapshot_date,
    f.predicted_count,
    f.actual_count,
    p.plow_event_id,
    p.first_shift_start_utc,
    r.shift_number,
    f.etl_run_id AS forecast_etl_run_id,
    f.source_max_ingest_date AS forecast_source_max_ingest_date,
    r.etl_run_id AS rank_etl_run_id,
    r.source_max_ingest_date AS rank_source_max_ingest_date
FROM fact_request_forecast AS f
INNER JOIN dim_snowfall_event AS e ON f.snowfall_event_id = e.snowfall_event_id
INNER JOIN dim_plow_zone AS z ON f.plow_zone = z.plow_zone
-- Through dim_plow_event, never fact_event_zone_rank.matched_snowfall_event_id
-- directly: the dimension carries the fan-out guard (one operation per event).
LEFT JOIN dim_plow_event AS p ON f.snowfall_event_id = p.matched_snowfall_event_id
LEFT JOIN fact_event_zone_rank AS r
    ON
        p.plow_event_id = r.plow_event_id
        AND f.plow_zone = r.plow_zone
ORDER BY f.model_version ASC, e.start_date ASC, f.plow_zone ASC
