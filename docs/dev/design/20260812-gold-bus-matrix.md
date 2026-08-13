# Gold 层 Bus Matrix（BO × 表）

> **Status**: Draft · **Date**: 2026-08-12
> **决策依据**: [ADR 0010](../adr/0010-gold-fact-grain-and-dimension-layering.md)（粒度与分层，已 Accepted）·
> [design/20260809-gold-silver-schema-derivation.md](20260809-gold-silver-schema-derivation.md)（表清单 TBL-D1…F8，已 Accepted）·
> [requirements/business-objectives.md](../requirements/business-objectives.md)（BO-1…BO-8）

---

## 1. 问题

`docs/dev/design/20260809-gold-silver-schema-derivation.md` 的 §5 把 S2 定义为
「BO × 表」矩阵，门禁是**双向**的：

- 每个 BO 的验收标准都能指到具体表和列；
- **反向也成立**：没有 BO 指向的表，从表清单里删掉。

这两条现在还只是散落在 `business-objectives.md`（八个 BO 各自的验收标准）与
20260809 设计文档（§4.2/4.3 的 15 张 Gold 表）两篇文档里，**没有人把它们逐格对上过**。
S3 要冻结 contract，冻结前必须知道每一列是为哪条验收标准存在的——
没有 BO 指向的列，contract 里多半也不该有它的值域/非空约束。

---

## 2. 矩阵（正向：BO → 表 → 列 → 验收判据）

只列 H1 范围内的表（TBL-F9 `fact_service_request` 因 `closed_date` 语义未验、
BO-5 是 P1，本次不建，见 ADR 0010 §4.4）。

| BO | Gold 表 | 承担验收的列 | 验收判据（引自 business-objectives.md） |
|---|---|---|---|
| **BO-1**（需求侧 / M1） | `fact_service_request_zone_event`（F1） | `(event_id, plow_zone, winter_category)`, `request_count` | 22 个作业分区能产出事件级序列；面板满格 1,298（§8 O1，不只存非零） |
| BO-1 | `fact_request_forecast`（F5） | `predicted_count`, `actual_count`, `model_version` | M1 在留出冬季上 MAE 优于 seasonal-naive 基线（§4.4 评估协议） |
| BO-1 | `dim_snowfall_event`（D4） | `event_id`, `snow_season` | 建模单元的时间边界（有效窗口 2015-12 起、排班期 59 个事件） |
| BO-1 | `dim_service_type`（D5） | `winter_category`, `priority_weight` | 冬季工单识别规则覆盖六类且不误伤（Pol-ice / Serv-ice 等），加权工单量的权重来源 |
| BO-1 | `dim_channel`（D6） | `channel_normalized`, `is_comparable_pre_2022` | 渠道归一化映射（`Self Service + Mobile + SMS In → VOF`），2022 前后总量口径可比 |
| BO-1（描述性切片） | `fact_service_request_daily_by_label`（F8） | `(date, label_type, label_id)`, `request_count` | 各 ward 冬季工单量、逐年趋势——**不与评分链共用列**（ADR 0010 D2） |
| **BO-2**（供给侧排班顺位） | `fact_event_zone_rank`（F2） | `(plow_event_id, plow_zone)`, `shift_number`, `rank_factor` | 418/418 无缺失；与降雪事件对齐率 ≥ 17/19（89.5%），已知未对齐两次显式标注 |
| BO-2 | `fact_plow_shift`（F3） | `shift_id`, `shift_start`, `shift_end` | 顺位的溯源明细，`shift_end` 只作计划值不作完成时刻（ADR 0008） |
| BO-2 | `fact_parking_ban`（F4） | `ban_id`, `snow_ban_id` | 49 条禁令独立成表，与 F3 左连接，30 条无排班的禁令不判定为缺失 |
| BO-2 | `dim_plow_zone`（D1） | `address_count`, `address_count_snapshot_date` | 分区平均顺位与地址数交叉验证 `r = +0.491`（局限：分母是当期快照） |
| **BO-3**（降雪事件切分） | `dim_snowfall_event`（D4） | `event_rule_version`, `snowfall_sum_cm`, `accum_flag`, `severity_score` | 双判据事件定义（3 cm/日 或 10 日累计 ≥ 10 cm）；`event_rule_version` 语义化编号（§8 O3） |
| **BO-4**（空间对齐） | `dim_plow_zone`（D1） | `geometry_wkt`, `geometry_repaired`, `area_delta_pct` | 82 多边形 → 25 分区 MULTIPOLYGON 集合；8/25 修复且有记录（ADR 0010 D6） |
| BO-4 | `dim_admin_label`（D2） | `label_type`, `label_id` | 15 ward + 237 neighbourhood，无几何且不留空列 |
| BO-4 | `dim_region_crosswalk`（D3） | `weight`, `is_dominant`, `calibration_window` | zone→label 单方向；空间命中率 99.9%；下游单值查表按缺陷处理 |
| ~~BO-5~~（P1，H1 不建） | — | — | 目标变量 `closed_date` 语义未验，见 ADR 0010 §4.4；不出现在本次 Gold 层 |
| **BO-6**（负载评分） | `fact_winter_event_zone_load`（F6） | `(event_id, plow_zone)`, `load_score`, `score_status`, `forecast_model_version` | 满面板 1,298，缺失用 `score_status` 表达；`rank_factor = 0` 的行数须为 0（顺位缺失只能是 NULL） |
| **BO-7**（纵向数据集） | *不进 Gold 层* | — | 目标是 Bronze `snapshot` 分区 + 未来 Silver 纵向序列；本次交付窗口内产不出可分析历史，**不建 Gold 表**（business-objectives.md BO-7「本 BO 在本次交付中的定位」） |
| **BO-8**（推荐层） | `fact_recommendation`（F7） | `(event_id, plow_zone)`, `rank_model`, `rank_baseline`, `rank_delta` | 对历史降雪事件回测，产出分区级排序；每条建议可追溯驱动因素；"优于基线"降为内部目标不对外承诺 |
| BO-8 | `dim_recommendation_rules`（D7） | `rule_id`, `template_text` | 可解释文字模板 + 模型不可用时的降级兜底；**不得称为 AI** |

