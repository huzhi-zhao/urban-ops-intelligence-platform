# Silver / Gold ETL 实施计划

> **Status**: Draft · **Date**: 2026-08-17
>
> **上游**: [20260809-gold-silver-schema-derivation.md](20260809-gold-silver-schema-derivation.md)
> （表清单 TBL-S1…S9 / TBL-D1…D7 / TBL-F1…F8，阶段 S0–S7）·
> [20260812-gold-bus-matrix.md](20260812-gold-bus-matrix.md) ·
> [ADR 0010](../adr/0010-gold-fact-grain-and-dimension-layering.md)
> **前置上线记录**: [20260813 篇](../launch/20260813-gold-silver-schema-derivation-launch.md)（契约评审）·
> [20260814 篇](../launch/20260814-table-creation-deployment-launch.md)（建表 + §7.2/§7.4 的三条定案）
>
> 本篇只管**一件事**：把 S4 建出来的 25 张空表填满。它是 S5 → S7 的执行计划，
> 不新增也不修改任何 schema——contract 自 2026-08-13 起冻结。

---

## 1. 问题

S4 于 2026-08-14 建成 25 张表（8 Silver + 17 Gold），smoke 数据写入回读通过后
清除。**当前 24 张表是空的**（唯一有真实数据的是 `silver_weather_archive` +
`silver_snowfall_event`，由 `etl_weather_archive.py` 产出）。

可观测的缺口，逐条：

| 缺口 | 事实 |
|---|---|
| Silver job 缺 4 个 | `spark/jobs/` 只有 3 个：`etl_weather_archive.py` · `etl_weather_forecast.py` · `etl_plow_zone_boundary.py`。`silver_service_request` / `silver_plow_shift` / `silver_parking_ban` / `silver_snow_clearing_address` 无生产者 |
| Gold 一行没有 | `sql/dml/` 与 `sql/intelligence/` **目录不存在** |
| 种子表无来源 | `dim_service_type`（3,563 行）· `dim_channel`（15）· `dim_winter_category`（7）· `dim_recommendation_rules` 都是种子表，`config/seeds/` 也不存在（20260813 篇 C13 / C14） |
| M1 无输入 | `fact_service_request_zone_event`（13,068 行训练面板）是 M1 的唯一输入 |
| 一处物理残留 | `s3a://{bucket}/silver/snowfall_events/`（复数）是 C1 改名前的旧前缀，需人工清除（20260814 篇 §7.2） |

时间约束是硬的：会期 **2026-09-19**，design doc §5.2 的倒排里 W3（8/24–8/30）
要出「单季端到端跑通」、W4（8/31–9/6）全量回填、W5 M1 训练。今天是 8/17，
**W2 的余下几天与 W3 就是本篇的执行窗口**，且关键路径上的全量回填是唯一
「跑错了要重跑数小时」的环节。

---

## 2. 约束

**不可重放 / 高成本**

- `silver_service_request` 回填 4,876 天、Bronze 16 GB / 18.4 M 行。写入粒度定错
  只能重跑（20260814 篇 §7.4）。
- Bronze 的 `snapshot` 分区（`SRC-WPG-SNOW` / weather forecast）**漏采即永久缺失**，
  本篇任何步骤都不得写 Bronze。
- contract 冻结：本篇产生的任何「字段不够用」都要走变更流程记进 launch，
  **不得在 job 里私自加列**——三方一致性单测（177 项断言）会直接红。

**资源**

- 计算节点可用内存 **7 GB**（Trino / Hive Metastore / Superset 与 Spark 共用）。
  一次提交十年 311 会 OOM，切片是必须的，不是优化。
- Trino / Hive Metastore 是**外部平台级服务**，不在本仓库 compose 栈里
  （ADR 0006 §9）。`make stack-up` 之后仍然可能连不上——每批开工前先确认。

**已定死、本篇必须照做的三条**（20260814 篇 §7.2 / §7.4）

1. **C6/C17**：增量与幂等统一为 `INSERT OVERWRITE PARTITION`，**覆盖单位 = 一整天的分区**，不用 `MERGE`。
2. **C7**：`silver_service_request` 保持 `open_date_local` 日分区；写入前
   `repartition(N, "open_date_local")` 再 `partitionBy`，`N = clamp(窗口天数, 1, 64)`；
   **每个日分区恰好 1 个文件**。
3. **C1/C2**：物理路径与列名已统一为单数 `snowfall_event` / `snowfall_event_id`。

**城市无关护栏**（CLAUDE.md §护栏）

