# Gold / Silver 表结构上线记录

> **Date**: 2026-08-13 起 · **Design**:
> [../design/20260809-gold-silver-schema-derivation.md](../design/20260809-gold-silver-schema-derivation.md)
> （表清单 TBL-D1…F8 / TBL-S1…S7，阶段 S0–S7）
> **相关**: [../design/20260812-gold-bus-matrix.md](../design/20260812-gold-bus-matrix.md)（S2 矩阵）·
> [../adr/0010-gold-fact-grain-and-dimension-layering.md](../adr/0010-gold-fact-grain-and-dimension-layering.md)（D1–D7）
> **Result**: **In progress** —— S0–S4 已完成，本篇记录 **S3→S4 门禁复核**及其后的实现偏差；
> §2 的 31 项发现已按优先级处理完毕（结果见 §8）

---

## 0. 这一篇为什么在 S4 之前就开

按 [README](README.md) 的默认规则，launch 记录上线后写。本篇提前开篇，与前两篇同理：
**S4–S7 横跨 22 张表、多次提交、一次 16 GB 回填，等全部做完再写会丢掉过程信息**，
而其中最关键的过程信息恰恰产生在 S4 动手之前——contract 已于 2026-08-13 冻结，
冻结之后的每一处改动都必须有据可查。

design doc §5 与 bus matrix §5 都写明「S4 开始的实现偏差记进 `docs/dev/launch/`」。
本篇即那个落点。§2 是 S4 动手前的一次完整 Schema Review，它同时充当
**S3 冻结版的偏差清单**——发现的问题不是 S4 实现时才产生的，是冻结那一刻就在里面的。

---

## 1. 时间线

| 日期 | 动作 | 状态 |
|---|---|---|
| 2026-08-09 | design doc 初稿 + ADR 0010 初稿（S0） | ✅ |
| 2026-08-12 | ADR 0010 D1–D7 与 §5 五个待确认项逐条定稿，两篇改 `Accepted`（S1） | ✅ |
| 2026-08-12 | Bus Matrix 初稿（S2）+ 22 篇 contract 草稿（S3） | ✅ |
| 2026-08-12 | `event_rule_version` 进 `SNOWFALL_EVENT_SCHEMA`，双判据切分进 `segment_snowfall_events`，真实数据跑通 N=99/59 | ✅ |
| 2026-08-12 | `silver_plow_zone_boundary` 真实上游跑通：82 行 / 25 zone / 8 repaired | ✅ |
| 2026-08-13 | contract 正式冻结，提前于 8/23 时间盒；bus matrix 改 `Accepted`（S3 收口） | ✅ |
| **2026-08-13** | **S3→S4 门禁复核（本篇 §2）** —— 31 项发现，其中 6 项阻塞 | ✅ 本篇 |
| 2026-08-14 | S4：25 张表的 `sql/ddl/*.sql` + `spark/schemas/` StructType + 三方一致性单测（177 项） | ✅ |
| **2026-08-14** | **执行入口 `scripts/ddl/apply_ddl.py` + ADR 0006 §9（Trino 定性）**，见本篇 §9 | ✅ 本篇 |
| — | S5：小样本端到端 + 三个数字复现 | ⬜ 未开工（缺 4 个 Silver ETL job） |

**回滚点**：S6 全量回填之前，schema 改动的成本是「改文件」；回填之后是「重跑数小时」。
§2 的 A / B 两组必须在 S6 之前落地，C 组中的 C1 / C2 / C7 同理（见 §5）。

---

## 2. 与设计的偏差 —— S3→S4 Schema Review

### 2.0 复核范围与方法

**范围**：7 篇 `contracts/silver-contracts/` + 15 篇 `contracts/gold-contracts/`，
对照 5 篇 `contracts/api-contracts/`、business-objectives.md 八个 BO 的验收标准、
ADR 0008 / 0009 / 0010、以及**已落地的实现**
（`spark/schemas/weather_schemas.py`、`spark/schemas/plow_zone_boundary_schemas.py`、
`scripts/analysis/` 的探针）。

**方法**：四个维度各走一遍——① 字段来源 / 类型 / 主键 / 增量逻辑 / 粒度；
② Gold 模型是否支持分析与指标定义；③ 一致性 / 命名 / 性能 / referential integrity；
④ 字段是否满足 BQ。横向结论见 §3。

**总体判断**：契约集的**粒度纪律执行得好**——D1 的唯一粒度、D2 的 `forbidden_columns`、
`rank_factor` 的 NULL 语义，这几处是把纪律写成了**结构**而不是注释，
下游想写错都写不出来。类型映射也准：四个 Socrata floating timestamp 全部走
`America/Winnipeg → UTC` 且保留本地日分区列，`interaction_id` 明确保留 string
不做数值化，`shift_number` string→int 有据可查。这些是最容易出错的地方，都对了。

**但有 6 条会在 S4 写 DDL 时直接卡住或写错，其中 2 条是硬矛盾**（A1 / A2）。

发现共 31 项，分三组：

| 组 | 数量 | 性质 | 处理时点 |
|---|---|---|---|
| **A** | 6 | 阻塞——不解决 S4 建不出正确的表 | S4 之前 |
| **B** | 6 | 结构性——建议随 S4 一并改 | S4 内 |
| **C** | 19 | 一致性 / 命名 / 性能 / RI | S4–S6，C1/C2/C7 优先 |

---

### 2.1 A 组 —— 阻塞项（S4 之前必须有结论）

#### 🔴 A1 · `rank_factor` 归一化公式未定，参考实现产出的正是契约禁止的值

**契约怎么写的**：
[`fact_event_zone_rank.yaml:66`](../../../contracts/gold-contracts/fact_event_zone_rank.yaml) 与
[`fact_winter_event_zone_load.yaml:97`](../../../contracts/gold-contracts/fact_winter_event_zone_load.yaml)
都断言

```
COUNT(*) WHERE rank_factor = 0 = 0
```

两处的列注释都只说 "Normalized shift_number"，**没有给公式**。

**实现实际是什么**：探针 `scripts/analysis/score_collinearity.py` 的 `rank_factor()`
是全局 min-max：

```python
low, high = min(known), max(known)      # shift_number ∈ 1..5 → low = 1
span = (high - low) or 1.0
return [None if c.rank is None else (c.rank - low) / span for c in cells]
```

`shift_number = 1` → `rank_factor` **正好是 0.0**。418 行里约五分之一会撞上，
S 区（平均第 1.26 班）几乎每次事件都撞。

