# Gold / Silver 表结构：从 BO 反推到定稿

> **Status**: **Accepted** · **Date**: 2026-08-09 · **Accepted**: 2026-08-12
> **决策依据**: [ADR 0010](../adr/0010-gold-fact-grain-and-dimension-layering.md)（粒度与分层）·
> [ADR 0009](../adr/0009-plow-zone-as-the-unit-of-analysis.md)（分析单元）·
> [ADR 0008](../adr/0008-plow-schedule-is-a-plan-not-a-record.md)（供给侧口径）

> ✅ **本篇已定稿。** 表清单与阶段计划（`TBL-*` / `S0–S7`）与 §8 开放项
> 已于 2026-08-12 随 ADR 0010 一并过完，S0→S1 收口。正文自此冻结，
> 实际偏差记进 [launch/](../launch/README.md)。

---

## 1. 问题

`sql/ddl/` 与 `sql/dml/` **尚不存在**，Gold 层一行代码都还没写。这是好事——
零迁移成本，直接按 Trino 方言写。但也意味着**第一个写下的 DDL 会锁死后面所有
DML 与回填**。

现在必须动的原因有三个，都是时间顺序而非偏好：

1. **BO 已定稿。** 八个 BO 的口径、验收标准、指标可用性全部有实测背书，
   `business-objectives.md` 与 `metric-feasibility-audit.md` 都已收口。
   "Gold 要算什么"这个问题第一次有了确定答案。
2. **回填之后改列 = 重跑。** 311 的 Silver 是 16 GB 量级。
3. **旧的反推结论有一半作废。** 2026-08-03 的讨论记录假设了 `ward × event` 粒度，
   而 ADR 0008 / 0009 之后单元统一到了 `plow_zone`。**那份记录不能直接实现。**

时间盒是硬的：H1 会期 **2026-09-19**。

---

## 2. 约束

| 约束 | 性质 |
|---|---|
| 分析单元 = `plow_zone × snowfall_event`，面板 22 × 59 = 1,298 格 | 硬（ADR 0009） |
| `ward` / `neighbourhood` 不得承载数值 | 硬（ADR 0009 §2.2） |
| 顺位只在 19 次全市犁雪事件上有定义，其余为 NULL，**不得填 0** | 硬（ADR 0008 §3、BO-6） |
| 有效评分窗口 2015-12 起（供给侧数据起始） | 硬（BO-6） |
| Bronze 是 frozen on-disk contract，不动 | 硬（AGENTS.md） |
| 业务语义（冬季关键词 / 优先级 / 渠道）不进 `spark/transforms/` | 硬（护栏 §1） |
| H1 没有 Trino，空间判定只能在 Spark 做 | 硬（查询层属 H2） |
| 8/25 个分区几何 OGC 非法，必须先 `ST_MakeValid` | 硬（实测） |
| 建模不用 Spark MLlib，单机读 Gold Parquet | 已定（roadmap Phase 4.5） |

**显式接受的风险**：`address_count` 用当期快照归一化十年历史（BO-2 / BO-6 已
要求主动声明，本设计把声明落成 `address_count_snapshot_date` 列）。

---

## 3. 剖析结论（design README 要求的五问）

数据源本身已经全部实测过，此处只归拢到"决定什么"这一列：

| 问题 | 结论 | 决定了什么 |
|---|---|---|
| 主键是什么？唯一吗？ | 311 的行粒度是 **interaction 不是 case**：`case_id` 有 **1.87%** 重复，只按它去重会在冬季子集上丢 **24,088 行（10.9%）**。复合键 `(case_id, interaction_id)` 实测**去重后行数不变** | Silver PK 写成**约束**而不是一步 `dropDuplicates`——约束破坏时会报警，去重只会静静少一批行 |
| 时间字段格式？时区？ | 四个 Socrata 源全是 floating timestamp（本地墙钟、无 offset、无 Z）。Winnipeg 冬季 CST = UTC−6，夏季 CDT = UTC−5 | 两列并存：`open_ts_utc`（存储）+ `open_date_local`（分区列）。按 UTC 日分区会把傍晚工单挪到次日 |
| 哪些字段 NULL 率高？ | 311 全表仅 **20.9%** 带坐标，冬季子集 **80.1%**。这是上游固有特性（60.8% 是无地址的电话咨询），不是管道缺陷 | 空间命中率告警的分母必须是**有地理信息的子集**；`geo_match_status` 用三值而非裸 NULL |
| 哪些字段基数低？ | `plow_zone` 25 · `ward` 15 · `neighbourhood` 237（casefold 后；原始 242 含 5 对大小写变体）· `shift_number` 5 | 聚簇键按 `plow_zone`；`neighbourhood` 必须先 casefold，否则 McMillan 会被拆成两个报告单元 |
| 与其他源怎么关联？ | 需求侧**按点归分区**（`ST_Contains`，命中 99.9%），不经过任何文本标签；供给侧 `tix9-r5tc.snow_ban_id → mfzv-893p.id`，**0 条孤儿**，49 条禁令中 19 条有排班 | Gold 的禁令连接必须是**左连接**，且不得据此判定缺失——差集是语义不是缺数据 |