**审计列**（`etl_run_id` / `built_at` / `source_max_ingest_date`，ADR 0010 D7）不单独进矩阵——
它们是每张表的通用列，不对应特定 BO，验收判据是"每张表都有"而非"某个 BO 需要"。

---

## 3. 反向核对（表 → BO，门禁：不得有孤儿表）

| Gold 表 | 指向它的 BO | 结论 |
|---|---|---|
| `dim_plow_zone`（D1） | BO-2、BO-4 | ✅ 保留 |
| `dim_admin_label`（D2） | BO-1、BO-4、BO-6（标签） | ✅ 保留 |
| `dim_region_crosswalk`（D3） | BO-4 | ✅ 保留 |
| `dim_snowfall_event`（D4） | BO-1、BO-3、BO-6 | ✅ 保留 |
| `dim_service_type`（D5） | BO-1 | ✅ 保留 |
| `dim_channel`（D6） | BO-1 | ✅ 保留 |
| `dim_recommendation_rules`（D7） | BO-8 | ✅ 保留 |
| `fact_service_request_zone_event`（F1） | BO-1 | ✅ 保留 |
| `fact_event_zone_rank`（F2） | BO-2 | ✅ 保留 |
| `fact_plow_shift`（F3） | BO-2 | ✅ 保留 |
| `fact_parking_ban`（F4） | BO-2 | ✅ 保留 |
| `fact_request_forecast`（F5） | BO-1 | ✅ 保留 |
| `fact_winter_event_zone_load`（F6） | BO-6 | ✅ 保留 |
| `fact_recommendation`（F7） | BO-8 | ✅ 保留 |
| `fact_service_request_daily_by_label`（F8） | BO-1（描述性，§2.7） | ✅ 保留——**唯一需要提醒的一张**：它不服务任何验收标准的数字，只服务 BO-1 输出定位从"结论"降级为"背景"这一条纪律。若 S3 冻结时发现它没有任何下游消费，应重新评估是否真的要建 |

**结论：15 张表全部有 BO 指向，无孤儿表，无需删表。**

反向核对同时说明了一件事：**BO-5、BO-7 在 H1 没有对应的 Gold 表**，这不是矩阵的缺口，
是这两个 BO 本身的定位决定的（BO-5 是 P1 且目标变量未验；BO-7 的产出还没到可进 Gold
的阶段）。矩阵不需要为它们造表来"填满"。

---

## 4. 未覆盖的列（S3 需要留意）

矩阵只列了"承担验收的列"，以下几类列存在但不直接对应某条验收标准的数字，
S3 写 contract 时仍要处理，只是约束来源不是 business-objectives.md 的验收标准，
而是 ADR 0010 的建模决策本身：

| 列 | 所在表 | 约束来源 |
|---|---|---|
| `has_plow_schedule` | D1 | ADR 0010 §5 Q5：不排除无排班分区，用此列过滤，行数须 = 3（`B/D`/`X`/`Downtown`） |
| `winter_category` 是零格还是非零格 | F1 | ADR 0010 §5 Q3 / design §8 O1：满面板，零格是显式信号不是缺失 |
| `region_type` **不得出现** | F1、F2、F6、F7 等评分链事实表 | ADR 0010 D2：`grep -l "region_type" sql/ddl/fact_*.sql` 应为空（F8 除外） |
| `etl_run_id` / `built_at` / `source_max_ingest_date` | 全部 15 张表 | ADR 0010 D7：统一审计列，非 BO 验收 |

---

## 5. 下一步

1. ✅ S2 门禁已满足（正向：每条验收标准都指到了表和列；反向：无孤儿表）；
2. ✅ **S3 草稿已完成（2026-08-12）**：7 篇 `contracts/silver-contracts/*.yaml` +
   15 篇 `contracts/gold-contracts/*.yaml`，PK / 非空 / 值域 / 行数期望 / freshness
   全部落地，`forbidden_columns`（D2 护栏）与 `served_by_bo`（回链本篇矩阵）显式写出。
   `dim_region_crosswalk.calibration_window` 按 ADR 0010 §5 O5 的结论**只给了初始值，
   未锁 accepted_values**，标注等 S6 标定后再收口——**这是 S3 唯一未真正冻结的一列**。
3. Contract 冻结线 **8/23** 不变；`silver_plow_zone_boundary`（批 4 产物）仍是
   S4 建表前的唯一硬阻塞。冻结前还要做的事：
   - `snowfall_events` 的 `event_rule_version` 列需要真的加进
     `spark/schemas/weather_schemas.py` 的 `SNOWFALL_EVENT_SCHEMA`（目前只在
     contract 里，代码还没加）；
   - `dim_service_type` 的域完整性依赖 O2（谁维护）有结论，否则 S3 只能断言
     "出现过的取值都能解析"，断不了"没有漏"。