**为什么这是硬矛盾**：design doc §6.1 明写「对不上时**信探针**」。
于是这条 contract 测试跑一次就红，而且是探针对、契约错。

**根因**：这条断言把两件事混在一起了——「NULL 不许被填成 0」（对，这是 BO-6
`有效分析窗口` 的硬要求）与「0 不是合法取值」（在 min-max 归一化下是错的）。

**待决**（二选一，倾向前者）：

| 方案 | 做法 | 代价 |
|---|---|---|
| **① 定死公式** | `rank_factor = shift_number / 5`，值域 0.2–1.0 | 0 被自然排除，断言原样成立；语义更直白（第 5 班 = 满负荷）；探针需同步改，BO-6 的相关系数要复算 |
| ② 改断言 | 保留 min-max，断言改为「该事件有排班的行 `rank_factor IS NULL` 计数 = 0」 | 探针不动，但「0 不许出现」这条易读的护栏没了 |

**无论选哪个，公式必须写进契约。** BO-6 ③ 对地址数分母的态度是
「分母写进公式，**不留给实现选择**」——顺位因子的归一化应当同等对待。

> 附带：`normalise()`（同文件）在输入恒定时返回全 0，这是第二条产 0 的路径。

---

#### 🔴 A2 · 天气因子在 Gold 是 (event, zone) 粒度，Silver 只有单点

**契约怎么写的**：
[`fact_winter_event_zone_load.yaml:64`](../../../contracts/gold-contracts/fact_winter_event_zone_load.yaml)
的 `weather_severity_factor` 挂在 `(snowfall_event_id, plow_zone)` 上。

**Silver 实际有什么**：
[`silver_weather_archive.yaml:46`](../../../contracts/silver-contracts/silver_weather_archive.yaml)
自己写着

> single citywide point（multi-point zone centroids are a Gold-layer BO-3 addition,
> **not stored here yet**）

而 `dim_snowfall_event.severity_score` 也只是**事件级标量**，没有分区维度。

**探针实际怎么做的**：`score_collinearity.py` 的 `fetch_zone_weather()`
是**逐分区 `representative_point()` 取数**的——不是质心（凹形分区的质心会落在自己外面），
是保证落在几何内部的代表点，每个分区一条 Open-Meteo 存档序列。

**后果**：BO-6 ① 那个「天气项方差 **99.4% 在事件之间、0.6% 在事件之内**」——
那 0.6% 就是分区间差异。用单点数据算，它**恒等于 0**，方差分解结论复现不了。
这是 design doc §6.1 三个硬门禁之外的第四个复现问题，且它支撑的是
Description 里点名的「a weighted score whose nominal weights ranked its own factors
in the wrong order」这条对外承诺。

**待决**：

| 方案 | 做法 | 代价 |
|---|---|---|
| ① 补 Silver | 新增 **TBL-S8 `silver_weather_archive_by_zone`**（25 分区 × 日），进 §4.5 构建顺序 | 25 × 11 季的存档调用，探针 `var/probe-cache/` 已有缓存可复用 |
| ② 明确退化 | H1 内天气因子退化为**事件级常量**，同步改 BO-6 ① 的表述与方差分解一节 | 分区间极差实测仅 2.1%，退化在数值上可接受 |

**②也是可接受的答案，但必须写下来。** 现在是留白——契约声明了一个
Silver 产不出来的粒度，S4 建表的人会以为上游有。

---

#### 🔴 A3 · `winter_category` 是 F1 的主键成分，却无值域、无裁决规则、FK 指向非唯一列

[`fact_service_request_zone_event.yaml:30`](../../../contracts/gold-contracts/fact_service_request_zone_event.yaml)：

```yaml
- name: winter_category
  type: string
  nullable: false
  references: dim_service_type.winter_category    # ← 不是 dim_service_type 的键
```

三个问题叠在一起：

1. **FK 指向非唯一列。** `dim_service_type` 的 PK 是 `type`，`winter_category`
   是它上面一个 `nullable: true` 且**非唯一**的属性。FK 必须指向唯一键，
   这条在 Trino 或任何引擎里都不成立。
2. **取值域在任何地方都没有定义。** 是 6 个生效关键词类
   （`SNOW` / `FROZEN` / `PLOW` / `SANDING` / `WINDROW` / `ICE CONTROL`）吗？
   还是 7 类含 `PLOUGH`（实测命中 0 行）？契约里一个 `domain:` 都没有，
   而这是 F1 的主键成分。
3. **一个 `type` 可同时命中多个关键词类**（例如同时含 `SNOW` 与 `ICE CONTROL`），
   没有裁决规则。同一个 `type` 会算进两个类别，F1 的行数与「满面板」定义就是浮的。

**建议**：新建 **`dim_winter_category`**（6 行，PK `winter_category`，
带 `keyword_pattern` 与 `is_effective`），`dim_service_type.winter_category`
变成指向它的真 FK，F1 再指向 `dim_winter_category`。

关键词模式本来就该按**城市无关护栏 §1** 从 `scripts/analysis/snowfall_events.py`
（现在硬编码在探针里）挪进 `config/`——它是城市专有的业务语义。

---

#### 🔴 A4 · 「满面板」在 F1 上没有可执行的行数定义

design doc §4.3 写的是「满面板 **1,298 × 类别数**」，但契约
[`fact_service_request_zone_event.yaml:8`](../../../contracts/gold-contracts/fact_service_request_zone_event.yaml)
落成了：

```yaml
row_count_expectation:
  full_panel: true
  panel_cells: 1298
...
  row_count_upper_bound: "COUNT(DISTINCT (snowfall_event_id, plow_zone)) <= 1298"
```

三处不自洽：

- `panel_cells: 1298` 与 grain 含 `winter_category` **冲突**（真实行数应是 1298 × N）；
- `full_panel: true` 要求满，测试却只断言 `<=`——**漏掉一半也能过**；
- 类别数 N 没有任何地方给出（依赖 A3 先定）。

**更严重的是 S5 硬门禁跑不出来。** design doc §6.1 要求复现
「面板非零率 **70.57%**（916 格）」，但 916 是 `(event, zone)` 级的数。
含类别维度后，「非零」是指该格所有类别之和 > 0，还是每个类别单算？
契约没说，也没有对应的测试。**三个必须复现的数字里，第二个在 F1 上无定义。**

**建议**：

```yaml
row_count_expectation:
  exact: 7788          # 1298 × 6 生效类别，待 A3 确认类别数
tests:
  relationships:
    - "COUNT(DISTINCT (snowfall_event_id, plow_zone)) = 1298"
    - "COUNT(*) FROM (SELECT snowfall_event_id, plow_zone FROM ... GROUP BY 1,2 HAVING SUM(request_count) > 0) = 916"
```