---

## 4. 方案

### 4.1 反推链条（修订版）

```
Abstract obligation                  Analysis unit          Gold grain                       Silver must provide
──────────────────────────────────────────────────────────────────────────────────────────────────────────────────
snowfall events                  →   snowfall event     →   dim_snowfall_event               daily weather series
311 complaints                   →   zone × event       →   fact_service_request_zone_event  interaction rows + point→zone
the ORDER zones are scheduled    →   zone × plow event  →   fact_event_zone_rank             shift_number, UTC + local
reported WITH ward/nbhd labels   →   label, not a grain →   dim_region_crosswalk (weighted)  ward/nbhd text on the same row
Winter Load Score                →   zone × event       →   fact_winter_event_zone_load      (all of the above)
AI-driven recommendation         →   zone × event       →   fact_recommendation              —
```

顺序只能是 **BO → Gold → Silver**。Silver 该长什么样，唯一依据是 Gold 要拿它
算什么；Gold 该长什么样，唯一依据是摘要（`business-objectives.md` §0.2 的需求
合同）承诺了什么。反过来做一定返工。

### 4.2 Gold 维度

| # | 表 | 粒度 | 服务 | 备注 |
|---|---|---|---|---|
| **TBL-D1** | `dim_plow_zone` | 25 个 `plow_zone` | BO-4 / BO-6 | 唯一带真几何的维度。列见 ADR 0010 D3 |
| **TBL-D2** | `dim_admin_label` | 15 ward + 237 nbhd | 报告标签 | **无几何**，且不留空列（ward 边界取不到） |
| **TBL-D3** | `dim_region_crosswalk` | (plow_zone, label_type, label_id) | BO-4 | 方向固定 `zone → label`；带 `weight` / `is_dominant` / `calibration_window` |
| **TBL-D4** | `dim_snowfall_event` | event_id | BO-3 | 全项目分析单元主键。含 `severity_score`(0–1) · `snow_season` · **`event_rule_version`** |
| **TBL-D5** | `dim_service_type` | 3,563 个 `type` | BO-1 / BO-5 | 冬季分类 + 优先级解析（`Pr 2` / `Priority 2` / `P2` / `_vof`）。种子表 |
| **TBL-D6** | `dim_channel` | 15 个渠道 | §2.2 渠道漂移 | `Self Service + Mobile + SMS In → VOF` + `is_comparable_pre_2022` |
| **TBL-D7** | `dim_recommendation_rules` | rule_id | BO-8 | 文字模板 + 降级兜底。**不得称之为 AI** |

### 4.3 Gold 事实

| # | 表 | 粒度 | 服务 | 行数期望 |
|---|---|---|---|---|
| **TBL-F1** | `fact_service_request_zone_event` | (event_id, plow_zone, winter_category) | M1 训练面板 | 满面板 1,298 × 类别数（§8 O1 已定：不只存非零，零请求是训练需要的显式信号） |
| **TBL-F2** | `fact_event_zone_rank` | (plow_event_id, plow_zone) | BO-2 | **418**（19 × 22，零缺失） |
| **TBL-F3** | `fact_plow_shift` | shift id | BO-2 溯源 | 418 |
| **TBL-F4** | `fact_parking_ban` | ban id | BO-2 | 49（**独立成表**，与 F3 左连接） |
| **TBL-F5** | `fact_request_forecast` | (event_id, plow_zone, model_version) | M1 输出 | 每版模型一套面板 |
| **TBL-F6** | `fact_winter_event_zone_load` | (event_id, plow_zone) | **BO-6 旗舰交付物** | 满面板 1,298，缺失用 `score_status` 表达 |
| **TBL-F7** | `fact_recommendation` | (event_id, plow_zone) | BO-8 | 含 `rank_model` / `rank_baseline` / `rank_delta` |
| **TBL-F8** | `fact_service_request_daily_by_label` | (date, label_type, label_id) | 描述性切片 | **不与评分链共用任何列** |
| ~~TBL-F9~~ | ~~`fact_service_request`〔P1〕~~ | ~~(case_id, interaction_id)~~ | BO-5 / M2 | **H1 不建**（ADR 0010 §4.4） |

