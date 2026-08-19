# Gold 维表与事实表（L2）

> **Status**: 可执行（2026-08-19 细化，此前为框架） · **Date**: 2026-08-17 / 细化 2026-08-19
>
> **上游需求**: [20260817-etl-implementation.md](20260817-etl-implementation.md)（E3 + E4）
> **前一次上线**: [L1 Silver 全链路跑通](20260817-silver-etl-runnable.md) —— **硬前置**
> **后一次上线**: [L3 评分链与 M1](20260817-scoring-chain-and-m1.md)
> **上线记录**: [20260819-gold-dimensional-build-launch.md](../launch/20260819-gold-dimensional-build-launch.md)
> **相关**: [20260812-gold-bus-matrix.md](20260812-gold-bus-matrix.md) ·
> [ADR 0010](../adr/0010-gold-fact-grain-and-dimension-layering.md)（Gold 粒度与维度分层）
>
> 细化依据：L1 全量已落地（`silver_service_request` 12,474,313 行 / 4,878 个日分区），
> 8 张 Silver 表全部有数据，可以对着真实列写 SQL 了。**不新增也不修改任何 schema
> —— contract 自 2026-08-13 起冻结**；本篇发现的三处口径冲突都在 SQL 与文档里化解，
> 没有一处动 DDL（§12 O9/O10/O11）。

---

## 1. 问题

L1 交付之后，8 张 Silver 表全部有数据，**17 张 Gold 表仍然零行**。
`sql/dml/` 与 `config/seeds/` 自 E0（2026-08-17）建起，至今只有 README。

L2 要填的是其中两类，共 **13 张**：

| 类 | 张数 | 内容 |
|---|---|---|
| 维表 | 9 | `dim_winter_category` · `dim_service_type` · `dim_channel` · `dim_plow_zone` · `dim_admin_label` · `dim_snowfall_event` · `dim_plow_event` · `dim_region_crosswalk` · `dim_recommendation_rules` |
| 描述性 / 直通事实表 | 5 | `fact_plow_shift` · `fact_parking_ban` · `fact_event_zone_rank` · `fact_service_request_zone_event` · `fact_winter_request_daily_by_label` |

剩下 4 张（`fact_request_forecast` · `fact_winter_event_zone_load` ·
`fact_recommendation` + 评分链）在 L3。分界线是**「是不是在算分或训练模型」**：
是的进 L3，不是的进 L2。物理上都叫 Gold 表，但做的事不是一类。

## 2. 为什么单独一次上线

三条，都不是工程量：

1. **回滚粒度不同。** Gold 可整表重建，分钟级；L1 的全量回填跑完就是既成事实。
   混在一次上线里，「能不能回滚」没有统一答案。
2. **最大不确定量是人工活，不是技术活。** `dim_service_type` 要给 3,563 个
   `type` 取值定语义。这批取值的过审进度不该压在 Silver 回填的关键路径上。
3. **Gold 侧一个 DAG 都没有。** ——已在本篇 §4 定案。

## 3. 已定死、L2 不重开的口径

- **执行引擎 = Trino SQL，不是 Spark。** 空间归属在 Silver 就做完了
  （ADR 0009 / L1 §3.3），Gold 侧不需要任何几何函数，剩下的都是 join + 聚合 + 算术。
  再引一层 Spark 会在 7 GB 内存里与 Trino 抢资源。
- **禁 `SELECT *`。** 每张表带 `etl_run_id` · `built_at` · `source_max_ingest_date`
  （ADR 0010 D7）。
- **业务语义落 `config/seeds/*.csv` 与维表，不落库代码**（城市无关护栏 §1）。
- **不改 schema / contract**（2026-08-13 冻结）。
- ✅ **一条被修订**：C6「`INSERT OVERWRITE PARTITION`，覆盖单位一整天的分区」
  在 Gold 侧不成立，改为分层表述，见 §4.3（2026-08-19 签字）。

---

## 4. 执行入口定案（原 O1）

> O1 是「L2 细化的第一件事」，因为它决定 SQL 的参数形状。结论比预想简单。

### 4.1 先决事实：Gold 一张分区表都没有

`sql/ddl/` 的 17 张 Gold 表，`WITH (...)` 里**全部只有 `format` 与
`external_location`，没有一个 `partitioned_by`**。

这与 C6 直接冲突：非分区表上没有 `PARTITION` 可以 OVERWRITE。
`fact_winter_request_daily_by_label.sql` 的头注在建表时就记下了这一点
（「No partitioning/freshness declared here beyond C6's blanket rebuild note」），
当时按待议留下，没有人回来处理。

**C6 是一条 Silver 形状的规则，被整体搬到了 Gold。** 它在 Silver 是对的：
`silver_service_request` 有 4,878 个日分区、12.4 M 行，7 天回溯窗口必须只覆盖
自己那几天。Gold 不是这个形状。