第二条就是 70.57% 判据的可执行形式。

---

#### 🔴 A5 · `partial_no_rank` 行的 `load_score` 与 `scored` 行不可比，且无列承载权重口径

BO-6 §有效分析窗口明写：

> 评分在这些事件上退化为 BO-1 + BO-3 两项，**须在输出中标记权重口径，
> 不得静默重归一化**。

契约
[`fact_winter_event_zone_load.yaml:25`](../../../contracts/gold-contracts/fact_winter_event_zone_load.yaml)
只给了 `load_score` `range: [0, 100]` 和三值的 `score_status`。

**算一下面板构成**：59 个排班期降雪事件里，只有 **17** 个能对齐到犁雪事件
（19 次犁雪，2021-01-07 与 2026-02-26 两次在任何判据下都不对齐）。所以：

| score_status | 格数 | 占比 |
|---|---|---|
| `scored`（三项齐全） | 17 × 22 = **374** | 28.8% |
| `partial_no_rank`（缺顺位项） | 1298 − 374 = **924** | **71.2%** |

**四分之三的行没有 0.30 的顺位项**，权重和只有 0.70：

- 不重归一化 → 这批行的分数天然低约 30 分，`load_level` 的
  LOW/MED/HIGH/CRITICAL 阈值会把 924 行**系统性压到低档**；
- 重归一化 → 违反 BO-6 明文。

`score_status` 记的是「**缺什么**」，不是「**用了什么权重**」。

**建议**：加一列 `score_weight_profile`（`full_3factor` / `demand_weather_only`），
并在契约里写明 **`load_score` 与 `load_level` 只在同一 profile 内可比**。

> 这一条不解决，旗舰交付物 71.2% 的行会给出误导性的负载等级——
> 而 BO-6 是 design doc 标注的「BO-6 旗舰交付物」。

---

#### 🔴 A6 · `dim_plow_zone.address_count` 没有 Silver 血缘

[`dim_plow_zone.yaml:34`](../../../contracts/gold-contracts/dim_plow_zone.yaml)
声明 `source: SRC-WPG-SNOW (g3p4-h83y)`、`nullable: false`。但：

- 7 篇 silver-contract 里**没有** `silver_snow_clearing_status` 或任何等价物；
- `SRC-WPG-SNOW` 是 `snapshot` 策略，Bronze 有数据，**Silver 没有落点**；
- 从 237,867 个地址点算出 25 个分区的地址数，需要一次**点在多边形内归属**——
  这一步在 design doc §4.5 的构建顺序图里**完全不存在**。

BO-6 ③ 说这个分母「**是承重的，不是修饰**」：去掉它，
`r(顺位, 请求量)` 会从 +0.017 虚涨到 +0.139（虚增八倍）。
承重的东西现在没有生产路径。

**建议**：补 **TBL-S9 `silver_snow_clearing_address`**（或至少一个
`zone → address_count` 的聚合产物），并进 §4.5 构建顺序图——
它依赖 `silver_plow_zone_boundary`，与 `silver_service_request` 的点归属同一条通路。

> 顺带确认：三个无排班分区的地址数是已知的（`B/D` 11,150 · `X` 2,590 ·
> `Downtown` 574），所以 `dim_plow_zone` 25 行全部非空是可达成的，
> `nullable: false` 本身没问题。

---

### 2.2 B 组 —— 结构性问题（建议随 S4 一并改）

#### 🟠 B1 · 缺 `dim_plow_event`，导致两处 FK 指向非唯一列 + 一处 join 会破主键

| 位置 | 问题 |
|---|---|
| [`fact_plow_shift.yaml:20`](../../../contracts/gold-contracts/fact_plow_shift.yaml) | `plow_event_id references fact_event_zone_rank.plow_event_id`，而 F2 的 PK 是 `(plow_event_id, plow_zone)`，**被引列不唯一** |
| [`fact_parking_ban.yaml:32`](../../../contracts/gold-contracts/fact_parking_ban.yaml) | `matched_plow_event_id` 同上 |
| [`fact_event_zone_rank.yaml:39`](../../../contracts/gold-contracts/fact_event_zone_rank.yaml) | `matched_snowfall_event_id` **没有唯一性断言**。若两次犁雪落进同一个降雪事件（10 日滚动累积判据下完全可能），把 rank join 进 F6 会 **fan-out，直接破坏 F6 的 `(snowfall_event_id, plow_zone)` 主键** |

**一张 `dim_plow_event` 同时解决三个问题**：

```
dim_plow_event(plow_event_id, ban_id, first_shift_start_utc,
               matched_snowfall_event_id, is_aligned, <audit×3>)   -- 19 行
```

它本来就是这三张表缺的那个共享维度：19 次全市犁雪是一个独立的实体，
现在被摊在 F2 的复合键里。加一条断言：

```
COUNT(DISTINCT matched_snowfall_event_id) = COUNT(*) WHERE matched_snowfall_event_id IS NOT NULL
```

**附带的 STM 真空**：`plow_event_id` 从 Silver 怎么来（应该就是
`silver_plow_shift.snow_ban_id`）**在任何一篇契约里都没写**。S4 实现时会各写各的。

---

#### 🟠 B2 · F1 只收排班期 59 事件，与「pre-era 事件供 M1 长历史训练」直接冲突

[`dim_snowfall_event.yaml:45`](../../../contracts/gold-contracts/dim_snowfall_event.yaml)：

> Pre-era events feed M1's long-horizon training only

[`fact_service_request_zone_event.yaml:67`](../../../contracts/gold-contracts/fact_service_request_zone_event.yaml)：

> `snowfall_event_id -> dim_snowfall_event.snowfall_event_id WHERE is_scheduling_era = true`

F1 是 M1 **唯一**的训练面板，而它被限制在排班期。那 40 个 pre-era 事件
**没有任何表承载它们的分区级请求量**，「供 M1 长历史训练」无处兑现。
BO-6 §有效分析窗口也明写「2008–2015 仅用于 M1 的需求侧训练与长期趋势估计」，
§2.4 更要求「M1 的特征中应包含趋势项」——趋势项需要长历史。

**两条路**：

- **① F1 扩到全部 99 事件**（22 × 99 = 2,178 格/类别），评分链仍只吃
  `is_scheduling_era = true` 的 1,298 格。多 880 格，代价极小。**推荐。**