Winnipeg 专有字面量（`plow_zone` / `ward` / `neighbourhood` / dataset id / 冬季分类
关键词 / 渠道归一化映射）**不得进 `spark/transforms/`**。job 文件可以带字段名映射
（`etl_plow_zone_boundary.py` 已是这个形态：通用逻辑在 transform，Winnipeg 字段名在 job），
语义字典进 `config/seeds/` 与 Gold 维表。

**显式接受的风险**

- 0.3–0.5 MB/日分区是反推值、**未实测**。第一个季度跑完后核一次对象大小。
- `dim_region_crosswalk` 的权重标定窗口未定（见 §6 O2），先用全量窗口出可跑的版本。

---

## 3. 方案

### 3.0 Gold 层用 Trino SQL，不用 Spark

一个必须先说清的执行引擎选择：**Silver 全部在 Spark，Gold 全部在 Trino SQL**
（`sql/dml/` + `sql/intelligence/`），M1 在 Python。

理由只有一条是决定性的：**空间归属在 Silver 就做完了**。ADR 0009 把「按点归分区」
定为唯一归属路径，`silver_service_request.plow_zone` 落盘时就已是解析好的分区标签，
Gold 侧不再需要任何几何函数，因此不需要 Spark。剩下的都是 join + 聚合 + 算术，
Trino 直接读 Hive 外部表就能做，且 `sql/dml/` 这个目录本来就是为此存在的。
被否决的替代见 §4。

### 3.1 分批

六批，每批一个 PR，批与批之间停下等确认（feedback: 增量交付）。

```
E0 清理与骨架   ──> E1 三张小 Silver ──┐
                                      ├──> E2 silver_service_request（大头）
                                      │         │
                                      └─────────┴──> E3 Gold 维表 ──> E4 Gold 事实
                                                                        │
                                                                  E5 M1 + 评分链
                                                                        │
                                                                  E6 DQ 基线 + S7 冻结
```

---

#### E0 · 清理与骨架（半天）

- 计算节点上重跑一次 `etl_weather_archive`，确认 `silver/snowfall_event/`（单数）
  有数据后**删掉 `silver/snowfall_events/`（复数）**。
  ⛔ 只删这一个前缀，不碰 `silver/weather_archive/` 与 `bronze/`。
- 建 `sql/dml/` · `sql/intelligence/` · `config/seeds/`，各带一份 README 说明落什么。
- 新建 `spark/transforms/zone_assignment.py`：**按点归作业分区**的通用 transform
  （广播多边形集 → `ST_Contains` 语义 → 三值 `geo_match_status`）。
  角色名命名，不出现 `plow_zone`；它是 E1 与 E2 共用的那一份实现。
- 复核 Trino 连通性与 25 张表存在性：`make ddl-smoke` 的只读部分。

**门禁**：`make lint` + `make test-unit` 全绿；复数前缀在对象存储上不存在。

---

#### E1 · 三张小 Silver 表（1 天）

三个新 job，都是全量覆写、无日期窗口，量级 418 / 49 / 25 行，形态照抄
`etl_plow_zone_boundary.py`（同一 static 骨架 + `coalesce(1)`）。

| job | 表 | 要点 |
|---|---|---|
| `etl_plow_shift.py` | `silver_plow_shift`（418） | floating timestamp → UTC（`timestamp_normalizer`，`America/Winnipeg`）；`shift_number` string→int；`snow_ban_id` 保留原样，**不在 Silver 做与 ban 的 join**（Silver 不做跨事实 JOIN） |
| `etl_parking_ban.py` | `silver_parking_ban`（49） | 同上时区处理；`ban_type_id` 原样 |
| `etl_snow_clearing_address.py` | `silver_snow_clearing_address`（25/采集日） | 唯一读 `snapshot` Bronze 的 Silver job：读 `ingest_date={D}/data.ndjson.gz`，**用 E0 的 `zone_assignment` 把地址点归到分区**，再按分区聚合 `address_count`；`snapshot_date` = `ingest_date`，进 PK |

**门禁**（可执行）：行数 418 / 49 / 25；三张表 PK 无重复；`shift_start_utc <
shift_end_utc` 全真；`silver_plow_shift` 的 25 个 `plow_zone` 取值 ⊆
`silver_plow_zone_boundary` 的取值集。

---

#### E2 · `silver_service_request`（2–3 天，本批最重）

`etl_plow_shift` 之外唯一带日期窗口的新 job：`spark/jobs/etl_service_request.py`，
签名与 `etl_weather_archive.py` 对齐（`--bucket --start --end`，`[start, end)`）。

处理链：

1. 读 Bronze `daily` 分区（`{YYYY-MM}/data_{date}.ndjson.gz`），**显式传 schema**
   （禁止无 schema 的 `read.json`）。