### 4.2 🔴 更要命的一条：Trino 没有 `INSERT OVERWRITE` 语法

`sql/dml/README.md` 现在写着「`INSERT OVERWRITE PARTITION`, never `MERGE`」。
**这条规则照字面写不出能跑的 SQL。** Trino 的 Hive 连接器不提供
`INSERT OVERWRITE` 这个 SQL 语法，覆盖是一个会话属性：

```sql
SET SESSION hive.insert_existing_partitions_behavior = 'OVERWRITE';
```

而它**只作用于分区表的已存在分区**。在 17 张非分区 Gold 表上，
普通 `INSERT INTO` 的行为是**追加**：

- 第一次跑，`dim_plow_zone` 25 行，门禁通过；
- 第二次跑，50 行，PK 唯一性测试才会发现；
- 若门禁只查「>= 25」或只在首次跑，**没有任何东西会报错**。

这是从 Silver 搬规则到 Gold 的直接后果，与 §4.1 是同一个根因的两个表现。

### 4.3 定案

1. **16 张表：`CREATE OR REPLACE TABLE ... AS SELECT`，整表全量重建，不带日期参数。**
   最大的 13,068 行，`dim_service_type` ≤ 3,563 行，其余三位数。全部秒级。
   这与 §2 的「Gold 分钟级可反复重建」是同一件事——**能整表重建，就没有增量的必要**，
   增量只会引入一个没人验证的状态维度。
   `CREATE OR REPLACE TABLE` 需要 Trino ≥ 438；计算节点实测 `SELECT version()`
   = **451**（2026-08-19）。它是原子的，失败时旧表还在，不像 `DROP` + `CREATE`
   中间有一个表不存在的窗口。

2. **`fact_winter_request_daily_by_label`（F8）单独处理。** ≈1.6 M 行、粒度含日期，
   是唯一一张增量有意义的表。本次仍走整表重建（可接受：一次全扫 Silver 冬季子集），
   **不改 DDL 加分区**——schema 冻结，加分区要走变更流程。
   F8 只服务 BO-1 描述性切片，**不在评分链上**，所以它排在最后做，不阻塞任何东西。

3. **C6 在 Gold 的表述需要修订。** 这不是 L2 自行推翻已定项——C6 的原始语境
   （20260814 篇 §7.2）讨论的是 Silver 与增量写入。建议改为：
   > **Silver**：`INSERT OVERWRITE PARTITION`（Spark 侧 `partitionOverwriteMode=dynamic`），
   > 覆盖单位一整天的分区。
   > **Gold**：`CREATE OR REPLACE TABLE ... AS SELECT` 整表全量重建；
   > 粒度含日期且体量足够大的表（当前只有 F8）另议。Trino 无 `INSERT OVERWRITE` 语法。
   ✅ **2026-08-19 签字通过**：按上述分层表述修订，**三处一次改完**
   （CLAUDE.md · 伞篇 `20260817-etl-implementation.md` · `sql/dml/README.md`），
   不留旧说法。执行在 launch 篇阶段 E3。

### 4.4 触发方式

一个 DAG：`dag_gold_build`，`schedule=None`（手动）先行，L3 之后再谈定时。

理由是**上游根本不是日频的**：9 张维表里 4 张来自种子 CSV（改了才需要重建）、
3 张来自 static 参照表（上游是覆盖式全量），只有 `dim_snowfall_event` 与
评分链事实表跟着 Silver 走。给一个上游按周/按季变化的东西配日调度，
只会每天跑一次没有产出的重建。

### 4.5 执行器：复用 `apply_ddl.py` 的连接层

**不写第二套 Trino 连接。** [`scripts/ddl/apply_ddl.py`](../../../scripts/ddl/apply_ddl.py)
已有 `load_trino_settings` / `_connect` / `render_ddl`（`{bucket}` 占位符渲染）
与按层派生 schema 的逻辑。详见 §7。

⚠️ **`CREATE OR REPLACE` 在带 `external_location` 的外部表上行为未实测**：
会不会保留原 location、旧对象是被覆盖还是残留成孤儿文件（会让下次全表扫描
读到两代数据），**必须在 smoke prefix 上先试**，不要在生产表上试。
这是本次上线的**第一个执行步骤**（launch 篇阶段 B），不是收尾时的复核。

### 4.6 顺带解决的两个开放项

- **O7（Silver 分区完整性检查）**：Gold 构建器每次跑之前先核一次
  `silver_service_request` 的分区数与 Bronze 实际覆盖天数之差，缺口就拒绝构建。
  比单独造一个审计 DAG 便宜，且 Gold 本来就是 Silver 唯一的下游消费者。