- ② 删掉 D4 里那句话，承认 M1 只用排班期，并同步改 BO-6 与 §2.4。

---

#### 🟠 B3 · F5 与 F1 粒度不一致，且没有 baseline 列

**粒度**：F5 grain 是 `(snowfall_event_id, plow_zone, model_version)`，**没有 `winter_category`**；
F1 有。M1 到底是在类别粒度训练然后加总预测，还是在加总粒度训练
（那 F1 的类别维度对 M1 就是无用的）？**两张表的粒度关系没有定义**，
而 F1 的立表理由就是 "M1's training panel"。

**基线**：ADR 0010 D5 为 BO-8 造了 `rank_baseline`，理由是
「没有基线的模型结论不予采信」。同一条纪律下，
[`fact_request_forecast.yaml`](../../../contracts/gold-contracts/fact_request_forecast.yaml)
只有 `predicted_count` / `actual_count`，**M1 的 seasonal-naive 基线没有列**。
§4.4 要求「任何模型指标必须与其基线**成对出现**」——加一列 `baseline_count` 成本为零，
不加则 MAE 对比只存在于某次运行的日志里，正是 D5 §4.3 否决过的形态。

---

#### 🟠 B4 · `fact_recommendation` 主键不含 `model_version`

[`fact_recommendation.yaml:9`](../../../contracts/gold-contracts/fact_recommendation.yaml)
的 PK 是 `(snowfall_event_id, plow_zone)`。而 `rank_model` 由 M1 驱动——
**M1 换版本重跑就是原地覆盖**，上一版的回测结果消失。

这正是 ADR 0010 D5 立表要防的事：F5 上防住了（`model_version` 在 PK 里），
F7 上漏了。BO-8 的验收标准是「对历史降雪事件做回测」，回测可复算是它的全部意义。

**建议**：PK 改为 `(snowfall_event_id, plow_zone, model_version)`，与 F5 对齐。

---

#### 🟠 B5 · `dim_snowfall_event` 掉列，且 bus matrix 点名的一列根本不存在

**掉列**（Silver → Gold，无记录理由）：`duration_days`、`peak_daily_snowfall_cm`、
`min_temperature_c`。

其中 `peak_daily_snowfall_cm` 是 BO-3 ③ **阈下累积**叙事的核心数字
（「21 日累计保留对照组的 76%，单日峰值只保留 26%，差 2.92 倍」）——
Gold 层拿不到就讲不了，而这条被 Description 点名为
"four plow operations that matched no snowfall event until we found the accumulation
was subthreshold"。

**bus matrix 对不上**：[bus matrix §2 BO-3 行](../design/20260812-gold-bus-matrix.md)
写的承担验收列是

```
event_rule_version, snowfall_sum_cm, accum_flag, severity_score
```

对照契约：`snowfall_sum_cm` 实际叫 **`total_snowfall_cm`**，
`accum_flag` **完全不存在**。

🔴 **S2 的双向门禁在这一格没有真的逐字对上。** 而 `accum_flag`
（本事件是靠单日阈值还是滚动累积命中的）恰恰是唯一能把「阈下累积」
落成数据的列——它现在只存在于探针的中间变量里。

**建议**：补回三列 + 新增 `accum_flag`，并改 bus matrix 的列名。

---

#### 🟠 B6 · `fact_service_request_daily_by_label` 分不出冬季工单

bus matrix 给它的验收是「各 ward **冬季**工单量、逐年趋势」，
但表结构
[`fact_service_request_daily_by_label.yaml`](../../../contracts/gold-contracts/fact_service_request_daily_by_label.yaml)
是 `(date, label_type, label_id) → request_count` 一个裸计数，
**既没有 winter 维度，也没有筛选声明**。

- 加 `winter_category` → 会与 F1 共用列，违反 D2 的「不与评分链共用任何列」；
- **改名 + 契约写死「本表只装冬季工单」** → 推荐，正好也强化它的立表理由。

建议改名 `fact_winter_request_daily_by_label`。

另：本表**没有行数期望**。密集情况下约 6,600 天 × 252 标签 ≈ **1.6M 行**，
是 Gold 层第二大表（仅次于 F1），值得给个量级 + 分区声明（见 C6）。

---

### 2.3 C 组 —— 一致性 / 命名 / 性能 / referential integrity

