# ADR 0010 — Gold 层的事实粒度与维度分层

> **Status**: **Accepted** · **Date**: 2026-08-09 · **Accepted**: 2026-08-12
> **Relates to**: [ADR 0009](0009-plow-zone-as-the-unit-of-analysis.md)（分析单元）·
> [ADR 0008](0008-plow-schedule-is-a-plan-not-a-record.md)（供给侧口径）·
> [ADR 0004](0004-silver-cleansing-methodology.md)（清洗方法论）
> **落地方案**: [design/20260809-gold-silver-schema-derivation.md](../design/20260809-gold-silver-schema-derivation.md)

> ✅ **本篇已定稿。** 七条决策与 §5 的五个待确认项已于 2026-08-12 逐条过完，
> 无异议接受，状态改为 `Accepted`，正文自此冻结；后续偏差记进 `docs/dev/launch/`。

---

## 0. 决策速览

| # | 决策 | 状态 |
|---|---|---|
| **D1** | 唯一事实粒度 = `plow_zone × snowfall_event` | ✅ 已定 |
| **D2** | 行政单元（ward / neighbourhood）不进任何事实表的键 | ✅ 已定 |
| **D3** | 维度表按「有无真几何」拆成 `dim_plow_zone` 与 `dim_admin_label` | ✅ 已定 |
| **D4** | crosswalk 方向固定为 `zone → label`，带权重与主导份额 | ✅ 已定 |
| **D5** | 模型输出单独落表，评分表引用 `model_version` | ✅ 已定 |
| **D6** | 分区几何在 Gold 存为多边形**集合**，不做 dissolve，不引 Sedona | ✅ 已定 |
| **D7** | 每张 Gold 表带统一审计列 | ✅ 已定 |

---

## 1. 背景

ADR 0009 定了**分析单元**是 `plow_zone`，但没有定**表长什么样**。这两件事之间
还隔着一层：分析单元决定了事实表的键，却没有决定维度怎么分层、模型输出放哪、
几何以什么形态存。这一层此前只存在于一篇 2026-08-03 的讨论记录里，而那篇写在
ADR 0008 / 0009 之前，**它假设的 `ward × event` 粒度已经作废**。

现在必须定，原因是时间顺序：`sql/ddl/` 尚不存在，第一个写下的 DDL 就会锁死
后面所有 DML 与回填。而 311 的 Silver 是 16 GB 量级，**回填之后再改列意味着重跑**。

> ADR 0009 §4.4 已经拒绝过一次"等 Silver 建好再看"：`fact_*` 的粒度、聚簇键、
> `dim_geography` 的连接方向全都由分析单元决定，先建后定等于建完重建。
> 本篇是那句话的下半段——**分析单元定了，表结构就必须跟着定完，不能停在一半**。

---

## 2. 决策

### D1 · 唯一事实粒度 = `plow_zone × snowfall_event` ✅

**所有进入评分链的事实表都用这一个粒度**，不设第二种。

| | 值 | 来源 |
|---|---|---|
| 面板规模 | 22 分区 × 59 排班期事件 = **1,298 格** | ADR 0009 §2.1 |
| 非零率 | **70.57%**（916/1,298） | 可行性台账 2026-08-09 |
| 分区数为何是 22 而非 25 | `B/D` / `X` / `Downtown` 无排班，`rank_factor` 恒 NULL | ADR 0008 §3 |
| 事件数为何是 59 而非 99 | 顺位只在排班期（2015-12 起）有定义 | BO-6 §有效分析窗口 |

**判据**：`fact_service_request_zone_event`、`fact_winter_event_zone_load`、
`fact_recommendation` 三张表的行数上界都是 1,298 × 类别数。任何一张表跑出
第四种粒度，都说明有一步偷偷做了聚合或分摊。

> ⚠️ **这不等于"每张表都必须有 1,298 行"**。`fact_winter_event_zone_load`
> 是满面板（缺失以 `score_status` 表达，不删行）；
> `fact_service_request_zone_event` **也是满面板**（见 §5 O1 的定论——M1
> 训练需要显式的零，只存非零会让"零请求"系统性地从训练面板里消失）。
> **满不满是各表自己的事，键必须一样。**

### D2 · 行政单元不进任何事实表的键 ✅