**每张事实表额外带**：`etl_run_id` · `built_at` · `source_max_ingest_date`（ADR 0010 D7）。

### 4.4 Silver（相对 2026-08-03 版本几乎不动）

| # | 表 | 粒度 | 分区 | 状态 |
|---|---|---|---|---|
| **TBL-S1** | `silver_service_request` | (case_id, interaction_id) | `open_date_local` | 🔴 待建（大头，16 GB → Parquet 约 1.5–2 GB） |
| **TBL-S2** | `silver_plow_zone_boundary` | entity_id（82 多边形） | 不分区 | 🔴 待建（= 批 4 泛化 `etl_dcp.py`）。**新增** `geometry_repaired` / `area_delta_pct` |
| **TBL-S3** | `silver_plow_shift` | id（418） | 不分区，全量覆写 | 🔴 待建 |
| **TBL-S4** | `silver_parking_ban` | id（49） | 不分区 | 🔴 待建 |
| **TBL-S5** | `silver_weather_archive` | 日 | `date` | ✅ 已有 |
| **TBL-S6** | `snowfall_events` | event | 不分区 | ✅ 已有，含 `event_rule_version`（2026-08-12 补上，双判据切分同批进了 `segment_snowfall_events`，见 bus matrix §5） |
| **TBL-S7** | `silver_weather_forecast` | 小时 | `ingest_date` | ✅ 已有 |

Silver 的五条设计原则**未因两个 ADR 而变**，原样沿用：存 UTC 按本地日分区 ·
业务语义一律后移到 Gold · 空间归属进 Silver 且用三值状态 · `static` 源不按日期
分区 · Silver 不做跨事实 JOIN。

> **这五条经受住了一次分析单元变更**——单元从 ward 换到 zone，Silver 一列没改。
> 这是把业务语义挡在 Gold 外面的直接回报。

### 4.5 构建顺序与唯一硬阻塞

```
silver_plow_zone_boundary  ←── 批 4（泛化 etl_dcp.py）是唯一硬阻塞
        │
        └──> silver_service_request        (需要广播的 82 个多边形)
                    │
silver_plow_shift ──┼──> dim_plow_zone, dim_admin_label, dim_service_type, dim_channel
silver_parking_ban ─┤         │
snowfall_events ────┘         └──> dim_region_crosswalk
                                        │
                                        ├──> fact_event_zone_rank
                                        └──> fact_service_request_zone_event
                                                  │
                                                  └──> M1 ──> fact_request_forecast
                                                              └──> fact_winter_event_zone_load
                                                                    └──> fact_recommendation
```

**批 4 的优先级比 2026-08-03 那版更高**：ADR 0009 把"按点归分区"变成了**唯一**的
归属路径（不再有文本降级），没有 `silver_plow_zone_boundary` 就没有 M1 面板的
任何一行。它同时是出口 grep 仅剩的一处遗留项，也符合 roadmap Phase D 的
「能力泛化必须先于实例删除」。

---

## 5. 任务计划：S0 → S7

采用北美中小企业数仓的通行裁剪版——Kimball 维度建模 + STM + data contract +
dbt 风格测试四件套 + ADR 变更控制。**核心纪律只有一条：contract 冻结之前
schema 随便改，冻结之后改 schema 走变更流程。**

| # | 阶段 | 产物 | 门禁（过不了不进下一阶段） |
|---|---|---|---|
| **S0** | Hypothesis 收口 | 本篇 + ADR 0010 初稿 | 每张表都写明 grain 一句话；ADR 0010 §5 的五个待确认项有结论 |
| **S1** | 决策定案 | ADR 0010 状态改 `Accepted` | D1–D7 **逐条**确认；本篇状态改 `Accepted` |
| **S2** | Bus Matrix + STM | 「BO × 表」矩阵 + 逐列 source→target 映射 | 每个 BO 的验收标准都能指到具体表和列；**反向也成立**：没有 BO 指向的表删掉 |
| **S3** | Contract 冻结 🔒 | `contracts/silver-contracts/*.yaml` + `gold-contracts/*.yaml` | PK / 非空 / 值域 / 行数期望 / freshness 齐全 |
| **S4** | DDL + 骨架 | `sql/ddl/*.sql`（Trino 方言）+ `spark/schemas/` StructType | `make lint` 零告警；空表能建起来 |
| **S5** | 小样本 + 契约测试 | 单个雪季端到端跑通 | 四件套测试全绿 + **三个数字复现**（见 §6） |
| **S6** | 全量回填 + DQ 基线 | 全历史 Silver + Gold + 一份质量基线表 | 行数 / 空值率 / 分区完整性记录在案（ADR 0004 §4）；异常已解释 |
| **S7** | Finalize 🏁 | schema **v1.0** 冻结 + `CHANGELOG.md` | S2 矩阵逐格复核；M1 能直接读面板开训 |