| # | 问题 | 位置 | 建议 |
|---|---|---|---|
| **C1** | `snowfall_events` 是七张 Silver 表里**唯一**没有 `silver_` 前缀的，且唯一用复数 | `silver-contracts/snowfall_events.yaml` | 改 `silver_snowfall_event`。**现在改零成本，S6 回填后改要重跑** |
| **C2** | `event_id`（降雪事件）与 `plow_event_id`（犁雪事件）两个语义完全不同的 "event" 贴脸命名 | F1/F5/F6/F7 vs F2/F3/F4 | 降雪侧统一 `snowfall_event_id`。**这是十年后最容易 join 错的一处** |
| C3 | FK 写单列，被引 PK 是复合键 | `dim_region_crosswalk.yaml:26`、`fact_service_request_daily_by_label.yaml:24` 均写 `references: dim_admin_label.label_id` | 改 `(label_type, label_id)`。crosswalk 的 `relationships` 测试写对了，列级 `references` 没跟上 |
| C4 | `score_status` 的 `no_schedule_era` **命名与语义相反，且不可能有行**——三个无排班分区已被排除出 22 分区面板 | `fact_winter_event_zone_load.yaml:39` | 删掉，或改名 `no_schedule_zone`。字面读作「非排班期」，注释却说排班期外的行根本不进本表 |
| C5 | bus matrix 的 F3/F4 列名与契约对不上：`shift_start`/`shift_end`（实为 `_utc` 后缀）；F4 的 `snow_ban_id`（F4 没有这一列，它在 `silver_plow_shift`） | bus matrix §2 | 改矩阵。与 B5 同属 S2 门禁的复核漏项 |
| **C6** | **15 篇 Gold 契约无一声明 freshness / 增量策略 / 分区键**，而 7 篇 Silver 都有 `freshness:` | 全部 gold-contracts | 统一补一句「full rebuild via `INSERT OVERWRITE`，无分区（小表例外）」。CLAUDE.md 规定「`fact_` 按 date 分区、按 region 聚簇」——这批表都不符合，是**合理的例外，但必须显式豁免**，否则 S4 写 DDL 要靠猜。幂等是 CLAUDE.md 的硬规则，不能只在 Silver 声明 |
| **C7** | `silver_service_request` **6,600 个日分区 × ~250 KB parquet** = 典型小文件问题 | `silver_service_request.yaml:18` | 分区键保留 `open_date_local`（本地日语义是对的，不能换 UTC），但写入时按月 `coalesce`。**16 GB 回填前定，回填后改要重跑** |
| C8 | `closed_ts_utc` 在 7 天回溯窗之外**永不更新**——2019 年开、2026 年关的工单，那个分区再也不会被重写 | `silver_service_request.yaml:43` | H1 不阻塞（BO-5 是 P1），但契约要写明「本列对长周期工单系统性缺失」，否则 H2 做 M2 时会当数据质量问题查半天 |
| C9 | 缺 `case_status` / `subject`。没有 `case_status` 分不清「工单仍开着」与「`closed_date` 缺失」；`subject` 是 60.8% 无地址行的解释变量（§2.1） | 同上 | 两列成本近零，建议补 |
| C10 | `is_dominant` 没有「每个 `(plow_zone, label_type)` 恰好一个 `true`」的断言，存在并列风险 | `dim_region_crosswalk.yaml:33` | 加断言 + 定并列裁决规则 |
| C11 | crosswalk 缺**支撑量**列。基于 3 条工单的 `weight = 1.0` 和基于 3,000 条的长得一模一样 | 同上 | 加 `support_n`。这张表的立表理由就是「份额必须随标签一起写出」，**样本量是份额可信度的一半** |
| C12 | `dim_channel` 的闭域（`exact: 15`）是**假设不是强制**，没有 `dim_service_type` 那样的构建期 anti-join | `dim_channel.yaml` | 加对称断言。O2 判定「风险不存在」，但强制成本和它一样低 |
| C13 | `dim_service_type` 的种子表 **bootstrap 未定义**，且构建期 LEFT ANTI JOIN 作用在全表 3,563 个取值上（不只冬季类）——首次构建要一次性覆盖 3,563 行 | `dim_service_type.yaml:46` | 定一个生成脚本（Silver `distinct type` → 规则打标 → 人工复核 diff），并说明 anti-join 的作用域 |
| C14 | `config/` 下**只有 `sources/`**。三张种子表（`dim_service_type` / `dim_channel` / `dim_recommendation_rules`）声明的 `source: config/` **目前不存在** | `config/` | S4 一并建 `config/seeds/` |
| C15 | ADR 0010 D6 写「Silver 存 82 行原始多边形，每行一个 `POLYGON`」，实际 api-contract、silver-contract、`PLOW_ZONE_BOUNDARY_SILVER_SCHEMA` **三处一致是 `MultiPolygon`** | ADR 0010 §D6 | ADR 已冻结不改，本条即为记录（launch 的正当用途） |
| C16 | `severity_score` 在 Gold 是 `nullable: false`，但其输入 `min_temperature_c` 在 Silver 是 nullable | `dim_snowfall_event.yaml:31` | 定 coalesce 规则并写进契约（探针用的是 `float(cold) if cold is not None else 0.0`） |
| C17 | `source_max_ingest_date` 对 `static` 源**无定义**——static 策略写的是 `data_static.ndjson.gz`，没有 `ingest_date=` 分区 | 全部 gold-contracts | 定为 manifest 的生成日期 |
| C18 | F2 的 `rank_factor` 注释描述了一个**在本表不可能发生**的 NULL 情形（本表恰好就是那 19 次事件的 418 行） | `fact_event_zone_rank.yaml:33` | NULL 语义属于 F6 不属于 F2；F2 的 `rank_factor` 应为 `nullable: false` |
| C19 | F2 与 F3 **是同一个粒度**（`shift_id` 与 `(plow_event_id, plow_zone)` 一一对应，都是 418 行），D1「唯一事实粒度」在这里表现为同一粒度落两张表 | F2 / F3 | 不算错（一张分析表、一张溯源明细），但契约要说明，否则下一个人会想 `union` |

---

### 2.4 D 组 —— S4 实现期发现（2026-08-14，冻结后偏差）

写完 25 张表的 DDL + StructType 后补了一个**三方一致性单测**
[`tests/unit/test_contract_ddl_schema_consistency.py`](../../../tests/unit/test_contract_ddl_schema_consistency.py)
（契约 ↔ `sql/ddl/*.sql` ↔ `spark/schemas/` StructType，25 张表 × 7 条断言，
契约为权威，另外两边都与它比、不互相比）。它一跑就抓出两条，**都不是眼睛能扫出来的**：

#### 🔴 D1 · 4 份 Silver 契约漏了 `source_id` / `loaded_at` 审计对

8 份 Silver 契约里，`weather_archive` / `weather_forecast` / `snowfall_event` /
`snow_clearing_address` 声明了这两列，`parking_ban` / `plow_shift` /
`plow_zone_boundary` / `service_request` **没有**——但这 8 张表的 StructType
**全都有**。DDL 是照契约生成的，于是后 4 张表 Spark 会写出两列 Trino 表里
没声明的字段：Parquet 落了盘，查询侧看不见，不报错。

**处置（2026-08-14，用户裁决方案 A）**：补契约，不砍 StructType。判据是
审计列在 Gold 已由 ADR 0010 D7 定为**每张表的结构义务**
（`etl_run_id` / `built_at` / `source_max_ingest_date`），Silver 的
`source_id` / `loaded_at` 是同一性质的东西，不该退化成「实现了的表才有」。
反向砍列还要重跑 `silver_plow_zone_boundary` 已落盘的 82 行。
落点：4 份契约 + 4 份 DDL。

> 这条也解释了为什么它躲过了 §2 那轮复核：复核比的是**契约与设计**，
> 而这里契约与设计都没问题，错的是契约之间不一致——只有把 25 张表放在一起
> 做机器比对才看得见。

#### 🔴 D2 · 三张分区表的分区列不在列尾，`CREATE TABLE` 建不起来

`silver_weather_archive`（`date`）· `silver_weather_forecast`（`ingest_date`）·
`silver_service_request`（`open_date_local`）三张表的分区列都排在列表中间。
Hive 连接器要求 `partitioned_by` 恰好是列清单的**末尾且同序**，否则 Trino
直接拒绝建表（`Partition keys must be the last columns in the table`）。

这条命中的正是 S4 自己的验收判据「空表能建起来」。**处置**：三份 DDL 把分区列
移到末尾并就地注明原因——DDL 的列序自此**允许**与契约列序不同，差异仅限
分区列后移，一致性单测按这条规则校验（列名比集合，分区列比后缀）。