- **O8（`dag_audit_bronze` 核不到内容）**：两个探针
  （`bronze_duplicate_scan` / `bronze_rowcount_reconcile`）已实现，搬进 DAG 即可。
  **与 Gold 调度是两件事**，不要合并——Bronze 审计是日频的，Gold 构建不是。

---

## 5. 表清单与依赖顺序

顺序是硬的，执行器按这个 DAG 排（§7）：

```
L2-a 种子          L2-b 维表(9)                       L2-c 事实表(5)
config/seeds/*.csv
  winter_category ─> dim_winter_category ─┐
  service_type_keywords ─────────────────┴─> dim_service_type ─┐
  channel ────────> dim_channel                                │
  recommendation_rules ─> dim_recommendation_rules             │
                                                               │
silver_plow_zone_boundary ─┐                                   │
silver_snow_clearing_address┴─> dim_plow_zone ─────────────────┤
silver_service_request ───────> dim_admin_label ───────────────┤
silver_snowfall_event ────────> dim_snowfall_event ────────────┤
silver_plow_shift + silver_parking_ban ─> dim_plow_event ──────┤
dim_plow_zone + dim_admin_label + dim_service_type             │
                          └───> dim_region_crosswalk ──────────┤
                                                               │
                                                               ├─> fact_plow_shift (F3)
                                                               ├─> fact_parking_ban (F4)
                                                               ├─> fact_event_zone_rank (F2)
                                                               ├─> fact_service_request_zone_event (F1)
                                                               └─> fact_winter_request_daily_by_label (F8)
```

三条硬约束：

- `dim_winter_category` 必须最先 —— `dim_service_type` 的 FK 指它。
- `dim_service_type` 必须在 `dim_region_crosswalk` 与 F1/F8 之前 ——
  「哪些工单算冬季」只在这张表里解析，三个下游都要用。
- 事实表必须最后 —— 三张评分链事实表的键要完全一致（ADR 0010 D1）。

---

## 6. 逐表构建规格

统一约定，下面每张表不再重复：

- 文件名 = 表名，`sql/dml/<table>.sql`，一条 `CREATE OR REPLACE TABLE ... AS SELECT`。
- 不写 catalog/schema 限定名（执行器注入），`{bucket}` 占位符由 `render_ddl` 渲染。
- 尾三列一律：`'{etl_run_id}' AS etl_run_id`、`{built_at}` 由执行器以
  `TIMESTAMP '...'` 字面量注入（不用 `now()`——同一次构建的 17 张表要同一个时刻）、
  `source_max_ingest_date` 取该表**真实上游**的最大 `loaded_at` 的日期，
  不是构建日（ADR 0010 D7 的原意是血缘，不是时间戳）。
- 每张表跑完立刻在同一个连接里跑自己的门禁 SQL，**不通过就整批失败**（§7）。

### 6.1 `dim_winter_category`（7 行，种子）

`config/seeds/winter_category.csv`：`winter_category,keyword_pattern,is_effective`。
7 行 = 6 个生效类（SNOW / FROZEN / PLOW / SANDING / WINDROW / ICE_CONTROL）+ PLOUGH
（`is_effective=false`，Winnipeg 命中 0 行，留作可移植性）。

🔴 **`keyword_pattern` 必须是精确的 LIKE 模式，一类一条，不许复合 OR。**
`%ICE%` 会命中 Serv**ice** / Pol**ice** / Not**ice** / Invo**ice**——松匹配实测
10.40%，真值 1.50%。这正是它是种子表而不是 Spark 里一句 `LIKE` 的理由。

装载方式：种子 CSV 不走 Trino 的 `INSERT ... VALUES` 拼串，由执行器读 CSV
生成 `VALUES` 子句喂给 `CREATE OR REPLACE TABLE ... AS SELECT * FROM (VALUES ...)`。
7/15 行的量级，拼 `VALUES` 是最简单且可审计的做法。

**门禁**：`COUNT(*) = 7`；`COUNT(*) WHERE is_effective = true = 6`；PK 唯一。

### 6.2 `dim_service_type`（≤ 3,563 行，Silver × 种子）

来源：`SELECT DISTINCT "type" FROM silver_service_request`
左连接 `config/seeds/service_type_keywords.csv` 的关键词字典。

三个必须写进 SQL 的决定：

1. 🔴 **构建期 anti-join 必须 = 0**，且是**「每个观测到的 `type` 值都有一行」**，
   **不是**「每行的 `winter_category` 都非空」。非冬季 `type` 的 `winter_category`
   为 NULL 是正确的（DDL 该列本就可空）。这两句话混淆会让构建在首次就永远失败。
2. **多关键词命中 first-match-wins**，顺序 SNOW > FROZEN > PLOW > SANDING >
   WINDROW > ICE_CONTROL > PLOUGH。**该规则未验证**（O4）：构建器必须把每个
   多命中的 `type` 值连同命中的类一起打印，人工过一遍再定。
