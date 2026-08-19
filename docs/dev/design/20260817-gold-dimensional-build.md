# Gold 维表与事实表（L2）

> **Status**: Draft（框架） · **Date**: 2026-08-17
>
> **上游需求**: [20260817-etl-implementation.md](20260817-etl-implementation.md)（E3 + E4）
> **前一次上线**: [L1 Silver 全链路跑通](20260817-silver-etl-runnable.md) —— **硬前置**
> **后一次上线**: [L3 评分链与 M1](20260817-scoring-chain-and-m1.md)
> **相关**: [20260812-gold-bus-matrix.md](20260812-gold-bus-matrix.md) ·
> [ADR 0010](../adr/0010-gold-fact-grain-and-dimension-layering.md)（Gold 粒度与维度分层）
>
> ⚠️ **本篇是框架,不是可执行计划。** 只把背景、边界、已知风险和待定项定下来,
> 细化到可动手的程度是 L1 上线之后的专门任务 —— 那时才有真实的 Silver 数据
> 可以对着写 SQL,现在写的每个行数判据都还是转述探针。

---

## 1. 问题

L1 交付之后,8 张 Silver 表全部有数据,**17 张 Gold 表仍然零行**。
`sql/dml/` 与 `config/seeds/` 自 E0（2026-08-17）建起,至今只有 README。

L2 要填的是其中两类,共 **13 张**:

| 类 | 张数 | 内容 |
|---|---|---|
| 维表 | 9 | `dim_winter_category` · `dim_service_type` · `dim_channel` · `dim_plow_zone` · `dim_admin_label` · `dim_snowfall_event` · `dim_plow_event` · `dim_region_crosswalk` · `dim_recommendation_rules` |
| 描述性 / 直通事实表 | 5 | `fact_plow_shift` · `fact_parking_ban` · `fact_event_zone_rank` · `fact_service_request_zone_event` · `fact_winter_request_daily_by_label` |

剩下 4 张（`fact_request_forecast` · `fact_winter_event_zone_load` ·
`fact_recommendation` + 评分链）在 L3。分界线是**「是不是在算分或训练模型」**:
是的进 L3,不是的进 L2。物理上都叫 Gold 表,但做的事不是一类。

## 2. 为什么单独一次上线

三条,都不是工程量:

1. **回滚粒度不同。** Gold 是 `INSERT OVERWRITE PARTITION`,分钟级可反复重建;
   L1 的全量回填跑完就是既成事实。混在一次上线里,「能不能回滚」没有统一答案。
2. **最大不确定量是人工活,不是技术活。** `dim_service_type` 要给 **3,563** 个
   `type` 取值定语义（P1/P2/P3 承诺时限、冬季分类关键词）。这批取值的过审进度
   不该压在 Silver 回填的关键路径上。
3. **Gold 侧一个 DAG 都没有。** 17 张表靠什么触发、增量窗口参数从哪来 ——
   这是伞篇 E3/E4 完全没描述的一块工作（§4 O1）。

## 3. 已定死、L2 不重开的口径

- **执行引擎 = Trino SQL,不是 Spark。** 空间归属在 Silver 就做完了
  （ADR 0009 / L1 §3.3）,Gold 侧不需要任何几何函数,剩下的都是 join + 聚合 + 算术。
  再引一层 Spark 会在 7 GB 内存里与 Trino 抢资源。
- **增量与幂等 = `INSERT OVERWRITE PARTITION`,覆盖单位是一整天的分区,不用 `MERGE`**
  （C6/C17）。Hive 外部表上的 `MERGE` 需要 Iceberg,不在 H1。
- **禁 `SELECT *`,日期一律参数化。** 每张表带 `etl_run_id` · `built_at` ·
  `source_max_ingest_date`（ADR 0010 D7）。
- **业务语义落 `config/seeds/*.csv` 与维表,不落库代码**（城市无关护栏 §1）。
- **不改 schema / contract**（2026-08-13 冻结）。

## 4. 框架:三段,顺序是硬的

```
L2-a 种子与语义     ──> L2-b 维表(9)  ──> L2-c 事实表(5)
(config/seeds/*.csv)     (sql/dml/dim_*)     (sql/dml/fact_*)
```

`dim_winter_category` 必须先建 —— `dim_service_type` 的 FK 指它。
事实表必须最后 —— 三张评分链事实表的键要完全一致（ADR 0010 D1）。

### 已知的硬判据（细化时逐条落成 SQL）