**这一批的结论**：S4 真正的价值不在 25 个文件写出来，在于写完之后有一个
**机器可复核的三方关系**。C 组当时按「改动成本 vs 影响」放弃的 19 项里，
凡是这类结构性一致问题，以后都由这个测试兜住，不再依赖人工巡检。

---

## 3. 按评审维度的横向结论

### 3.1 字段来源 / 类型 / 主键 / 增量逻辑 / 粒度

**类型映射对得很准**——最容易出错的四处都对了：四个 Socrata floating timestamp
全部走 `America/Winnipeg → UTC` **且保留本地日分区列**；`interaction_id`
明确保留 string（api-contract 已标注 "numeric-looking, but Socrata returns it as a string"）；
`shift_number` string→int 有据可查；`ban_type_id` 保留 string。

**主键**除 B1 的三处 FK 外都成立。复合键 `(case_id, interaction_id)` 写成**约束**
而不是一步 `dropDuplicates`，这个选择是对的——约束破坏时会报警，去重只会静静少一批行。

**🔴 增量逻辑是整份契约最薄的一环**：Gold 全体没有增量/幂等声明（C6），
而 CLAUDE.md 把「所有 ETL 必须幂等」列为硬规则。Silver 侧也只声明了 freshness，
没声明 `INSERT OVERWRITE PARTITION` 还是 `MERGE`。

**粒度纪律执行得最好**——D1 的唯一粒度、D2 的 `forbidden_columns`
把纪律写成了结构而非注释，这个做法值得在 S4 保留并延续到 DDL。

### 3.2 Gold 模型是否支持分析 / 指标定义

八个 BO 的验收标准里，**能直接指到具体表和列的有 6 个**。指不到的两处都在 B5：

- BO-3 的「降雪量 → 请求量剂量反应曲线」缺 `peak_daily_snowfall_cm`、缺 `accum_flag`；
- BO-2 的「决策滞后 = `ban_start` − 降雪峰值时刻」在 Gold 层拿不到峰值日。

🔴 **更要紧的是 BO-6 的三个因子各有一个缺口**：

| 因子 | 权重 | 缺口 |
|---|---|---|
| 预测服务请求量 | 0.40 | 地址数分母**无 Silver 血缘**（A6） |
| 排班顺位 | 0.30 | 归一化公式**未定，且参考实现与契约断言互斥**（A1） |
| 天气严重度 | 0.30 | **无分区级来源**（A2） |

旗舰指标的三项输入全部有问题，这是本次复核最应优先处理的一组。

### 3.3 一致性 / 命名 / 性能 / referential integrity

**一致性**最值得改的是 C1（`snowfall_events` 缺前缀）与 C2（两种 event id），
都是现在改零成本、S6 之后改要重跑。

**RI** 的真问题集中在 B1——一张 `dim_plow_event` 一次解决三处；
其余是 C3 的复合键 FK 写成单列。

**性能**在这个数据量下基本不是问题：15 张 Gold 表最大约 1.6M 行（F8），
其余都在千行量级，无需分区与聚簇——但这个「无需」要显式写出来（C6）。
唯一真实的性能问题是 **C7 的 Silver 小文件，且必须在 16 GB 回填前定**。

### 3.4 字段是否满足业务问题（BQ）

**反向核对**（bus matrix §3 的门禁）重跑了一遍：15 张表确实都有 BO 指向，**无孤儿表**。

**正向有 3 处「BO 指向了一列，而列不存在或名字不对」**：
`accum_flag`（不存在）、`snowfall_sum_cm`（实为 `total_snowfall_cm`）、
F4 的 `snow_ban_id`（不在 F4，在 `silver_plow_shift`）。
S2 的双向门禁在这几格没有真的逐字对上（B5 / C5）。

**F8 是 bus matrix 自己标了「唯一需要提醒的一张」**，B6 印证了这个提醒——
它现在的形状支撑不了被赋予的验收标准（分不出冬季工单）。

---

## 4. 验收判据的实际结果 —— S5 三个数字的可执行性核对

design doc §6.1 的三个硬门禁，**在当前契约下是否有可执行的断言**：

| 判据 | 值 | 契约里有断言吗 | 结论 |
|---|---|---|---|
| M1 面板格数 | 1,298 | ✅ `fact_winter_event_zone_load` 的 `COUNT(*) = 1298` | **可执行** |
| 面板非零率 | 70.57%（916 格） | ❌ F1 的 grain 含 `winter_category`，"非零" 无定义，且测试只有 `<=` 上界 | **不可执行**（A4） |
| 工单空间命中率 | 99.9%（134,123 / 134,258） | ⚠️ `silver_service_request.geo_match_status` 三值可算出来，但**没有写成断言** | **可算，未断言** |

§6.2 的七条结构判据：

| 判据 | 契约支持 |
|---|---|
| 事实表只有一种粒度 | ⚠️ F2/F3 是同一粒度落两张表（C19）；F1 多一维 `winter_category` |
| 行政单元不进 fact 键 | ✅ `forbidden_columns` 已落到 F1/F2/F6/F7 |
| 顺位缺失表示为 NULL | 🔴 断言存在但与参考实现互斥（A1） |
| 供给侧连接是左连接 | ✅ `fact_parking_ban` 的 19/30 断言齐全 |
| 复合键无重复 | ✅ `silver_service_request` 的 `unique` 测试 |
| 几何已修复且有记录 | ✅ 两个粒度各有断言（Silver 8/82、Gold 8/25），已确认不矛盾 |
| 无排班分区显式标记 | ✅ `COUNT(*) WHERE has_plow_schedule = false = 3` |

**结论**：七条里五条可执行，一条有矛盾（A1），一条口径待明确（F2/F3 粒度）。
三个必须复现的数字里，**第二个在 S4 建表前必须补上可执行形式**，
否则 S5 门禁形同虚设。

---

## 5. 遗留项与处理顺序

**建议顺序**（按「改动成本随时间跳变」排，不按严重度）：

| 批 | 内容 | 为什么在这个位置 |
|---|---|---|
| **1** | **A1 + A5** | 两条都只改契约文字，但决定 BO-6 输出正确与否；A1 现在跑测试就红 |
| **2** | **A3 + A4** | F1 是 M1 的输入，粒度和行数不定，S4 建不了表 |
| **3** | **A2 + A6** | 两条都可能新增 Silver 表，会改 §4.5 构建顺序图，**越早知道越好** |
| **4** | **B1 + B5** | `dim_plow_event` 与 `dim_snowfall_event` 补列，一次 PR 能做完 |
| **5** | **B2 + B3 + B4 + B6** | 都是单表改动，可与 S4 的 DDL 同批 |
| **6** | **C1 + C2 + C7** | 三条都是**回填后改成本跳一个量级**的，必须在 S6 之前 |
| **7** | C 组其余 | 随 S4 写 DDL 顺手带上 |