3. `priority_weight` 从 `type` 串里解析 `Pr 2` / `Priority 2` / `P2` / `_vof`
   等后缀变体，P1=3 / P2=2 / P3=1；解析不出的留 NULL（DDL 允许）。
   ⚠️ 解析规则是**城市语义**，落 `config/seeds/`，不落 SQL 里的一串 `regexp_like`。

**门禁**：anti-join = 0；PK 唯一；`priority_weight ∈ {1,2,3} ∪ {NULL}`；
`winter_category` 非空的行数比全表小得多（冬季只占 1.5%，见 6.11）。

### 6.3 `dim_channel`（15 行，种子）

`config/seeds/channel.csv`。`Self Service + Mobile + SMS In → VOF`；
这三个的 `is_comparable_pre_2022 = false`（2022 报告口径迁移，不是行为变化）。

**门禁**：`COUNT(*) = 15`；`COUNT(*) WHERE is_comparable_pre_2022 = false = 3`；
`SELECT DISTINCT channel_raw FROM silver_service_request` 的取值集 ⊆ 本表
（anti-join = 0，同 6.2 的理由）。

### 6.4 `dim_plow_zone`（25 行）

来源：`silver_plow_zone_boundary` 按 `plow_zone` 聚合 + `silver_snow_clearing_address`
带入 `address_count`。

- `geometry_wkt` 是同一 `plow_zone` 下多个多边形的**几何集合**（内部边界保留），
  **不是 `ST_Union`**（ADR 0010 D6）。Trino 侧用 `ST_GeometryCollection` 语义拼；
  82 个 MultiPolygon → 25 行。
- `has_plow_schedule = false` 恰好 3 个：`B/D` · `X` · `Downtown`（约 31% 面积）。
  判据是「在 `silver_plow_shift` 里有没有出现」，**不是硬编码这三个名字**——
  硬编码就把城市语义写进了 SQL，且明年多一个分区没人会发现。
- ⚠️ `address_count` 取 `silver_snow_clearing_address` 的 `MAX(snapshot_date)`。
  但该 Silver 表**实现为扁平全量覆写，一次只存一个采集日**（CLAUDE.md 已记）。
  所以实际是「全表」，但 SQL 仍写 `MAX(snapshot_date)` 过滤，并**断言
  `COUNT(DISTINCT snapshot_date) = 1`**：哪天它真变成时间序列，这条断言会先响，
  而不是 `address_count` 悄悄翻倍。
- `geometry_repaired` / `area_delta_pct` 直通 Silver（同一 `plow_zone` 下任一
  多边形被修过即 true）。

**门禁**：`COUNT(*) = 25`；`geometry_repaired = true` **= 8**；
`has_plow_schedule = false` **= 3**；`address_count IS NULL` = 0
（三个无排班分区的数已知：B/D 11,150 · X 2,590 · Downtown 574）。

### 6.5 `dim_admin_label`（15 + 237 = 252 行）

来源：`silver_service_request` 的 `ward_raw` / `neighbourhood_raw`。

🔴 **`neighbourhood` 先 casefold 再去重**：原始 242 个取值含 5 对
大小写碰撞（`Daniel Mcintyre` / `Daniel McIntyre`），不折叠会把 McMillan
拆成两个报告单元。折叠后 237。

✅ **`label_id` 存 casefold 值**（O10，2026-08-19 签字）：join 键必须是规范形，
折叠值最不容易出岔子。显示形态在 Superset 侧用 `INITCAP` 或最高频原形处理，
**不在 Gold 里存第二份可读形态**——那等于把同一个概念存两遍，迟早不一致。

**门禁**：`label_type='ward'` = 15；`label_type='neighbourhood'` = **237**；
PK 唯一；`label_type ∈ {ward, neighbourhood}`。

### 6.6 `dim_snowfall_event`（99 行）—— 🔴 有口径冲突

直通 `silver_snowfall_event` + 派生 `severity_score` / `snow_season` /
`is_scheduling_era`。

🔴 **Silver 有 159 行，Gold 契约冻结在 99。** 两者不是矛盾，是口径不同：
Silver 按 Bronze 实际最早日（2000-01-01）、全年每一天切事件；
99 是探针口径（`scripts/analysis/snowfall_events.py`：`FIRST_WINTER = 2008`，
只取 11-01 → 次年 05-01）。逐项对账已闭合：159 − 52（2000–2007）− 1 − 7 = 99。

所以 `dim_snowfall_event.sql` **必须显式带这个过滤**：

```sql
WHERE start_date >= DATE '2008-11-01'
  AND MONTH(start_date) IN (11, 12, 1, 2, 3, 4)
```

**这个过滤不加就没有任何东西会报错**：表变 159 行，F1 面板从 13,068 变成
22 × 159 × 6 = 20,988，一路长进 M1 的训练集。门禁必须查精确值。