2. `open_ts_utc` = floating ts + `America/Winnipeg` → UTC；
   `open_date_local` = **本地日**（按 UTC 日分区会把傍晚工单挪到次日）。
3. 空间归属：广播 82 个多边形（已 `make_valid` 修复），`zone_assignment` 输出
   `has_geo` / `geo_match_status`（三值）/ `plow_zone`。
4. `ward_raw` / `neighbourhood_raw` **原样保留**——casefold 与归一化在 Gold
   `dim_admin_label` 做，不在 Silver。
5. PK `(case_id, interaction_id)` 作**断言**而非 `dropDuplicates`：重复即失败告警。
   7 天回溯窗口天然重复拉取的是**同一分区的整体覆写**，由 C6 的覆盖语义解决，不靠去重。
6. 写入：`repartition(N, "open_date_local")` → `partitionBy("open_date_local")` →
   `partitionOverwriteMode=dynamic` + `mode("overwrite")`，`N = clamp(days, 1, 64)`。
7. 拒绝行进 `silver/_rejects/service_request/`，与既有 job 一致。

配套：

- `dags/dag_silver_service_request.py`（`0 7 * * *`，7 天滑动回溯，`catchup=True`）
  与 `dags/dag_backfill_silver_service_request.py`（手动、任意 `[start, end)`）。
  DAG 只做调度，窗口切片由 job 的 `[start, end)` 承担（既有设计：1 DAG Run = 1 窗口）。
- `scripts/backfill/plan_silver_service_request.sh`：**照抄 Bronze 侧
  `plan_wpg_311_backfill.sh` 的窗口划分**（8 个雪季 + 按年切的全量段），
  `source _plan_lib.sh` 复用 checkpoint / 告警 / watchdog。切片粒度就是重跑粒度。

**门禁**：先单季（2024-11 → 2025-04）跑通再谈全量。

- `COUNT(*) = COUNT(DISTINCT (case_id, interaction_id))`
- 每个日分区的**文件数 == 1**
- 空间命中率：分母是 `has_geo = true` 的子集，达到 **99.9%**
- 同一窗口重跑两次，分区行数与校验和一致（幂等）
- 实测一次日分区对象大小，与 0.3–0.5 MB 的估计对账

---

#### E3 · Gold 维表（2 天）

七张维表 + `dim_winter_category`。种子数据落 `config/seeds/*.csv`，
由 `sql/dml/dim_*.sql`（`INSERT OVERWRITE`，全量重建）或一次性 bootstrap 脚本装载。

| 表 | 来源 | 要点 |
|---|---|---|
| `dim_winter_category`（7） | 种子 | 先建，`dim_service_type` 的 FK 指它 |
| `dim_service_type`（3,563） | bootstrap 脚本扫 Silver 的 `type` 取值 + 关键词字典 | 🔴 **构建期 anti-join 必须 = 0**：未覆盖的 `type` 值让构建失败，不静默 null。首次预计一次性报出大批值（C13）。多关键词命中用 first-match-wins，**该规则未验证**（§6 O3） |
| `dim_channel`（15） | 种子 | `Self Service + Mobile + SMS In → VOF`；`is_comparable_pre_2022` |
| `dim_plow_zone`（25） | `silver_plow_zone_boundary` 聚合 + `silver_snow_clearing_address` | `address_count` 血缘指 S9；`has_plow_schedule=false` 的 3 行（`B/D` · `X` · `Downtown`）显式建模 |
| `dim_admin_label`（15+237） | Silver 的 `ward_raw` / `neighbourhood_raw` | `neighbourhood` **先 casefold**（242 → 237，否则 McMillan 被拆成两个报告单元）；**无几何、不留空列** |
| `dim_snowfall_event`（99） | `silver_snowfall_event` | 直通 + `severity_score` / `snow_season` / `event_rule_version` / `accum_flag` |
| `dim_plow_event`（19） | `silver_plow_shift` + `silver_parking_ban` | 三处 FK 指它（B1） |
| `dim_region_crosswalk` | zone × label 加权 | 方向固定 `zone → label`；带 `weight` / `is_dominant` / `calibration_window`。标定窗口见 §6 O2 |
| `dim_recommendation_rules` | 种子 | 文字模板 + 降级兜底。**不得称之为 AI** |

**门禁**：`dim_service_type` anti-join = 0；`dim_plow_zone` 中
`geometry_repaired = true` 的行数 = **8**、`has_plow_schedule = false` = **3**；
`dim_admin_label` 行数 15 + 237；每张维表 PK 唯一。

---

#### E4 · Gold 事实表（2 天）