**必须同步更新的文档**（改契约就要改，否则 S2 门禁再次失效）：

- `docs/dev/design/20260812-gold-bus-matrix.md` §2 —— B5 / C5 的列名；
- `docs/dev/design/20260809-gold-silver-schema-derivation.md` §4.3 / §4.4 / §4.5 ——
  新增表（TBL-S8 / S9 / `dim_plow_event` / `dim_winter_category`）与构建顺序；
- 两篇 design doc 均已 `Accepted` 且正文冻结 —— **按规矩偏差只记本篇，不回写正文**。

**不处理的**：C15（ADR 0010 D6 的 `POLYGON` / `MultiPolygon` 措辞）——
ADR 冻结不改名不改文，本篇记录即为处理。

---

## 8. 处理结果（2026-08-13，同日执行）

批 1–5（A1–A6、B1–B6）全部实现，改的是契约文字 + 两处探针代码
（`score_collinearity.rank_factor`、`snowfall_events.Event.accum_triggered`）+
`business-objectives.md` 一处口径补注 + bus matrix 列名订正。C 组按用户指示**收紧
范围**——只做了顺带发现的一处真实 bug 修复，其余 19 项维持「记录不动手」，
理由见下。跑了 `make lint` + `make test-unit`（除已知与本次改动无关的
NYC 311 网络测试外全绿）。

### 8.1 已实现

| # | 处置 | 落点 |
|---|---|---|
| A1 | 公式定死为 `shift_number / 5`（方案①），值域改 `[0.2, 1]`，"never 0" 从约定变成结构保证 | `fact_event_zone_rank.yaml`、`fact_winter_event_zone_load.yaml`、`score_collinearity.rank_factor()` |
| | 附带验证：新公式与旧 min-max 公式是同一变量的正仿射变换（shift_number 值域 1–5 全部出现），**BO-6 相关系数数值不变，无需复算** | — |
| A5 | 新增 `score_weight_profile`（`full_3factor` / `demand_weather_only`），两个断言把 `score_status` 与 profile 绑死 | `fact_winter_event_zone_load.yaml` |
| A3 | 新建 `dim_winter_category`（7 行：6 生效 + PLOUGH portability），`dim_service_type.winter_category` 改真 FK | `dim_winter_category.yaml`（新）、`dim_service_type.yaml` |
| | 附带发现但**未裁决**：同一 `type` 命中多关键词类的仲裁规则——留了 first-match-wins 的建议，标为待验证 | 同上 |
| A4 | `panel_cells` 拆成 22×99=2178（全量）与 1298（排班期），`exact` 从依赖不清的 `<=` 改成三条可执行断言（7788→13068 见 B2 联动、1298、916） | `fact_service_request_zone_event.yaml` |
| A2 | 选方案②（明确退化），非方案①（新建 TBL-S8）—— H1 内 `weather_severity_factor` 退化为事件级常量，新增断言强制「同事件跨分区取值相同」 | `fact_winter_event_zone_load.yaml`、`business-objectives.md` |
| A6 | 新建 `silver_snow_clearing_address`（TBL-S9，zone 粒度聚合，不做点粒度表——省一层），`dim_plow_zone.address_count` 血缘改指向它 | `silver_snow_clearing_address.yaml`（新）、`dim_plow_zone.yaml` |
| B1 | 新建 `dim_plow_event`（19 行），三处 FK 全部改指向它；`fact_event_zone_rank.matched_snowfall_event_id` 补 fan-out 断言 | `dim_plow_event.yaml`（新）、`fact_plow_shift.yaml`、`fact_parking_ban.yaml`、`fact_event_zone_rank.yaml` |
| B2 | F1 扩到全部 99 事件（方案①）：2178 格 × 6 类 = 13068 行；评分链 F6 仍只读排班期 1298 格，两者关系写进 note，不是隐含假设 | `fact_service_request_zone_event.yaml` |
| B3 | 写明 M1 在 F1 训练（含类别维度）、在 F5 预测（不含类别维度，`predicted_count` = 跨类别求和）；新增 `baseline_count` 列 | `fact_request_forecast.yaml` |
| B4 | PK 加 `model_version`，新增该列，与 F5 对齐 | `fact_recommendation.yaml` |
| B5 | 补回 `duration_days` / `peak_daily_snowfall_cm` / `min_temperature_c`，新增 `accum_flag`（Silver + Gold 两层）；bus matrix 列名订正（`snowfall_sum_cm`→`total_snowfall_cm`、补 `accum_flag`、`shift_start/end`→`_utc`、`snow_ban_id`→`matched_plow_event_id`） | `dim_snowfall_event.yaml`、`snowfall_events.yaml`、`20260812-gold-bus-matrix.md` |
| | `accum_flag` 的真实派生逻辑也落了代码，不只是契约声明：`Event` 加 `accum_triggered` 字段，`_event()` 按 `peak_cm < threshold` 计算 | `scripts/analysis/snowfall_events.py` + 对应单测 |
| B6 | 改名 `fact_winter_request_daily_by_label`，写明「只装冬季工单，不是全量 311 rollup」，补行数量级 | `fact_winter_request_daily_by_label.yaml`（改名） |
| 顺带 | `fact_service_request_zone_event.yaml` 的 `forbidden_columns` 段落是**发现即修的语法 bug**——list 与 `note:` 键混写，标准 YAML 解析器直接报错，不是本次 31 项之一，但会挡住任何工具读取这份契约 | 同上 |

### 8.2 未实现——收紧范围后明确放弃（用户 2026-08-13 指示：舍弃影响小的改动）

C 组 19 项里，除上面「顺带」那处真实语法 bug，**其余全部保持 §2.3 原文，不动手**。
不是漏做，是按「改动成本 vs 影响」重新掂量后主动放弃，逐项理由：