⚠️ 代价要说清（O9）：被过滤掉的 60 个事件里有真实降雪（最大一个
2019-10-10 → 10-20，11 天 41.16 cm），而 F1 存在的理由之一正是「pre-era 事件
喂 M1 长周期训练」。**用 2008 年 11 月这条线切掉 2000–2007，是为了对齐一个
探针的默认常量，不是因为那八年数据不可用。** 本次按 99 执行（契约冻结、
13,068 已是多处门禁的硬数字），但这条口径**建议在 L3 M1 训练前重新签一次字**——
那时才知道 M1 是否吃得下更长的历史。

派生列：`severity_score` = 降雪量与低温的归一化复合，值域 [0,1]，
`min_temperature_c` 可空时按 C16 的 coalesce 规则处理；
`snow_season` = `'YYYY-YYYY'`；`is_scheduling_era` = `start_date >= 2015-12-01`。

**门禁**：`COUNT(*) = 99`；`is_scheduling_era = true` **= 59**；
`severity_score` ∈ [0,1] 且非空；PK 唯一；`event_rule_version` 单值
`v1-3cm-or-10d10cm`。

### 6.7 `dim_plow_event`（19 行）

来源：`silver_plow_shift.snow_ban_id` 的 distinct 值（一次全市作业 = 一个 ban），
`first_shift_start_utc = MIN(shift_start_utc)`，与 `silver_parking_ban` 取 `ban_id`。

`matched_snowfall_event_id`：与 `dim_snowfall_event` 按时间对齐，
**17/19 对齐、2 个为 NULL**（2021-01-07 / 2026-02-26）。
⚠️ 对齐用的 lag 窗口必须写进 SQL 注释并与 BO-3 探针一致——
CLAUDE.md 记过一次教训：`--align-lag-days 3` 与 7d 会给出不同的名单，
台账里的四个日期就是这样错过一次。本篇按 **lag 7d** 的结论（4 次未对齐里
2 次落在 19 个 plow_event 之外）。

**门禁**：`COUNT(*) = 19`；`is_aligned = true` **= 17**、`false` **= 2**；
`COUNT(DISTINCT matched_snowfall_event_id) = COUNT(*) WHERE ... IS NOT NULL`
（**扇出守卫**，B1：两个 plow_event 塌进同一个 snowfall_event 会让 L3 的 F6 join 翻倍）。

### 6.8 `dim_region_crosswalk`（zone × label 加权）

方向固定 `zone → label`。`weight` = 该分区的冬季工单落在该 label 上的份额，
`SUM(weight) GROUP BY (plow_zone, label_type) = 1`。

依赖 `dim_service_type`（判定冬季）与 `dim_admin_label`（规范化 label）。

🔴 **`calibration_window` 取近期窗口，不是十年均值**（O2）：前后半期 ρ = +0.591，
V/M 两个分区移动超过一整个班次。建议**最近 3 个雪季**，窗口值写进
`calibration_window` 列（如 `'2022-2023..2024-2025'`），**不藏在 SQL 字面量里**——
「这个数来自哪个窗口」是这个数的一部分。

`is_dominant` 是显式列，不许下游用 `ORDER BY weight LIMIT 1` 重算（ADR 0010 D4）。
并列裁决规则（O3）：`weight` 相等时取 `label_id` 字典序最小，**且构建器打印所有
并列组**——静默裁决和没有裁决一样危险。

**门禁**：`SUM(weight)` 按 `(plow_zone, label_type)` 分组全部 ≈ 1.0（±1e-9）；
每组 `is_dominant = true` 恰好 1 行；PK 唯一；两个 FK 的 anti-join = 0。

### 6.9 `dim_recommendation_rules`（种子）

`config/seeds/recommendation_rules.csv`。文字模板 + 降级兜底。
**不得称之为 AI**——代码、注释、文档、讲稿里都不行。L3 才消费它，L2 只负责装载。

**门禁**：PK 唯一；`is_fallback = true` 至少 1 行（没有兜底的规则集在 L3 会静默塌成空文本）。

### 6.10 五张事实表

| 表 | 行数 | 构建要点 |
|---|---|---|
| `fact_plow_shift`（F3） | **418** | 直通 `silver_plow_shift` + FK 到 `dim_plow_event`。418 = 19 事件 × 22 分区，与 F2 同数是巧合的必然：一个事件里每个分区恰好一个班次 |
| `fact_parking_ban`（F4） | **49** | 直通 `silver_parking_ban`，与 F3 **左连接**取 `matched_plow_event_id`。**30 条为 NULL 是语义**（有禁停但没有全市作业），永远不许 inner join |
| `fact_event_zone_rank`（F2） | **418** | `rank_factor = shift_number / 5.0`，**固定分母 5**，不是对观测值 min-max。值域 [0.2, 1]，`= 0` 结构上不可达 |
| `fact_service_request_zone_event`（F1） | **13,068** | 满面板 = 22 分区 × 99 事件 × 6 生效类。**零请求作为显式训练信号写入**，不是只存非零。用 `CROSS JOIN` 生成骨架再左连接计数，`COALESCE(cnt, 0)` |
| `fact_winter_request_daily_by_label`（F8） | ⚠️ **≈1.6 M 待重估（O14）** | 只装冬季工单，粒度 `(date, label_type, label_id)`。**不与评分链共用任何列**。最后做 |