`ward` / `neighbourhood` 只能出现在两个地方：

1. `dim_admin_label`（标签维度本身）；
2. `dim_region_crosswalk`（分区 → 标签的带权映射）。

**唯一例外**：`fact_service_request_daily_by_label` —— 纯描述性统计表
（各 ward 冬季工单量、逐年趋势），ADR 0009 §2.2 明确允许。它**单独一张表、
不与评分链共用任何列**，因为共用会诱导 `union` 后 `sum`。

> **为什么要把这条写成 schema 约束而不是 code review 纪律**：
> ADR 0009 的结论是"标签可以贴，数不能搬"，但那是一句话，靠人记。
> 让 fact 表的键里根本没有 `region_type` 这一列，"搬"这个动作就写不出来。
> 长表 `(region_type, region_id)` 的形状本身就是在邀请 `GROUP BY region_type`。

### D3 · 维度表按「有无真几何」拆两张 ✅

| 表 | 粒度 | 有几何 |
|---|---|---|
| `dim_plow_zone` | 25 个 `plow_zone` | ✅ `geometry_wkt` |
| `dim_admin_label` | 15 ward + 237 neighbourhood | ❌ 无，且**不留空列** |

原设想的 `dim_geography` 是一张长表，用 `region_type` 区分三种归属，
`ward` / `neighbourhood` 行的 `geometry_wkt` 恒为 NULL。

**拆开的理由是这个 NULL 不是缺数据，是类型不同**：ward 的几何在开放数据门户上
根本取不到（`tz8z-hyaz` / `au4g-xjwh` / `mjit-gete` 三套边界 SODA 与 geospatial
export 都返回 `not_found`，ADR 0009 §4.3）。一张表里放两种本体，
下游每次都要先判断 `region_type` 才知道哪些列有意义——这正是长表最贵的地方。

`dim_plow_zone` 必带的四列（理由见 D6 与 §3）：

```
plow_zone            -- PK，25 个取值
geometry_wkt         -- ST_MakeValid 之后
has_plow_schedule    -- B/D · X · Downtown = false
address_count        -- BO-6 的归一化分母
address_count_snapshot_date   -- ⚠️ 见下
geometry_repaired    -- bool，8/25 为 true
area_delta_pct       -- 修复前后面积差
```

> 🔴 **`address_count_snapshot_date` 不是可选的。** 地址数取自 `g3p4-h83y` 的
> **当期快照**，却被用来归一化 2015 年起的历史事件；城市在扩张，这是一条
> BO-2 与 BO-6 都要求**主动声明**的局限。声明写成一个列，比写在 slide 上可靠——
> 列会跟着数据走，slide 不会。

### D4 · crosswalk 方向固定为 `zone → label` ✅

```
dim_region_crosswalk(plow_zone, label_type, label_id, weight, is_dominant, calibration_window)
```

- `weight` = 该分区的工单落在该标签上的份额，**∑label = 1**；
- `is_dominant` 显式落列，不靠下游 `ORDER BY weight LIMIT 1` 现算；
- `calibration_window` 记标定窗口——权重会随年份漂移。

**只保留一个方向。** 原设计是双向的 `(from_type, from_id, to_type, to_id)`，
而反方向（`ward → zone`）的唯一用途是把 ward 级的数换算到分区——那正是
ADR 0009 否决掉的动作。**留着反方向等于留一个合法出口。**

实测支撑：ward → zone 主导份额仅 **34.1%**，zone → ward 为 **45.4%**。
两个方向都不是 1:1，但只有 `zone → label` 这个方向是"给已算好的结果贴标签"，
不产生分派误差。

> ⚠️ **下游任何单值查表都是缺陷**，code review 按缺陷处理（ADR 0009 §2.3）。

### D5 · 模型输出单独落表 ✅

```
fact_request_forecast(snowfall_event_id, plow_zone, model_version, predicted_count, actual_count, ...)
fact_winter_event_zone_load(... , forecast_model_version)   -- 外键引用，不内联预测值
```

原设计让评分表直接吃 M1 的预测值。**后果是模型换一版就无法回溯上一版的评分**，
BO-8 要求的"对历史降雪事件做回测"也无从复算。