`sql/dml/fact_*.sql`，全部 `INSERT OVERWRITE PARTITION`，**日分区为覆盖单位**（C6）。
每张表带 `etl_run_id` · `built_at` · `source_max_ingest_date`（ADR 0010 D7）。
禁 `SELECT *`，日期一律参数化。

| 表 | 行数期望 | 要点 |
|---|---|---|
| `fact_plow_shift`（F3） | 418 | 直通 Silver + FK 到 `dim_plow_event` |
| `fact_parking_ban`（F4） | 49 | **独立成表**，与 F3 **左连接**；30 条 `shift_number` 为 NULL 是语义不是缺数据 |
| `fact_event_zone_rank`（F2） | **418**（19 × 22，零缺失） | `rank_factor = shift_number / 5`，值域 `[0.2, 1]`，**`rank_factor = 0` 的行数必须为 0** |
| `fact_service_request_zone_event`（F1） | **13,068**（22 × 99 × 6 类） | 满面板，零请求作为显式训练信号写入，不是只存非零 |
| `fact_winter_request_daily_by_label`（F8） | 描述性切片 | 只装冬季工单；**不与评分链共用任何列** |

**门禁**：五张表行数逐个对上；三张评分链事实表的键完全一致（ADR 0010 D1）；
`grep -l "region_type" sql/ddl/fact_*.sql` 除 F8 外为空；M1 面板格数
**1,298**（22 × 59 排班期事件）、非零率 **70.57%（916 格）** 与探针一致。

> 🔴 三个数字对不上时**信探针**——管道里有一步和探针口径不一致。

---

#### E5 · M1 + 评分链（3 天，跨 W5）

- `fact_request_forecast`（F5）：M1 读 F1（含类别维度）训练、预测时不含类别维度，
  `predicted_count` = 跨类别求和；带 `baseline_count` 与 `model_version`。
- `fact_winter_event_zone_load`（F6）：`sql/intelligence/`。满面板 1,298，缺失用
  `score_status` 表达（预期 `scored` ≈ 374、`partial_no_rank` ≈ 924），
  `score_weight_profile` ∈ {`full_3factor`, `demand_weather_only`} 与 `score_status` 绑死。
  `weather_severity_factor` 在 H1 内是**事件级常量**（A2 方案②），断言「同事件跨分区取值相同」。
- `fact_recommendation`（F7）：PK 含 `model_version`；`rank_model` / `rank_baseline` / `rank_delta`。

**门禁**：`score_status` 分布落在上述量级内（偏离说明犁雪↔降雪对齐逻辑变了，回查 A1/B1）；
F6/F7 满面板 1,298；`rank_factor = 0` 行数 = 0。

---

#### E6 · DQ 基线 + S7 冻结（1 天）

- 每张表记录：行数 · 各列空值率 · 分区完整性 · 构建耗时（ADR 0004 §4）。
  **这是后续告警阈值的唯一依据**，没有基线的阈值都是拍脑袋。
- 测试四件套补齐：`unique` / `not_null` / `relationships` / `accepted_values`
  （design §5.1 的实例清单逐条）。
- S2 bus matrix 逐格复核；`CHANGELOG.md` 记 schema **v1.0**；写 launch 记录。

---

## 4. 被否决的选项

| 选项 | 否决理由 |
|---|---|
| Gold 也用 Spark 写 | 空间归属已在 Silver 完成，Gold 不需要几何函数；再引一层 Spark 会在 7 GB 内存里与 Trino 抢资源，而 `sql/dml/` 目录与 25 张 Hive 外部表本就是为 Trino 准备的 |
| Gold 增量用 `MERGE` | C6 已定：`INSERT OVERWRITE PARTITION`。Hive 外部表上的 `MERGE` 需要 Iceberg，而 Iceberg 迁移是 ADR 0006 §5 的后续事项，不在 H1 |
| Silver 去重用 `dropDuplicates((case_id, interaction_id))` | 去重会**静静少一批行**；PK 破坏是上游变更的信号，必须报警。design §3 第一行已给出这条判据 |
| `silver_service_request` 改按月分区以解决小文件 | 要改分区列 + 重建表，且会让 7 天回溯的增量每天重写整月。C7 定案已否决（20260814 篇 §7.4） |
| 一次提交十年 311 回填 | 7 GB 内存 OOM；且切片粒度就是重跑粒度，一个大窗口失败等于全部重来 |
| 在 Silver 做 `neighbourhood` casefold / 渠道归一化 | 业务语义一律后移到 Gold（Silver 五条原则）。这条原则已经扛过一次分析单元变更，不为省一步破例 |
| 建 `fact_service_request`（原 TBL-F9） | H1 不建（ADR 0010 §4.4） |
| 给三张 static Silver 表建 ingest/silver DAG | 静态参照表一次全量覆写，没有调度可言（DAG 数量纪律）。手动或随 E3 重建时触发 |