三点展开：

**F2 与 O2 无关。** `rank_factor` 是每个 plow_event 的**实测** `shift_number`，
不是跨事件均值——顺位漂移（O2）影响的是 `dim_region_crosswalk` 的标定窗口
与 L3 的权重标定，**不影响 F2**。这条澄清把 O2 的阻塞面从 5 张表缩到 1 张。

**F1 的 22 不是 25。** `plow_zone` 只取 `has_plow_schedule = true` 的 22 个，
但**不是物理排除**那三个分区——它们在 `dim_plow_zone` 里在场、可被过滤。

**F1 的 99 跟着 6.6 的口径走。** 6.6 的过滤改了，13,068 就得改，
两处必须同源——执行器把 `dim_snowfall_event` 作为 F1 的唯一事件来源
（`JOIN dim_snowfall_event`，不重新查 Silver），这样口径只有一处。

### 6.11 L1 移交的两条判据，在这里核

| 判据 | 值 | 为什么现在才能核 |
|---|---|---|
| 冬季子集行数 | **≈ 275,282**，即 12,474,313 的 **≈1.5%** | 按 `type` 匹配冬季关键词，而关键词只在 `dim_service_type` 里解析。分母用 Silver 实测的 12,474,313，**不是上游的 18.4 M**（两者口径不同） |
| 空间命中率精确复现 | **134,123 / 134,258 = 99.9%** | 分母是「排班期 × 冬季 × 带几何」的工单，两个筛选条件 Silver 都没有。L1 只能验全表口径 2,841,151 / 2,842,219 = 99.962%（同量级略高，符合预期） |

🔴 对不上时**信探针**（`scripts.analysis.request_point_in_zone`）——
管道里有一步和探针口径不一致。

---

## 7. 执行器 `scripts/gold/build_gold.py`

新增一个模块，**不写第二套 Trino 连接**：

```python
from scripts.ddl.apply_ddl import load_trino_settings, schema_name, render_ddl, _connect
```

职责，按顺序：

1. **前置核验**（§4.6 O7）：`silver_service_request` 的分区数 vs Bronze 实际覆盖
   天数；差 > 0 直接拒绝构建并打印缺哪几天。Gold 是 Silver 唯一的下游，
   在这里查比造一个审计 DAG 便宜。
2. **种子装载**：读 `config/seeds/*.csv`，生成 `VALUES` 子句。
   CSV 的列顺序必须与 DDL 一致，不一致就报错——不做按名匹配的「宽容」处理，
   那会让一次列错位变成一次静默的语义交换。
3. **按 §5 的依赖图顺序**执行 `sql/dml/*.sql`。顺序**写死在代码里的一张表**，
   不靠文件名排序（`dim_service_type` 字典序在 `dim_winter_category` 之后是巧合，
   `dim_admin_label` 就不是）。
4. **每张表跑完立刻跑它自己的门禁**，取自 DDL 头注的 `-- relationships:` 段
   （`ddl_parser.py` 已经在解析这些注释）——**判据不写第二份**。
   任一条不过：整批失败，**已建的表不回滚**（`CREATE OR REPLACE` 是原子的，
   坏的那张表还是旧内容或建不出来，下游 join 会立刻失败，不会静默出错数）。
5. `--only <table>` / `--dry-run`（只打印 SQL 不执行）/ `--location-prefix`
   （复用 smoke 命名空间，与 `apply_ddl.py` 同一套）。

`etl_run_id` = `f"{plan}-{utcnow:%Y%m%dT%H%M%SZ}"`，一次构建全表共用；
`built_at` 同理由执行器注入字面量，**不用 SQL 的 `now()`**。

Makefile 加 `make gold-build [ONLY=…] [DRY_RUN=1]`。

## 8. 调度 `dag_gold_build`

- `schedule=None`，`max_active_runs=1`，Params：`bucket` · `only`（可选）。
- 任务用 `PythonOperator` 调 `build_gold`，与 `sync_partition_metadata` 同一形态，
  **不引入新的 Trino operator 依赖**。
- 🔴 **不写 `on_failure_callback`** —— `DEFAULT_ARGS` 已挂 `alert_on_failure`，
  局部写会把它覆盖掉。