| # | 为什么不做 |
|---|---|
| **C1**（`snowfall_events` 改名 `silver_snowfall_event`） | 🔴 **审查报告的"现在改零成本"这个前提是错的**——动手前查了 `spark/jobs/etl_weather_archive.py`，Silver 层这张表**已经用真实 Open-Meteo 存档跑通过**（本篇 §1 时间线 2026-08-12 那行），物理路径 `s3a://…/silver/snowfall_events/` 已经落了数据。改名意味着要么留下契约名与物理路径不一致，要么重跑（哪怕数据量不大，也是真实成本，不是「改文件」）。**这条判断已经过时，留给 S4 做 Silver→Gold 改名时一并处理更合适**，不在这里单独动 |
| C2（`event_id`→`snowfall_event_id` 消歧） | 命名清晰度问题，非正确性 bug；牵涉 6+ 份 Gold 契约的同步改名，且 Silver 侧 `event_id` 已是实际字段名（同 C1 的顾虑）。价值真实但不紧急，留到 S4 写 DDL 时顺手做。**（已于 2026-08-15 完成，见 20260814 篇 §7.2）** |
| C3（复合键 FK 单列写法） | `relationships` 测试已经写对了复合键断言，`references:` 单列只是文档字段不影响实际约束执行；纯格式一致性 |
| C4（`no_schedule_era` 命名） | 该分支本就断言"不可能有行"，命名反直觉但不会产生错误数据；留到 S4 |
| C6（15 篇 Gold 契约无 freshness 声明） | 影响真实（CLAUDE.md 幂等硬规则），但补 15 篇文件的 rebuild 策略声明是本轮范围外的批量文档工作，且不影响任何断言能否跑通——**S4 写 DDL 时天然需要决定这件事**，不必现在单独占一轮 |
| C7（`silver_service_request` 小文件） | 需要的是"按月 coalesce"这个写入时决策，落点在 Spark job 而非契约，现在写还是没写这个决策都不影响契约可读性；16GB 回填前必须定，但不必是今天 |
| C8（`closed_ts_utc` 系统性缺失说明） | H1 不阻塞（BO-5 是 P1），文档提醒性质，价值低于成本 |
| C9（补 `case_status`/`subject`） | 加列本身零成本，但 `silver_service_request` 表还没建（roadmap 批 4/5），现在加等于在猜 S4 实现前的字段——留给真正写 DDL 那一刻 |
| C10（`is_dominant` 唯一性断言 + 并列裁决） | 需要先有真实数据观察是否存在并列，现在是纯假设性加固 |
| C11（crosswalk 补 `support_n`） | 同上，真实价值但不阻塞任何一条已验收数字 |
| C12（`dim_channel` 反向 anti-join） | 对称性加固，`dim_channel` 是 15 行的小种子表，风险本来就低 |
| C13（`dim_service_type` bootstrap 脚本） | 是一次性生成脚本的设计工作，量级与本轮契约文字改动不同，值得单独立项而非顺手做 |
| C14（`config/seeds/`） | 目录还不存在，S4 建表时自然创建，现在建空目录没有意义 |
| C15 | 原文已明确「不处理」，ADR 冻结 |
| C16（`severity_score` coalesce 规则） | 探针已有隐含规则（`float(cold) if cold is not None else 0.0`），写进契约是补文档，不改行为 |
| C17（`source_max_ingest_date` 对 static 源无定义） | 同 C6，S4 建表时天然要决定 |
| C18（F2 的 `rank_factor` NULL 语义描述） | 纯注释精度问题，不影响任何断言 |
| C19（F2/F3 同粒度说明） | 纯文档说明，两张表结构本身没有问题 |

**结论**：A 组 6 项（含 6 项全部子决策）、B 组 6 项全部落地并通过
`make lint` + `make test-unit`；C 组按「改动成本 vs 影响」收紧后，
只处理了 1 处顺带发现的真实语法 bug，其余 19 项维持记录、不动手，
理由逐条写在上表，S4 写 DDL 时可直接按此表逐项决定是否顺手带上。

---

## 6. 上线后需要观察的

S4–S7 期间盯以下几项，超阈值即停下重估而不是继续往前推：

| 盯什么 | 阈值 / 判据 | 超了怎么办 |
|---|---|---|
| S5 三个复现数字 | 1,298 / 916 格 / 134,123 命中，**逐个对得上** | 对不上时**信探针**——管道里有一步和探针口径不一致 |
| `dim_service_type` 构建期 anti-join | 未覆盖的 `type` 值数量 = 0 | 构建失败，不静默 null。首次构建预计会一次性报出大批值（C13） |
| `fact_winter_event_zone_load` 的 `score_status` 分布 | `scored` ≈ 374、`partial_no_rank` ≈ 924 | 偏离说明犁雪↔降雪事件对齐逻辑改了，须回查 A1/B1 |
| Silver 回填后的分区文件数 | **每个日分区的文件数 == 1** | 小文件问题（C7）。原阈值「单文件 ≥ 8 MB」已作废——日分区只有 0.3–0.5 MB，该阈值恒不触发。定案见 [20260814 篇 §7.4](20260814-table-creation-deployment-launch.md) |
| 空间命中率告警 | 分母是 `has_geo = true` 的子集，**不是全表** | 全表分母会永久误报（CLAUDE.md「Escalate to human」与 §2.1 的已知冲突） |
| contract 冻结线 | S4 起改 schema 走变更流程，改动记本篇 §2 | 回填期间改列 = 重跑 |

---

## 7. 与设计质量的结论

31 项发现里，**没有一项是粒度或分层决策错了**——ADR 0010 的 D1–D7 七条决策
在复核中全部站得住，D2 的 `forbidden_columns` 更是把纪律写成结构的范例。

问题集中在**从决策到契约的最后一公里**：公式没写下来（A1）、粒度声明了但上游产不出（A2）、
主键成分没有值域（A3）、行数期望与 grain 不自洽（A4）、口径要求没有列承载（A5）、
血缘断在 Silver（A6）。这六条有一个共同形态——
**契约描述了「应该是什么」，但没有描述「怎么算出来」**，
而 S4 写 DDL 的人需要的正是后者。

> 这也说明 S2/S3 提前于 8/23 冻结的判断是对的：**提前冻结换来的这十天，
> 正好够把这 31 项在 S4 动手前处理完**，而不是在回填期间发现。

---

## 9. S4 之后：建表上线

S4 的产物（25 份 DDL + StructType + 三方一致性单测）到此为止都还没对 Trino
执行过——「空表能建起来」这条判据此前只被 `make lint` 间接检查过。

**执行入口、上线步骤、风险与验收判据不在本篇**，本篇是评审报告，记的是
「契约冻结版里有什么问题、怎么处置的」。上线过程另开一篇：

→ [20260814-table-creation-deployment-launch.md](20260814-table-creation-deployment-launch.md)

那一篇同时定性了 Trino / Hive Metastore / Superset 的归属
（平台级共享服务，不进本仓库 compose），决策落在
[ADR 0006 §9](../adr/0006-storage-compute-query-stack.md)。