这些数字来自探针,**不是估计**,对不上时信探针:

| 判据 | 值 |
|---|---|
| `dim_service_type` 构建期 anti-join | **= 0**（未覆盖的 `type` 值让构建失败,不静默 null） |
| `dim_plow_zone` 中 `geometry_repaired = true` | **8** |
| `dim_plow_zone` 中 `has_plow_schedule = false` | **3**（`B/D` · `X` · `Downtown`,约 31% 面积,是语义不是缺数据） |
| `dim_admin_label` 行数 | **15 + 237**（`neighbourhood` 先 casefold,242 → 237,否则 McMillan 被拆成两个报告单元） |
| `fact_event_zone_rank` 行数 | **418**,且 `rank_factor = 0` 的行数**必须为 0** |
| `fact_service_request_zone_event` 行数 | **13,068**（22 × 99 × 6 类,满面板,零请求作为显式训练信号写入） |
| `fact_parking_ban` 行数 | **49**,其中 30 条 `shift_number` 为 NULL（语义,与 F3 左连接） |
| **冬季子集行数**（L1 移交） | **≈ 275,282**,即全表的 **≈1.5%**。这条判据原挂在 L1 全量门禁上，但它按 `type` 做冬季关键词匹配，而 `winter_category` 正是在 `dim_service_type` 里解析的——Silver 没有这一列，L1 无从验证。**在 `dim_service_type` 建好后核对。** ⚠️ 关键词必须**精确匹配**：`%ICE%` 会命中 Serv-**ice** / Pol-**ice** / Not-**ice** / Invo-**ice**，松匹配实测 10.40%，真值 1.50%（`contracts/api-contracts/winnipeg-311.yaml` notes）。分母用 Silver 实测的 12,474,313，**不是上游的 18.4 M**（两者口径不同，见 L1 launch §3.2） |
| **空间命中率精确复现**（L1 移交） | **134,123 / 134,258 = 99.9%**（`scripts.analysis.request_point_in_zone`）。分母是「排班期 × 冬季 × 带几何」的工单，两个筛选条件 Silver 都没有——`winter_category` 在 `dim_service_type`，排班期在排班表。L1 只能验到全表口径的 **2,841,151 / 2,842,219 = 99.962%**（同量级、略高，符合预期）。**在两张维表建好后按探针口径精确核对** |

```bash
# 行政单元不进评分链 fact 键
grep -l "region_type" sql/ddl/fact_*.sql   # 只允许 fact_winter_request_daily_by_label
```

## 5. 开放项（细化前必须定的）

| # | 未定的事 | 现有建议 | 时点 |
|---|---|---|---|
| **O1** | 🔴 **Gold 调度入口完全未设计。** 17 张表怎么触发、日期参数怎么传、维表全量重建与事实表增量的关系 | 未定。可能是一个 `dag_gold_*` + Trino operator,也可能是一个 `make` 目标 + 手动。**先定再写 SQL**,否则 SQL 的参数形状会被返工 | ✅ **已定案 2026-08-19，见 §6**。结论比预想简单：17 张表**没有一张是分区表**，16 张的粒度里根本没有日期，整表全量重建即可，**不需要日期参数**。C6 在 Gold 的表述需要修订（§6.3），待签字 |
| **O2** | 🔴 **顺位不是常量**:前后半期 ρ = +0.591,V/M 两个分区移动超过一整个班次。BO-6 的 0.30 顺位权重**不得喂十年均值** | `dim_region_crosswalk.calibration_window` 与 `rank_factor` 都取**近期窗口**（建议最近 3 个雪季）,窗口值写进列而不是藏在 SQL 里 | 维表之前 |
| **O3** | `dim_region_crosswalk` 的权重标定窗口与 `is_dominant` 并列裁决规则（C10/C11） | 按 O2 的窗口出可跑版本,`support_n` 留待观察真实数据后补 | L2 内 |
| **O4** | `dim_service_type` 多关键词命中的仲裁:first-match-wins 只是建议,**未验证** | 构建脚本把多命中的 `type` 值全部打印,人工过一遍再定 | 种子段 |
| **O5** | BO-3 的事件定义已确认需要在单日阈值之外加**滚动累积判据**（`accum_flag` 已落地）,改动会连带改 N、ward × 事件面板与回测次数 | 按 N = 99/59 的当前定义执行;若 N 再变,行数判据同步改,**不改 schema** | 事实表之前确认 N 冻结 |
| **O6** | 「后排分区户数更多」的 `r = +0.491` 须在近期窗口上重算（十年均值已被证明会掩盖重排） | 跟 O2 同一个窗口一起做 | L2 内 |
| **O8** | **`dag_audit_bronze` 只核对分区存在性，不核对内容。** 2026-08-18 的分页事故里，34 个含重复行、23 个丢行的 Bronze 分片在现有审计下全部显示健康——manifest 的 `sha256_checksum` 只保证「写下去的字节没坏」，不保证「取回来的行是对的」 | 三层校验 A/B/C 的完整方案（含四条落地约束：PK 从契约派生不硬编码、snapshot 只报不补、迟到容差只给最近 N 天、日常滚动窗口+全量单独跑）已写在 [postmortem 附录](../postmortem/bronze-socrata-pagination-incident.md)。探针 `bronze_duplicate_scan.py`（校验 B）与 `bronze_rowcount_reconcile.py`（校验 C）已实现，搬进 DAG 即可 | L2 内，与 O1/O7 一起定 |
| **O7** | **Silver/Gold 侧没有分区完整性检查。** Bronze 有 `dag_audit_bronze`（发现缺口并自动补,snapshot 只报不补）,Silver 没有对应物——某个日分区写成 0 行或干脆没写,目前没有任何东西会主动发现,只靠 CLAUDE.md 的「升级人类」条款靠人看。L1 期间是理论问题;F1 打开增量 DAG、每天自动写分区之后变成值班问题 | 未定。可能复用 `dag_audit_bronze` 的派生式思路（从 registry/DDL 派生审计目标,不硬编码表名）,但 Silver 的缺口**不可自动补**——重跑窗口是幂等的,所以「补」这次是合法的,与 snapshot 不同 | L2 内,与 O1 的调度入口一起定 |