### 5.1 测试四件套的落法

没有 dbt，用 pytest + Spark 断言，**形式不重要，四类覆盖必须有**：

| 类型 | 本项目的实例 |
|---|---|
| `unique` | `silver_service_request` 的 `(case_id, interaction_id)`；每张 fact 的键 |
| `not_null` | `dim_plow_zone.geometry_wkt`；`fact_*.etl_run_id` |
| `relationships` | `fact_*.plow_zone → dim_plow_zone`；`fact_event_zone_rank.plow_event_id → fact_parking_ban` |
| `accepted_values` | `geo_match_status` 三值；`score_status` 二值；`plow_zone` 的 25 个取值 |

### 5.2 时间盒（会期 2026-09-19，倒排）

| 周 | 主线 | 并行 |
|---|---|---|
| **W1** 8/10–8/16 | S0 + S1 | 🔴 **批 4 必须本周动**——唯一硬阻塞，且 S5 要用它的产物 |
| **W2** 8/17–8/23 | S2 + S3（**8/23 前冻结 contract**） | `silver_plow_zone_boundary` 落地 + `ST_MakeValid` |
| **W3** 8/24–8/30 | S4 + S5 | `silver_service_request` 单季跑通 |
| **W4** 8/31–9/6 | S6 全量回填 | crosswalk 权重标定 |
| **W5** 9/7–9/13 | M1 训练 + BO-6 评分回测 | 出问题**在这一周吃掉缓冲**，不挪 S3 |
| **W6** 9/14–9/19 | S7 冻结 + 讲稿 | 只收尾，不动 schema |

**关键路径 = 批 4 → S3 → S6。** S3 拖一天，S6 的全量回填整体后移一天，
而回填是唯一一个"跑错了要重跑几小时"的环节。

---

## 6. 验收判据

### 6.1 三个必须复现的数字（S5 硬门禁）

这三个数是探针在**公开 API** 上算出来的，管道跑出来必须对得上：

| 判据 | 值 | 复现入口 |
|---|---|---|
| M1 面板格数 | **1,298**（22 分区 × 59 排班期事件） | `scripts.analysis.score_collinearity` |
| 面板非零率 | **70.57%**（916 格） | `scripts.analysis.snowfall_events --thresholds 3 --accum-window-days 10 --accum-threshold-cm 10 --zone-panel` |
| 工单空间命中率 | **99.9%**（134,123 / 134,258） | `scripts.analysis.request_point_in_zone` |

> 🔴 **对不上时信探针。** BO 文档的所有结论都建在它们上面；
> 管道对不上说明管道里有一步和探针口径不一致，不是探针错了。

### 6.2 结构判据

| 判据 | 怎么验 |
|---|---|
| 事实表只有一种粒度 | 三张评分链事实表的键完全一致（ADR 0010 D1） |
| 行政单元不进 fact 键 | `grep -l "region_type" sql/ddl/fact_*.sql` 输出为空（`fact_service_request_daily_by_label` 除外） |
| 顺位缺失表示为 NULL | `fact_winter_event_zone_load` 中 `rank_factor = 0` 的行数 = 0 |
| 供给侧连接是左连接 | 49 条禁令全部出现在结果里，其中 30 条 `shift_number` 为 NULL |
| 复合键无重复 | `COUNT(*) = COUNT(DISTINCT (case_id, interaction_id))` |
| 几何已修复且有记录 | `dim_plow_zone` 中 `geometry_repaired = true` 的行数 = **8** |
| 无排班分区显式标记 | `has_plow_schedule = false` 的行数 = **3**（`B/D` · `X` · `Downtown`） |

### 6.3 数据质量基线（S6 产出）

按 ADR 0004 §4 的 SLA 基线要求，每张表记录：行数 · 各列空值率 · 分区完整性 ·
构建耗时。**这是后续告警阈值的唯一依据**——没有基线的告警阈值都是拍脑袋。