---

## 5. 验收判据

全部可执行，按批归拢在 §3 各小节。整体收口这几条：

```sql
-- 复合键无重复
SELECT COUNT(*) - COUNT(DISTINCT (case_id, interaction_id)) FROM hive.uoip_silver.silver_service_request;  -- 0

-- 顺位缺失表示为 NULL，不是 0
SELECT COUNT(*) FROM hive.uoip_gold.fact_winter_event_zone_load WHERE rank_factor = 0;  -- 0

-- 几何已修复且有记录 / 无排班分区显式标记
SELECT COUNT(*) FROM hive.uoip_gold.dim_plow_zone WHERE geometry_repaired = true;   -- 8
SELECT COUNT(*) FROM hive.uoip_gold.dim_plow_zone WHERE has_plow_schedule = false;  -- 3

-- 供给侧是左连接
SELECT COUNT(*) FROM hive.uoip_gold.fact_parking_ban;  -- 49，其中 30 条 shift_number IS NULL
```

```bash
# 行政单元不进评分链 fact 键
grep -l "region_type" sql/ddl/fact_*.sql   # 只允许 fact_winter_request_daily_by_label

# 每个日分区恰好 1 个文件
aws --endpoint-url "$S3_ENDPOINT_URL" s3 ls --recursive "s3://$S3_BUCKET_NAME/silver/service_request/"

make lint && make test-unit
```

三个探针数字（S5 硬门禁）：面板 **1,298** 格 · 非零 **916** 格 · 空间命中
**134,123 / 134,258**。复现入口见 design §6.1。

---

## 6. 开放项

| # | 未定的事 | 建议 | 必须定下来的时点 |
|---|---|---|---|
| **O1** | 🔴 **顺位不是常量**：前后半期 ρ = +0.591，V/M 两个分区移动超过一整个班次。BO-6 的 0.30 顺位权重**不得喂十年均值** | `dim_region_crosswalk.calibration_window` 与 `rank_factor` 都取**近期窗口**（建议最近 3 个雪季），窗口值写进列而不是藏在 SQL 里 | E3 之前 |
| **O2** | `dim_region_crosswalk` 的权重标定窗口与 `is_dominant` 并列裁决规则（C10/C11） | 先按 O1 的窗口出可跑版本，`support_n` 留待观察真实数据后补 | E3 内 |
| **O3** | `dim_service_type` 多关键词命中的仲裁：first-match-wins 只是建议，**未验证** | 构建脚本把多命中的 `type` 值全部打印，人工过一遍再定 | E3 内 |
| **O4** | BO-3 的事件定义已确认需要在单日阈值之外加**滚动累积判据**（`accum_flag` 已落地），但改动会连带改变 N、ward × 事件面板与 BO-8 回测次数 | 本篇按 N=99/59 的当前定义执行；若 N 再变，E4/E5 的行数判据同步改，**不改 schema** | E4 之前确认 N 冻结 |
| **O5** | 日分区 0.3–0.5 MB 未实测 | E2 单季跑完立即核 | E2 门禁内 |
| **O6** | `closed_ts_utc` 系统性缺失（C8）对 BO-5 的影响 | BO-5 是 P1，H1 不阻塞；记录不处理 | H1 之后 |
| **O7** | `docs/guide/` 7 篇手册仍按 GCS/BigQuery 描述；Grafana 未部署 | 与本篇并行、不占关键路径 | S7 之前 |

---

## 7. 时间盒

| 窗口 | 批次 | 说明 |
|---|---|---|
| 8/17–8/19 | E0 + E1 | 小表先跑，把 static 骨架和 `zone_assignment` 验证掉 |
| 8/20–8/23 | E2（单季） | 🔴 关键路径起点。单季不通不进全量 |
| 8/24–8/26 | E2（全量回填） | 按 plan 脚本切片，checkpoint 可续跑 |
| 8/27–8/30 | E3 + E4 | Gold 维表 → 事实表，三个数字复现 |
| 8/31–9/6 | E5 | M1 训练 + 评分链回测 |
| 9/7–9/13 | 缓冲 | 出问题在这里吃掉，**不挪 contract 冻结线** |
| 9/14–9/19 | E6 + 讲稿 | 只收尾，不动 schema |

关键路径 = **E2 单季 → E2 全量 → E4**。E2 拖一天，后面整体后移一天。