- 任务组照 §5 三段：`seeds → dims → facts`，段内可并发（Trino 侧是串行 SQL，
  并发只在依赖允许时有意义，首版**全串行**，13 张表秒级，并发是纯风险）。

## 9. 测试策略

离线单测（`tests/unit/test_build_gold.py`），**不连 Trino**：

- 依赖顺序表：断言每张表的依赖都排在它前面（拓扑序自检）。
- 种子 CSV 与 DDL 列的一致性：列名、顺序、行数期望。
- `VALUES` 生成器的类型渲染（复用 `apply_ddl.literal_for` 的思路，
  VARCHAR 里的单引号必须转义——种子里迟早会有 `O'Connor` 这样的邻里名）。
- 门禁 SQL 提取：从每个 DDL 头注解析出的 `relationships` 条数 > 0。
- `sql/dml/*.sql` 静态检查：**不含 `SELECT *`**、不含硬编码 catalog/schema、
  含 `CREATE OR REPLACE TABLE`、含三列血缘。

`make lint` 的 sqlfluff 覆盖 `sql/dml/`（dialect trino 已在 `.sqlfluff` 钉死）。

## 10. 被否决的选项

| 选项 | 否决理由 |
|---|---|
| Gold 也用 Spark 写 | 空间归属已在 Silver 完成，Gold 不需要几何函数；再引一层 Spark 会在 7 GB 内存里与 Trino 抢资源 |
| Gold 用 `INSERT OVERWRITE PARTITION` | Trino 无此语法，且 17 张表无一分区表，普通 `INSERT` 是追加、跑两次静默翻倍（§4.2） |
| 给 Gold 表加 `partitioned_by` 以贴合 C6 | 16/17 张表的粒度里没有日期，造一个日分区列是为满足规则而伪造维度。F8 那张要加得走变更流程（schema 冻结） |
| `DROP TABLE` + `CREATE TABLE` + `INSERT` | 中间有一个「表不存在」的窗口；`CREATE OR REPLACE` 是原子的 |
| `dim_snowfall_event` 直取 Silver 全部 159 行 | 契约冻结在 99，F1 的 13,068 与多处门禁都跟着它。要改得连带改 M1 训练集，走签字（O9） |
| `has_plow_schedule` 硬编码 `B/D` / `X` / `Downtown` | 城市语义进 SQL；且新增一个无排班分区时没人会发现。按「在排班表里出现与否」派生 |
| 维表增量更新 | 最大 3,563 行，整表重建秒级。增量只引入一个没人验证的状态维度 |
| 种子数据用 JSON | 要人工逐行审、要 diff 可读。CSV（`config/seeds/README.md` 已定） |

## 11. 验收判据

按 §6 逐表的门禁全绿，外加整体收口这几条：

```sql
-- 维表行数
SELECT COUNT(*) FROM dim_winter_category;   -- 7   (is_effective=true → 6)
SELECT COUNT(*) FROM dim_channel;           -- 15  (is_comparable_pre_2022=false → 3)
SELECT COUNT(*) FROM dim_plow_zone;         -- 25  (repaired=8, no_schedule=3)
SELECT COUNT(*) FROM dim_admin_label;       -- 252 (ward 15 + neighbourhood 237)
SELECT COUNT(*) FROM dim_snowfall_event;    -- 99  (is_scheduling_era=true → 59)
SELECT COUNT(*) FROM dim_plow_event;        -- 19  (is_aligned true 17 / false 2)

-- 事实表行数
SELECT COUNT(*) FROM fact_plow_shift;               -- 418
SELECT COUNT(*) FROM fact_parking_ban;              -- 49，其中 30 条 matched_plow_event_id IS NULL
SELECT COUNT(*) FROM fact_event_zone_rank;          -- 418
SELECT COUNT(*) FROM fact_service_request_zone_event; -- 13068

-- 顺位缺失表示为 NULL，不是 0
SELECT COUNT(*) FROM fact_event_zone_rank WHERE rank_factor = 0;  -- 0

-- 满面板（M1 的输入）
SELECT COUNT(DISTINCT (snowfall_event_id, plow_zone))
FROM fact_service_request_zone_event;  -- 2178
-- 排班期子集 1298 格，其中非零 916 格（70.57%）

-- 幂等：连跑两次，行数不变（这条查的是 §4.2 的坑）
```

```bash
# 行政单元不进评分链 fact 键
grep -l "region_type" sql/ddl/fact_*.sql   # 只允许 fact_winter_request_daily_by_label

make lint && make test-unit
make gold-build && make gold-build   # 跑两次，第二次行数必须完全相同
```

三个探针数字（硬门禁）：面板 **1,298** 格 · 非零 **916** 格 · 空间命中
**134,123 / 134,258**。对不上时信探针。

## 12. 开放项