---

## 7. 被否决的选项

| 选项 | 否决理由 |
|---|---|
| 沿用 2026-08-03 讨论记录的表结构 | 它假设 `ward × event` 粒度，ADR 0008 / 0009 之后已作废一半 |
| 长表 `dim_geography(region_type, region_id)` | 形状本身在邀请 `GROUP BY region_type`，即 ADR 0009 禁止的动作。详见 ADR 0010 §4.1 |
| 先建 Silver、用 SQL 探完再定 Gold schema | ADR 0009 §4.4 已否决过；16 GB 回填一次不是几分钟 |
| 把 contract 冻结推到 W4（回填前） | 回填期间改列 = 重跑。冻结线的意义就是**给"还能不能改"画时间点** |
| H1 内补 BO-5 / M2 的工单粒度事实表 | `closed_date` 语义未验，且 BO-5 是 P1。详见 ADR 0010 §4.4 |

---

## 8. 开放项（2026-08-12 已逐条定稿，O6 除外——不阻塞 S1，按原定时间点处理）

| # | 问题 | 结论 | 定的时间点 |
|---|---|---|---|
| **O1** | `fact_service_request_zone_event` 存不存零格 | **满面板（1,298 行），不只存非零。** M1 训练需要显式的零——零请求是有效信号，不是缺失；只存非零会让下游一旦忘记补零就系统性训出偏差 | ✅ S1 定稿 |
| **O2** | `dim_service_type` / `dim_channel` 谁维护、怎么保证不漏新取值 | `dim_channel` 是闭域（`exact: 15`），风险不存在。`dim_service_type`（3,563 基数，开放域）才是真正的问题：**维护责任随改动方走**——谁的改动让新 `type` 值第一次出现在 `silver_service_request`（新数据源接入、或既有源上游新增取值），谁负责在同一批改动里更新 `config/` 的种子表，走普通 PR review，不设专职角色（项目当前是单人维护，引入职责分离没有意义）。**检测机制不是人工巡检，是构建期断言**：S4 建 `dim_service_type` 时，Gold 构建作业对 `silver_service_request.type` 与种子表做 LEFT ANTI JOIN，出现未覆盖的值就是构建失败，列出具体值——不是静默把 `winter_category`/`priority_weight` 置 null 然后指标悄悄漏掉这部分工单。这条断言就是 contract 里 `relationships` 测试的落地方式，S4 实现时对应写。 | ✅ S3 定稿（2026-08-12）；断言本身在 S4 实现 |
| **O3** | `event_rule_version` 怎么编号 | **语义化**（`v1-3cm-or-10d10cm`），不用自增。BO-3 遗留待办已确定阈值组合还会变，语义化编号自文档化，自增数字半年后无法反推规则 | ✅ S1 定稿（原定 S4，提前） |
| **O4** | 三个无排班分区在事实表里排除还是标记 | **标记**（`has_plow_schedule`），不物理排除——排除会让"这三个区有 6.5% 工单"这个事实从数据里消失 | ✅ S1 定稿 |
| **O5** | crosswalk 的 `calibration_window` 取多长 | **初始假设近 5 季**，但不是拍死的常量：S6 标定时要对比不同窗口长度下 crosswalk 权重的年际稳定性，选让 zone→label 主导份额波动最小的窗口。ρ=+0.591 只证明了漂移存在，没证明 5 季是对的尺度，5 季不写死进 contract | 初始值 S1 定稿，最终标定 S6 |
| **O6** | `closed_date` 语义 | H1 不解决。若 W5 有余量补测，否则留 H2 | 若要做 BO-5 |

---

## 9. 下一步

1. ✅ **逐条过 ADR 0010 的 D1–D7 与 §5 的五个待确认项**（= S0 收口）—— 2026-08-12 完成；
2. ✅ 本篇与 ADR 0010 同时改 `Accepted`（= S1）—— 2026-08-12 完成；
3. **进 S2**：画「BO × 表」Bus Matrix + 逐列 source→target 映射（STM）。每个 BO
   的验收标准都要能指到具体表和列，反向也成立——没有 BO 指向的表从 §4 删掉；
4. 批 4（泛化 `etl_dcp.py` → `etl_plow_zone_boundary.py`）与 S2 并行——见批 4
   本身已在 CLAUDE.md 记为完成，`silver_plow_zone_boundary`（TBL-S2）仍是 S2/S3
   之后 S4 建表时的硬阻塞，contract 冻结（S3，8/23 前）不能晚于它。