## 6. O1 定案：Gold 执行入口

> 2026-08-19 补写。O1 是「L2 细化的第一件事」，因为它决定 SQL 的参数形状。

### 6.1 先决事实：Gold 一张分区表都没有

`sql/ddl/` 的 17 张 Gold 表，`WITH (...)` 里**全部只有 `format` 与
`external_location`，没有一个 `partitioned_by`**。

这与 C6「增量与幂等 = `INSERT OVERWRITE PARTITION`，覆盖单位是一整天的分区」
直接冲突：非分区表上没有 `PARTITION` 可以 OVERWRITE。
`fact_winter_request_daily_by_label.sql` 的头注在建表时就记下了这一点
（「No partitioning/freshness declared here beyond C6's blanket rebuild note」），
当时按待议留下，没有人回来处理。

**C6 是一条 Silver 形状的规则，被整体搬到了 Gold。** 它在 Silver 是对的：
`silver_service_request` 有 4,878 个日分区、12.4 M 行，7 天回溯窗口必须只覆盖
自己那几天。Gold 不是这个形状。

### 6.2 判据：按粒度分，不按「是不是 fact」分

逐表看**粒度里有没有日期**：

| 表 | 粒度 | 行数 | 日期在粒度里？ |
|---|---|---|---|
| 9 张 `dim_*` | 实体 | 7 – 3,563 | ❌ |
| `fact_plow_shift` | 班次 | 418 | ❌ |
| `fact_parking_ban` | 禁停 | 49 | ❌ |
| `fact_event_zone_rank` | 事件 × 分区 | 418 | ❌ |
| `fact_service_request_zone_event` | 事件 × 分区 × 类别 | 13,068 | ❌ |
| `fact_winter_event_zone_load` | 事件 × 分区 | 1,298 | ❌ |
| `fact_request_forecast` | 事件 × 分区 | — | ❌ |
| `fact_recommendation` | 事件 × 分区 × 模型版本 | — | ❌ |
| **`fact_winter_request_daily_by_label`** | **(date, label_type, label_id)** | **≈1.6 M** | ✅ **唯一一张** |

**16 / 17 张表的粒度里根本没有日期。** 评分链那几张的时间维度是
「降雪事件」而不是「日」——事件是 `dim_snowfall_event` 的一行，不是一个日分区。
给它们造一个日分区列，是为了满足一条规则而伪造一个维度。

### 6.3 定案

1. **16 张表：整表全量重建，不带日期参数。**
   最大的 13,068 行，`dim_service_type` 3,563 行，其余三位数。全部秒级。
   这与本篇 §2 的「Gold 分钟级可反复重建」是同一件事——**能整表重建，就没有
   增量的必要**，增量只会引入一个没人验证的状态维度。