| # | 未定的事 | 建议 | 时点 |
|---|---|---|---|
| **O1** | Gold 调度入口 | ✅ **已定案，见 §4** | — |
| **O2** | 🔴 顺位不是常量（前后半期 ρ = +0.591） | `dim_region_crosswalk.calibration_window` 取最近 3 个雪季，窗口值写进列。**F2 不受影响**（§6.10） | `dim_region_crosswalk` 之前 |
| **O3** | `is_dominant` 并列裁决规则（C10/C11） | `weight` 相等取 `label_id` 字典序最小，且**打印所有并列组** | L2 内 |
| **O4** | `dim_service_type` 多关键词命中仲裁未验证 | 构建器打印全部多命中值，人工过一遍再定 | 种子段 |
| **O5** | BO-3 事件定义的滚动累积判据（`accum_flag` 已落地） | 按当前 v1 规则执行（159/99/59）；N 再变则行数判据同步改，**不改 schema** | 事实表之前确认 N 冻结 |
| **O6** | 「后排分区户数更多」`r = +0.491` 须在近期窗口重算 | 跟 O2 同一个窗口一起做 | L2 内 |
| **O7** | Silver 分区完整性检查 | ✅ 收进 Gold 构建器的前置核验（§4.6 / §7 步骤 1） | — |
| **O8** | `dag_audit_bronze` 核不到内容 | 两个探针已实现，搬进 DAG。**与 Gold 调度分开**（日频 vs 手动） | L2 内，单独 PR |
| **O9** | 🔴 **`dim_snowfall_event` 取 99 还是 159**（§6.6） | 本次按 **99**（契约冻结 + 13,068 连锁）。但 2008 这条线是探针默认常量，不是数据边界；**L3 M1 训练前重新签一次字** | 已按 99 执行，L3 前复议 |
| **O10** | `dim_admin_label.label_id` 的形态 | ✅ **已定案 2026-08-19：存 casefold 值**，显示形态在 Superset 侧处理（§6.5） | — |
| **O11** | C6 在 Gold 的表述 | ✅ **已定案 2026-08-19：分层表述**，三处同步改（§4.3） | 执行在 launch 阶段 E3 |
| **O12** | ⚠️ **`CREATE OR REPLACE` 在外部表上的孤儿文件行为未实测** | launch 篇阶段 B 在 smoke prefix 上先试，**不在生产表上试** | 写任何 DML 之前 |
| **O13** | 🔴 **Trino 全表扫 Silver 会超时。** 2026-08-19 实测：读真实列跨全部 4,878 个分区 → `Unable to execute HTTP request: Read timed out`（Trino 的 S3 客户端读 MinIO 超时，非 CLI、非 coordinator、非查询时限）。单年 365 分区 / 777,833 行秒级返回——**墙在分区数，不在数据量或吞吐** | 读 Silver 的 SQL 一律带 `open_date_local` 谓词；真需全历史的（只有 F8）走分片执行 + staging 表一次性 swap。四条规则已落 [`.claude/rules/gold-sql.md`](../../../.claude/rules/gold-sql.md)，`CLAUDE.md` 已 `@` 导入。Trino 是平台级共享服务（ADR 0006 §9），**不调它的连接参数** | 写第一条 DML 前生效 |
| **O14** | ⚠️ **F8 的 ≈1.6 M 行是稠密假设，实测不成立。** 该数按「6,600 天 × 252 标签」推得，但 2026-08-19 实测 `ward_raw` / `neighbourhood_raw` 的 NULL 率均为 **77.22%**，且与无坐标率 **77.21%** 几乎完全重合——**没坐标的行同时也没有行政区文本，不是两处独立缺失**。底层只有约 23% 的行带得动标签 | 建 F8 之前按实测标签覆盖率重算行数期望。**不改 schema**，只改门禁数字 | F8 之前 |

## 13. 时间盒

L1 全量已于 2026-08-18 落地，L2 从 8/19 起。伞篇原给 8/27–8/30，可以提前。

| 窗口 | 内容 |
|---|---|
| 8/19 | O12 实测（smoke prefix 上验 `CREATE OR REPLACE` + 外部表）。O10/O11 已签字 |
| 8/20 | 执行器 `build_gold.py` + 单测 + 四份种子 CSV |
| 8/21–8/22 | 9 张维表 DML + 门禁。`dim_service_type` 的人工过审是这两天的最大不确定量 |
| 8/23 | 4 张事实表（F3/F4/F2/F1）+ 三个探针数字复现 |
| 8/24 | F8（≈1.6 M 行，唯一需要看耗时的一张）+ DAG + 收口 |
| 8/25– | 缓冲，或提前进 L3。**不挪 2026-09-19 的会期** |

关键路径 = **O12 实测 → `dim_service_type` 过审 → F1 的 13,068**。