同理，`fact_recommendation` 必须带 **`rank_model` / `rank_baseline` / `rank_delta`**：
BO-8 的验收标准原文是「排序结果优于**按历史平均请求量排序**这一基线」——
基线不与模型排序存在同一行里，每次汇报都得重算，且没有第三方能验证。

> 这一条的成本只是两张薄表和三个列，收益是**评估协议（§4.4）落进了数据里**。
> BO 文档写着「没有基线的模型结论不予采信」，那么基线就该是一个列，不是一次口头计算。

### D6 · 几何存为多边形集合，不做 dissolve，不引 Sedona ✅

| 层 | 存什么 |
|---|---|
| `silver_plow_zone_boundary` | **82 行**原始多边形，每行一个 `POLYGON` + `plow_zone` 属性 |
| `dim_plow_zone` | **25 行**，`geometry_wkt` 为同一 zone 的多边形拼成的 `MULTIPOLYGON` |

关键在于 `MULTIPOLYGON` 是**集合**而不是 `ST_Union` 的结果：内部边界仍然存在，
只是被装进了一个几何对象里。

**为什么这样够用**：`ST_Contains` 对集合与 dissolve 结果的判定完全一致，
渲染也一致，差别只在内部那条边界还在不在——而**没有任何一个 BO 需要它消失**。
点在多边形内的判定本来就是逐多边形做的（已跑通，命中率 99.9%）。

**为什么值得单独写一条**：真做 dissolve 需要几何合并算子，要么引 Apache Sedona，
要么自写。而 25 个分区里 **8 个**含 OGC 非法几何（`A` · `B` · `B/D` · `D` ·
`Downtown` · `E` · `R` · `S`），union 会直接抛
`unable to assign free hole to a shell`。**选集合形态就同时省掉一个依赖和一类报错。**

> `ST_MakeValid` 仍然要做，但目的收窄为"让每个多边形自身合法"，
> 而不是"让 union 能成功"。修复必须发生在 Silver，并落 `geometry_repaired`
> 与 `area_delta_pct`——BO-4 那个 99.9% 命中率就是在修复后的几何上测的，
> 不记录等于结论不可复现。

### D7 · Gold 统一审计列 ✅

每张 Gold 表（维度与事实一律）带三列：

```
etl_run_id              -- 哪一次运行写的
built_at                -- 写入时刻（UTC）
source_max_ingest_date  -- 吃到的 Bronze 最新采集日
```

Silver 已有 `source_id / ingest_date / loaded_at`，Gold 一列都没有。
出问题时第一个要回答的永远是「这张表是哪次跑的、吃的是哪天的 Bronze」——
没有这三列，回答方式只能是翻 Airflow 日志。

---

## 3. 后果

### 3.1 Silver 一列都不用改

D1–D7 全部作用在 Gold。2026-08-03 反推出的那份 `silver_service_request` 列清单
（行级保真 + 业务语义后移）**不受影响**——这本身是对当初那个设计的验证：
分析单元换过一次、表结构又改了一轮，Silver 依然不用重来。

唯一的新增是 `silver_plow_zone_boundary` 要落 `geometry_repaired` / `area_delta_pct`
两列（D6）。

### 3.2 首批 DDL 的范围随之确定

`sql/ddl/` 尚不存在，本篇定了第一批文件是哪些表、每张的键是什么。
`.sqlfluff` 已把 dialect 钉死 trino，第一个写下的文件就会被正确 lint。

### 3.3 三个可执行的复现判据

这三个数是探针在**公开 API** 上算出来的，管道跑出来必须对得上：

| 判据 | 值 | 探针 |
|---|---|---|
| 面板格数 | 1,298 | `scripts.analysis.score_collinearity` |
| 面板非零率 | 70.57% | `scripts.analysis.snowfall_events --zone-panel` |
| 工单空间命中率 | 99.9% | `scripts.analysis.request_point_in_zone` |

> 对不上时**信探针**——BO 文档的所有结论都建在它们上面。

### 3.4 城市无关护栏的适用范围

`plow_zone` / `ward` / `neighbourhood` 是城市实例名，按护栏 §2 它们**允许**出现在
`sql/` 与 `dim_*` 表里（那本来就是每城一套）。但**不得**出现在
`spark/transforms/` 的通用 transform 里——空间归属那一步按角色名写
（"作业分区"由配置提供边界源），不写死 `plow_zone`。