2. **`fact_winter_request_daily_by_label`（F8）单独处理。** 1.6 M 行、粒度含日期，
   是唯一一张增量有意义的表。两条路：整表重建（可接受，但每次全扫 Silver 冬季子集），
   或给它加 `partitioned_by = ARRAY['date']` 走真正的 C6 语义。
   🔴 **后者要改 DDL，而 schema 自 2026-08-13 冻结** —— 走变更流程，不在本篇私自改。
   F8 只服务 BO-1 描述性切片，**不在评分链上**（§3 已定：不与评分链共用任何列），
   所以它可以最后做，不阻塞 L2 的其余部分。

3. **C6 在 Gold 的表述需要修订。** 这不是 L2 自行推翻已定项——C6 的原始语境
   （20260814 篇 §7.2）讨论的是 Silver 与增量写入，Gold 侧的「blanket rebuild note」
   本就没有落到物理设计上。建议改为：
   > **Silver**：`INSERT OVERWRITE PARTITION`，覆盖单位一整天的分区。
   > **Gold**：整表全量重建；粒度含日期且体量足够大的表（当前只有 F8）另议。
   ⚠️ **需要签字确认后才写进 CLAUDE.md 与伞篇**，本节先记录判据。

### 6.4 触发方式

一个 DAG：`dag_gold_build`，`schedule=None`（手动）先行，L3 之后再谈定时。

理由是**上游根本不是日频的**：9 张维表里 4 张来自种子 CSV（改了才需要重建）、
3 张来自 static 参照表（上游是覆盖式全量），只有 `dim_snowfall_event` 与
评分链事实表跟着 Silver 走。给一个上游按周/按季变化的东西配日调度，
只会每天跑一次没有产出的重建。

依赖顺序是硬的，任务组照 §4 的三段：

```
seeds ──> dim_winter_category ──> dim_service_type ──┐
                                                     ├──> facts (5)
      dim_channel / dim_plow_zone / dim_admin_label ─┤
      dim_snowfall_event / dim_plow_event ───────────┤
      dim_region_crosswalk ──────────────────────────┘
```

`dim_winter_category` 必须最先（`dim_service_type` 的 FK 指它）；
事实表必须最后（三张评分链事实表的键要完全一致，ADR 0010 D1）。

### 6.5 执行器：复用 `apply_ddl.py` 的连接层

**不写第二套 Trino 连接。** [`scripts/ddl/apply_ddl.py`](../../../scripts/ddl/apply_ddl.py)
已有 `load_trino_settings` / `_connect` / `render_ddl`（`{bucket}` 占位符渲染）
与按层派生 schema 的逻辑。新增 `scripts/gold/build_gold.py` 复用这些，
读 `sql/dml/*.sql` 按依赖顺序执行，DAG 侧用 `PythonOperator` 调用
——与 `sync_partition_metadata` 同一个形态，不引入新的 operator 依赖。

⚠️ **待实测：Trino 的整表覆写写法。** 候选是 `CREATE OR REPLACE TABLE ... AS SELECT`
（需要 Trino 438+）、`DELETE FROM` + `INSERT`、或 `DROP`/`CREATE`/`INSERT`。
三者对「外部表 + 已有 `external_location`」的行为不同，且 `DROP` 会不会连带删
对象存储数据取决于表是不是 external。**写第一条 DML 之前先在 smoke prefix 上试**，
不要在生产表上试。计算节点的 Trino 版本也要一并记下来。

### 6.6 顺带解决的两个开放项

- **O7（Silver 分区完整性检查）**：Gold 构建器每次跑之前先核一次
  `silver_service_request` 的分区数与预期日历天数之差，缺口就拒绝构建。
  比单独造一个审计 DAG 便宜，且 Gold 本来就是 Silver 唯一的下游消费者。
- **O8（`dag_audit_bronze` 核不到内容）**：两个探针
  （`bronze_duplicate_scan` / `bronze_rowcount_reconcile`）已实现，
  搬进 DAG 即可。**与本节的 Gold 调度是两件事**，不要合并——
  Bronze 审计是日频的，Gold 构建不是。

---

## 7. 时间盒（占位）

伞篇 §7 给的是 8/27–8/30。实际起点取决于 L1 全量回填何时跑完。
L2 细化时按当时的日期重排,**不挪 2026-09-19 的会期**。