---

## 4. 被否决的方案

### 4.1 长表 `dim_geography(region_type, region_id)` 承载三种归属

**否决理由：形状本身在邀请错误用法。**
长表让 `GROUP BY region_type` 写起来毫不费力，而那正是 ADR 0009 禁止的动作。
再加上 ward / neighbourhood 的 `geometry_wkt` 恒 NULL——不是缺数据，是取不到
（三套边界全部 `not_found`）——一张表里放着两种本体，下游每次都要先判类型。

（这是 2026-08-03 讨论记录的原设计，当时的语境里 ward 还是建模单元。）

### 4.2 dissolve 成 25 个真正的 MultiPolygon（引 Sedona 或自写）

**否决理由：为一个没有需求的性质付两笔成本。**
内部边界消失这件事没有任何 BO 需要，而代价是一个新依赖 + 绕开 8 个非法几何的
union 报错。若将来有了真需求（例如要算分区周长），再单独提。

### 4.3 M1 的预测值直接内联进评分表

**否决理由：把评估协议踢出了数据。**
BO 文档 §4.4 要求"任何模型指标必须与其基线成对出现"，内联之后基线只存在于
某次运行的日志里。多一张薄表换回测可复算，是明显划算的交易。

### 4.4 现在就给 `fact_service_request`（工单粒度，BO-5/M2）占位

**否决理由：目标变量的语义还没验。**
`closed_date` 到底是"工单关闭"还是"实际清雪完成"至今未确认，可行性台账把它
标为 H1 未纳入。BO-5 是 P1。建一张目标变量语义未定的表，是在 H1 里替 H2 背债。

### 4.5 等 Silver 建好、用 SQL 探完再定 schema

**否决理由：ADR 0009 §4.4 已经否决过一次，理由未变。**
16 GB 的 311 回填一次不是几分钟的事，先建后定等于建完重建。

---

## 5. 待确认（2026-08-12 已逐条定稿）

| # | 问题 | 结论 |
|---|---|---|
| Q1 | D6 是不是该单独拆成一篇 ADR？它是**选型**（引不引 Sedona），与 D1–D5 的**建模**不同类 | **不拆，留在本篇。** 它的否决理由完全依赖 D3 的几何存放位置，拆开要重复一遍背景。若 H2 出现真的需要 dissolve 的需求（如分区周长），另开新 ADR，不回来改本条 |
| Q2 | `dim_service_type`（3,563 个 `type`）与 `dim_channel` 是种子表还是从 Bronze 派生 | **种子表，进 `config/`。** 护栏 §1 明令业务语义不进代码；谁维护、怎么保证不漏新取值留到 S3（对应 design §8 O2），不阻塞本篇定稿 |
| Q3 | `fact_service_request_zone_event` 存不存零格 | **满面板，不只存非零。** M1 是回归/排序问题，零请求的 (event, zone) 组合本身是有效信号；只存 916 行非零格会让下游一旦忘记 outer join 补零就训出系统性偏差，而这类 bug 在训练阶段很难发现。对应 design §8 O1，已同步改稿 |
| Q4 | `event_rule_version` 的取值怎么编 | **语义化**（如 `v1-3cm-or-10d10cm`），不用自增。理由：BO-3 遗留待办已经确定未来阈值组合还会变（要加滚动累积判据），届时半年后没人能从自增数字反推出这版用的是什么规则，语义化编号本身就是自文档化的。对应 design §8 O3 |
| Q5 | 22 分区面板与 25 分区的 `dim_plow_zone` 并存，事实表要不要物理排除三个无排班分区 | **不排除，用 `has_plow_schedule` 过滤。** 排除会让"这三个区有 6.5% 的工单"这个事实从数据里消失。对应 design §8 O4 |

---

## 6. 复现

```bash
# D1 的面板规模与非零率
uv run python -m scripts.analysis.snowfall_events \
    --thresholds 3 --accum-window-days 10 --accum-threshold-cm 10 --zone-panel

# D4 的两个方向的主导份额（34.1% / 45.4%）
uv run python -m scripts.analysis.request_point_in_zone

# D6 的 8 个非法几何
uv run python -m scripts.analysis.zone_schedule_rank
```
