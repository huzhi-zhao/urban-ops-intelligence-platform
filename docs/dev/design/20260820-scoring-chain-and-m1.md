# 评分链与 M1（L3）

> **Status**: Ready to execute · **Date**: 2026-08-17，**2026-08-20 细化**
>
> **上游需求**: [20260817-etl-implementation.md](20260817-etl-implementation.md)（E5 + E6）
> **前置上线**: [L1 Silver 全链路跑通](20260817-silver-etl-runnable.md) →
> [L2 Gold 维表与事实表](20260819-gold-dimensional-build.md) —— **两者都是硬前置**
> **上线记录**: [20260820-scoring-chain-and-m1-launch.md](../launch/20260820-scoring-chain-and-m1-launch.md)
> （执行清单、门禁表与实测数字在那篇，本篇只定口径）
> **相关**: [ADR 0010](../adr/0010-gold-fact-grain-and-dimension-layering.md) ·
> [ADR 0009](../adr/0009-plow-zone-as-the-unit-of-analysis.md) ·
> [metric-feasibility-audit.md](../requirements/metric-feasibility-audit.md) ·
> [business-objectives.md](../requirements/business-objectives.md) §BO-1 / §BO-6 / §BO-8 / §4 ·
> [.claude/rules/gold-sql.md](../../../.claude/rules/gold-sql.md) R1–R6
>
> **2026-08-20 起本篇不再是框架。** L2 的 13 张表已全部建成、门禁全绿，
> 13,068 行训练面板是既成事实，L3 的每个决定都能对着真实的行数写。
> 原 §4 的 O1（「M1 完全未定」）在本次细化中定案，见 §4。

---

## 1. 问题

L2 交付之后，Gold 还剩 **4 张表零行**，而它们承载的正是项目对外要讲的东西：

| 表 | 是什么 | 行数期望 |
|---|---|---|
| `fact_request_forecast`（F5） | M1 的预测输出 | ≤ 1,298 / 每个 `model_version` |
| `fact_winter_event_zone_load`（F6） | 冬季运营负荷评分（BO-6 三因子公式） | **1,298** |
| `fact_recommendation`（F7） | 资源调配建议（回测） | **374** / 每个 `model_version` |
| `dim_recommendation_rules` | ✅ **已建**（6 行种子，L2 阶段 C） | 6 |

加上 E6 的 DQ 基线与 S7 schema 冻结。

**为什么它不是 ETL。** F5 要训练模型，F6/F7 是打分和生成建议文本。
虽然物理上都是 Gold 表、都走整表重建（R4），但失败模式完全不同：
ETL 的失败是「数字不对」，建模的失败是「数字对但结论站不住」。
两者放进一次上线，验收判据没法写在同一个尺度上。

另一件绕不开的：advisor 定死的 "AI-Driven" 标题让 ML 层成为必须项，
所以 L3 不是「有余力再做」的一批。

---

## 2. 已定死、L3 不重开的口径

这些是探针实测出来的结论，**不是设计假设**，L3 只能照做：

- **三因子独立性成立，公式不改残差形式、权重不重分配。** 三项两两 |r| 最大仅 +0.460；
  `r(顺位, 请求量) = +0.017`、`r(顺位, 天气) = −0.006`。删掉顺位项在 15/15 个事件上
  都改变分区排序。
- 🔴 **天气项不得表述为「影响调度建议」。** 它的方差 **99.4% 在事件之间**、
  仅 0.6% 在事件之内 —— 决定评分高低，几乎不影响事件内排序，而 BO-8 消费的正是排序。
- 🔴 **实际影响序与名义权重相反**：顺位(0.300) > 请求量(0.270) > 天气(0.167) 分数单位，
  名义权重是 0.40/0.30/0.30。对外表述按实际影响序。
- **请求量因子的地址数分母是承重的**：去掉它 `r(顺位, 请求量)` 从 +0.017 虚涨到 +0.139。
  这条也是 `silver_snow_clearing_address` 存在的唯一理由。
- `weather_severity_factor` 在 H1 内是**事件级常量**（A2 方案②），
  断言「同事件跨分区取值相同」。
- **`dim_recommendation_rules` 是文字模板 + 降级兜底，不得称之为 AI。**
- 满面板 **1,298** 格（22 × 59 排班期事件），缺失用 `score_status` 表达，
  `score_weight_profile` ∈ {`full_3factor`, `demand_weather_only`} 与 `score_status` 绑死。
- `fact_recommendation` 的 PK 含 `model_version`；带 `rank_model` / `rank_baseline` / `rank_delta`。
- **建议只作用在「同一优先级内部，22 个住宅区分区谁先谁后」这一层**（BO-8，2026-08-07 收窄）。
  越出这层的建议不得出现在输出里。
- **顺位差异 ≠ 不公平**：可说「差异稳定且系统性，且没有公开文件解释它怎么定的」，
  不得滑成对市政的指控（BO-8 表述纪律 #3）。

### 2.1 L2 交出来的、本篇据以写死数字的事实

| 事实 | 值 | 来源 |
|---|---|---|
| `dim_snowfall_event` | 99 行，其中 `is_scheduling_era` **59** | L2 §3 |
| `dim_plow_event` | 19 行，`matched_snowfall_event_id` 非空 **17**，未对齐 2 | L2 §3，扇出守卫 17=17 |
| `fact_event_zone_rank`（F2） | **418** = 19 × 22，`rank_factor = 0` 的行 0 | L2 阶段 D |
| `fact_service_request_zone_event`（F1） | **13,068** / 2,178 格 / 排班期 **1,298** 格 | L2 阶段 D |
| 排班期非零格 | **908**（2026-08-19 实测，门禁为下界 ≥ 880） | L2 §4.9 |
| `dim_plow_zone` | 25 行，`has_plow_schedule = true` **22** | L2 阶段 C |
| `dim_service_type` | **3,516** 行（不是 3,563） | L2 阶段 C |

🔴 **两个由此推出来的数，本篇当硬门禁用**（推导写在这里，别再各处重推）：

```
scored          = 22 × 17（排班期内有匹配犁雪作业的事件）= 374
partial_no_rank = 22 × (59 − 17)                        = 924
                                                  合计 = 1,298 ✅
```

`no_schedule_era` 在 H1 内**恒为 0 行**：三个无排班分区根本不进这个 22 分区面板
（F1 的 `has_plow_schedule = true` 过滤），该取值是留给「未来某个分区失去排班覆盖」的。

> 🔴 **374 / 924 是从 17 推出来的，不是量出来的。** 17 会随
> `dim_plow_event` 的对齐逻辑（lag 天数、事件边界）变化。门禁写等值，
> 但**失败时先查 17 有没有变**，再决定改哪一边（§7 O3）。

---

## 3. 依赖与开工前置（L3-0）

L3 **不能在 L2 阶段 E 收口之前开工**，三件事按顺序：

| # | 事 | 判据 | 出处 |
|---|---|---|---|
| **P1** | 修 O17：`silver_service_request` 08-17/18/19 三天数据缺失 | 08-12 → 08-20 窗口回填后，每个日分区行数量级正常 | L2 launch §4.13 / §7.2 |
| **P2** | `ONLY=facts` 重跑 Gold，对 §2.1 的五个行数 | 五个数逐个相同（**预期不变**，但那是推理不是实测） | L2 launch §7.2 |
| **P3** | 重跑三条探针，刷新会漂的数 | `snowfall_events` / `score_collinearity` 的当日输出记进 launch | L2 launch §7.2 第一条约束 |

> 🔴 **P3 不是形式。** L2 §4.9 已经量到：Open-Meteo 回修历史存档 → `segment_events`
> 重新切边界 → 非零格从 916 掉到 908，而 **N / 排班期数 / 中位时长全都不动**，
> 没有第二处输出会显示这件事。M1 的面板密度、BO-6 的相关系数都是这样量出来的，
> **重跑一次探针再对比，比相信台账里的数字便宜。**

---

## 4. M1 定案（原 O1）

### 4.1 面板、目标与切分

| 项 | 定案 | 依据 |
|---|---|---|
| **训练面板** | F1 的 **2,178 格**（22 分区 × 99 事件），按 `winter_category` **求和**后的一格一行 | F1 契约：F1 是 M1 的**唯一**训练面板，含前排班期事件 |
| **目标** | `request_count` 的跨类别求和（计数）。加权量 `weighted_request_count` 作为**第二目标留待 H2**，不在 H1 训练 | F5 只有一个 `predicted_count` 列，schema 冻结 |
| **预测面板** | 排班期 **1,298 格**（F5 的行数上限） | F5 契约 `<=1298 rows, scheduling-era subset only` |
| **切分** | **时序切分**：留出**最近一个雪季**（`dim_snowfall_event.snow_season` 的最大值）作测试集，其余作训练。🔴 严禁随机切分 | BO §4.4 |
| **基线** | seasonal-naive = **同分区**在训练期所有事件上的 `request_count` 均值 | BO §4.4，F5 的 `baseline_count` 列 |
| **指标** | MAE + Poisson deviance，**必须与基线成对出现** | BO §4.4「没有基线的模型结论不予采信」 |
| **模型族** | **Poisson GLM 作基线主模型**（计数目标 + 可解释）；仅当留出季 MAE 不优于 seasonal-naive 时，才试负二项 / 梯度提升，且**换模型必须换 `model_version` 并保留旧版预测** | BO §4.1 |

### 4.2 特征集（只用预测时刻可得的量）

| 组 | 特征 | 说明 |
|---|---|---|
| 事件 | `total_snowfall_cm` · `peak_daily_snowfall_cm` · `duration_days` · `min_temperature_c` · `accum_flag` · `severity_score` | 全部来自 `dim_snowfall_event` |
| 分区静态 | `address_count`（同时作 offset，见下） | `dim_plow_zone` |
| 日历 | 雪季序号、月份 | 由 `start_date` 派生 |
| 滞后 | 同分区**上一个事件**的 `request_count`、同分区训练期滚动均值 | 🔴 滚动统计**不得跨切分边界**计算 |

- **归一化用 offset 而不是除法**：Poisson GLM 以 `log(address_count)` 作 offset，
  等价于建模「每地址请求率」，与 BO-6 那条「分母是承重的」同一件事，
  但保持目标仍是计数（MAE 与实际工单量同量纲）。
- 🔴 **`shift_number` / `rank_factor` 不进 M1 特征。** 顺位是 BO-6 的**独立第二项**，
  喂进 M1 等于把 0.30 权重项偷偷混进 0.40 权重项，三项独立性的实测结论随即失效。

### 4.3 🔴 泄漏边界：H1 的 M1 是「给定事件特征」，不是「给定预报」

BO §4.2 的红线是：**不得用事件当期的实测降雪，却宣称能提前预测。**
H1 的现状是：

- `dim_snowfall_event` 的降雪量来自 **Open-Meteo 历史存档**（实测），不是预报；
- `silver_weather_forecast` 有 Bronze 采集、有 job、**没有 DAG，也没有一行生产数据**
  （CLAUDE.md 各层进度表），且预报**不可回填**（上游不留历史）。

因此 H1 的定案是 **回测口径**，且必须一句话说清楚：

> M1 在历史降雪事件上做**留出季回测**：给定该事件的降雪量、时长、低温与分区静态属性，
> 预测各分区的冬季请求量。**投产时这些量来自预报**（同名字段已在
> `silver_weather_forecast` 就位），本次展示用的是历史存档值。

- ✅ 允许：用存档事件特征训练与回测，对外说「给定一次降雪事件的强度」。
- ⛔ 禁止：说「M1 消费天气预报预测下一场雪」——H1 内那条通路一行数据都没跑过。
- 🟡 **可选加分项（不占关键路径）**：给 `etl_weather_forecast` 补一次手动跑，
  证明预报侧字段能落地。做不完不影响验收。

### 4.4 代码落点与依赖

```
models/request_forecast/       ← 新增。角色名，不出现 plow_zone / winnipeg
    __init__.py
    features.py                ← 面板 → 特征矩阵（纯函数，可单测）
    model.py                   ← 训练 / 预测 / 评估（Poisson GLM + naive 基线）
config/models/m1.yaml          ← 特征清单、切分、weights、model_version 前缀
scripts/models/train_m1.py     ← CLI：读 Trino → 训练 → 写 artefact → 装载 F5
tests/unit/test_m1_features.py
tests/unit/test_m1_model.py
```

- **城市无关护栏**：特征名用角色名（`unit_id` / `event_id` / `unit_size`），
  Winnipeg 字段名的映射只出现在 `scripts/models/train_m1.py` 与 `config/models/m1.yaml`，
  和 `etl_plow_zone_boundary.py` 同一形态（BO §4.3）。
- **新依赖**：`[project.optional-dependencies] ml = ["pandas", "statsmodels"]`。
  🔴 **必须走独立环境 `.venv-ml`**，理由与 `make test-dags` 完全一样：
  `uv` 不会因为两个发行包往同一目录写文件而报冲突（Spark provider 覆盖
  pyspark 3.5.1 那次教训，CLAUDE.md O15）。CI 加一个 `ml` job，照抄 `dags` job 的形状。
- `make test-unit-offline` **不得**因此变红：M1 的单测走 `ml` extra，本地没装就 skip，
  CI 里真跑。

---

## 5. F5 的写入模型：版本累积 vs 整表重建

🔴 **这是本篇发现的、L2 的 R4 覆盖不到的一条冲突，必须先解决再动手。**

- R4 说：Gold 表整表重建 = `DROP` → 清 prefix → `CREATE` → `INSERT`。
- F5 契约说：`Grows with every retrain, by design — old versions are never overwritten.`
  F7 同理（PK 含 `model_version`，BO-8 要求旧回测在重训后仍可查）。

按 R4 原样跑一次 `--only scoring`，**上一版的预测与回测会被 purge 静默清空**，
而行数门禁只看当前版本，什么都不会报。

**定案（方案 A，采纳）：预测结果落 artefact，表由 artefact 整表重建。**

```
s3a://{bucket}/gold/_forecast_runs/{model_version}/predictions.csv   ← 训练产物，只追加不修改
                                   /metrics.json                     ← MAE / deviance / 基线对比
s3a://{bucket}/gold/fact_request_forecast/                           ← 表，可随时 purge 重建
```

- `train_m1.py` 只写 artefact，**不直接写表**。
- `build_gold` 新增 `scoring` 段：F5 的「DML」是一个 **loader**（形状同种子表：
  读全部 artefact → `SELECT * FROM (VALUES ...)`），F5 因此仍然是 R4 的四步整表重建，
  而**版本不会丢**——被 purge 的是表，不是 artefact。
- 行数上限：`1,298 × 版本数`，H1 内 1–3 个版本，`VALUES` 装载完全够用。
- F7 同法：它对某个版本是**确定性可重算**的（读该版本的 F5 + F6），
  重建时对 F5 里出现的每个 `model_version` 各算一遍。

**被否决的两个方案**见 §8。

---

## 6. 评分链（F6 / F7）的可执行口径

`sql/intelligence/fact_winter_event_zone_load.sql` 与 `fact_recommendation.sql`，
两份裸 `SELECT`，由 `build_gold` 组装 `INSERT`（与 `sql/dml/` 完全同一套机制）。
R1（日期谓词）在这里天然满足：两张表都只读 Gold，**一行 Silver 都不读**。

### 6.1 三个因子的取值

| 因子 | 算法 | 来源 |
|---|---|---|
| `request_forecast_factor` | 对 **1,298 格整体**做 min-max：`density = predicted_count / address_count × 1000`，再归一化到 [0,1] | F5（当前版本）+ `dim_plow_zone.address_count` |
| `rank_factor` | `shift_number / 5`，**固定分母**；无匹配作业时 **NULL** | F2 经 `matched_snowfall_event_id` + `plow_zone` 连接 |
| `weather_severity_factor` | `= dim_snowfall_event.severity_score`（事件级常量） | `dim_snowfall_event` |

三条必须写在 SQL 注释里的理由：

1. **min-max 的范围是整个 1,298 格面板，不是逐事件。** 逐事件归一化会让每个事件里
   都恰好有一个 1.0 和一个 0.0，跨事件的评分高低随即失去意义，而 `load_level`
   正是跨事件比的。探针 `score_collinearity.normalise` 就是全面板归一化。
2. **常量输入映射为 0，不是 0.5。** 沿用探针的约定：一个没有变异的因子对排序没有贡献，
   用 0 说出这件事，比给它 0.5 让加权和虚高诚实。
3. 🟡 **`severity_score` 的归一化底盘是 99 个事件，探针当时是 59 个。**
   两者都在 [0,1]，但不是同一把尺子——`dim_snowfall_event` 的 DML 已经这么建了、
   schema 冻结，本篇**照做不改**，并把这条差异写进 launch，
   免得有人拿探针的 0.6% 方差数字去对 Gold 里的列（那本来就复现不了，BO-6 §① 已注明）。

### 6.2 评分与分级

```
scored          : load_score = 100 × (0.40·rf + 0.30·rk + 0.30·w)   上限 100
partial_no_rank : load_score = 100 × (0.40·rf +           0.30·w)   上限  70   ← 不重归一化
```

- 🔴 **不得静默重归一化**（BO-6 有效窗口那节）。`score_weight_profile` 就是把这件事
  写进数据里的列。
- `load_level` 的阈值**按各自的权重上限 C 取四分位**（`full_3factor` C=100，
  `demand_weather_only` C=70）：

  | 等级 | 判据 |
  |---|---|
  | LOW | `load_score < 0.25 × C` |
  | MED | `< 0.50 × C` |
  | HIGH | `< 0.75 × C` |
  | CRITICAL | `≥ 0.75 × C` |

  **用固定阈值而不是数据分位数**：分位数会让「重建两次结果相同」依赖于数据不变，
  且新增一个事件就会让历史事件的等级跳动。C 随 profile 走，正是 DDL 注释
  「两个 profile 之间不可比」那句话的实现。
- `load_score` 在 `score_status != 'scored'` 时**不得为 NULL**——DDL 注释写的是
  「非 scored 时为 NULL」指的是 `rank_factor`；`partial_no_rank` 行有 `demand_weather_only`
  的分数，这正是那个 profile 存在的理由。🔴 **写 SQL 前重读一遍 DDL 的
  `load_score` 注释**（"Null when score_status != scored"）——它与 `score_weight_profile`
  的注释存在张力，**以哪个为准要在 L3-b 开工第一天定，并记进 launch**（§7 O1）。

### 6.3 F7：排序、基线与归因

- 覆盖面：**只对 `score_status = 'scored'` 的 374 格**（F7 契约明写）。
- `rank_model`：同一事件内按 `load_score` 降序的名次（1 = 最该先派）。
- `rank_baseline`：同一事件内按**该分区历史平均请求量**降序的名次（BO §4.4 的 BO-8 基线）。
- `rank_delta = rank_baseline − rank_model`。
- `attribution_rule_id`：取三项**加权贡献**（`weight × factor`）最大的那一项对应的规则；
  三项中最大与次大之差 < 0.05 分数单位时判 `RULE-BALANCED`；任一输入缺失走
  `RULE-FALLBACK`。
- `attribution_text`：把 `dim_recommendation_rules.template_text` 的占位符
  （`{plow_zone}` / `{shift_number}` / `{request_count}` / `{snowfall_cm}`）填上实值。
  🔴 **模板与填值都不是 AI**，对外表述见 §9。

🟡 **`RULE-NO-SCHEDULE` 在 H1 内不可达**：三个无排班分区不在 22 分区面板里，
而 `partial_no_rank` 的格子根本不进 F7。**不删**（种子是数据不是 schema，删了换城市要重加），
但 launch 里要写明它 0 次命中，免得被当成缺陷查。

---

## 7. 分段、门禁与产出

```
L3-0 前置解锁 ──> L3-a M1 + F5 ──> L3-b 评分链 F6/F7 ──> L3-c DQ 基线 + S7 冻结
   (§3)          (models/ + Python)   (sql/intelligence/)      (E6)
```

### L3-0 · 前置解锁（0.5 天）

§3 的 P1/P2/P3。**门禁**：三条全部记进 L3 launch，含探针复跑当日的数字。

### L3-a · M1 与 F5（3 天）

产出：`models/request_forecast/` · `config/models/m1.yaml` · `scripts/models/train_m1.py` ·
`ml` extra + CI job · F5 的 loader 接进 `build_gold` 的 `scoring` 段 · 单测。

**门禁**（全部可执行）：

| # | 判据 | 期望 |
|---|---|---|
| a1 | 训练面板格数 | **2,178**（跨类别聚合后） |
| a2 | F5 行数 / 每个 `model_version` | **1,298** |
| a3 | `baseline_count IS NULL` 的行 | 仅最早那个雪季的事件（**必须逐条能解释**，不是「有几行为空」） |
| a4 | 留出季 MAE：模型 vs seasonal-naive | 两个数**成对**写进 launch。模型不优于基线**不阻塞上线**，但必须如实写 |
| a5 | 特征矩阵不含 `shift_number` / `rank_factor` | 单测断言（§4.2 红线） |
| a6 | 重跑同一 `model_version` | artefact 与表行数不变（随机种子固定） |
| a7 | `make lint` + `make test-unit-offline` + 新 `ml` job | 全绿 |

### L3-b · 评分链 F6 / F7（2 天）

产出：`sql/intelligence/fact_winter_event_zone_load.sql` · `fact_recommendation.sql` ·
`build_gold` 的 `scoring` 段（含门禁）· 单测。

**门禁**：

| # | 判据 | 期望 | 类型 |
|---|---|---|---|
| b1 | F6 行数 | **1,298** | 等值 |
| b2 | `score_status = 'scored'` | **374** | 等值（推导见 §2.1） |
| b3 | `score_status = 'partial_no_rank'` | **924** | 等值 |
| b4 | `score_status = 'no_schedule_era'` | **0** | 等值 |
| b5 | `rank_factor = 0` 的行 | **0** | 等值 |
| b6 | profile 与 status 绑死：`scored` 且非 `full_3factor` / `partial_no_rank` 且非 `demand_weather_only` | 各 **0** | 等值 |
| b7 | 前排班期事件混入：`snowfall_event_id ∈ (is_scheduling_era = false)` | **0** | 等值 |
| b8 | 天气项事件级常量：`COUNT(DISTINCT weather_severity_factor)` 每事件 | **≤ 1** | 等值（>1 的组数 = 0） |
| b9 | `load_score` 值域 | `[0, 100]`，越界行 0 | 等值 |
| b10 | F7 行数 / 每个 `model_version` | **374** | 等值 |
| b11 | F7 的 `attribution_rule_id` 全部命中 `dim_recommendation_rules` | anti-join **0** | 等值 |
| b12 | 每个事件内 `rank_model` 是 1..22 的排列 | 违反的事件数 **0** | 等值 |
| b13 | 连跑两次行数逐张相同（R4 purge 验证） | 相同 | 等值 |

🟢 **门禁一律等值，没有下界。** L2 定的口径是：只有盯着实时上游的数字才允许下界
（目前仅 F1 的 908 那一条，单测钉死）。F6/F7 全部读 Gold，是管道的性质，不会自己漂。

### L3-c · DQ 基线 + S7 冻结（1.5 天）

- `make gold-dq` 跑全部 **17 张表**（`dq_baseline.py` 从 `build_gold.TABLES` 取表，
  加了 `scoring` 段自动覆盖，不必改第二处）。
- 逐表记：行数 · 各列空值率 · 构建耗时。**这是后续告警阈值的唯一依据。**
- 测试四件套补齐：`unique` / `not_null` / `relationships` / `accepted_values`。
- S2 bus matrix 逐格复核；`CHANGELOG.md` 记 schema **v1.0**；写 launch。
- 讲稿口径页（§9）定稿。

**门禁**：17 张表零行的表数 = **0**；`make lint` + 全套单测绿；CHANGELOG 有 v1.0 条目。

### 预算提示

L2 实测：13 张表全量 **2,127 秒**，其中 97% 在两张 19 分片的表上。
F6/F7 都不分片、都只读 Gold（最大 141,377 行且不分区），
**按每张 1 分钟以内估**；`--only scoring` 整段应在 2 分钟量级。
超过 5 分钟就是有 Silver 被误连进来了，回查 R1。

---

## 8. 被否决的选项

| 选项 | 否决理由 |
|---|---|
| F5 按 R4 原样整表重建（不留 artefact） | 会静默清空历史 `model_version`，而 BO-8 的验收要求旧回测重训后仍可查（ADR 0010 D5）。门禁只看当前版本，什么都不会报 |
| F5 改为 `INSERT` 追加、永不 `DROP` | 与 R4 直接冲突：一次失败的构建留下半个版本，且没有任何幂等重跑路径。artefact 方案两头都保住 |
| M1 用梯度提升起步 | 目标是计数、面板 2,178 格、要可解释且要与基线成对汇报。GLM 先跑通再谈换模型；换模型必须换 `model_version` |
| M1 训练时把 `rank_factor` 当特征 | 顺位是 BO-6 的独立第二项，混进请求量项会让三项独立性的实测结论失效（§4.2） |
| M1 面板只用排班期 59 事件 | F1 契约明写「前排班期事件喂 M1 的长历史训练」，且 BO-6 §有效窗口把「M1 用长历史、评分用近十冬」写成了分工 |
| `load_level` 用数据分位数切 | 重建不再确定：新增一个事件会让历史事件的等级跳动，而 R4 的幂等性正是靠「同输入同输出」验的 |
| `partial_no_rank` 把权重重归一化到 1.0 | BO-6「有效分析窗口」明令禁止静默重归一化；`score_weight_profile` 这一列就是为此存在的 |
| 顺位缺失填 0 | 「没有排班记录」推不出「排在最后」（ADR 0008 §2.3）。NULL 是唯一正确表示 |
| 评分聚合到 ward / neighbourhood 出「选区级评分」 | ADR 0009：ward 与 plow_zone 只有 34.1% 重合，聚合会引入约三分之二分派误差 |
| 为 F6/F7 建调度 DAG | Gold 手动触发（L2 §4.4）。`dag_gold_build` 的 `only` 参数加一个 `scoring` 取值即可，不新建 DAG（DAG 数量纪律） |

---

## 9. 对外表述边界（原 O4，定案）

一句话链条（可直接上幻灯片）：

> M1 预测下一次降雪事件中各**作业分区**的请求量 → 该预测值构成 Load Score 的 0.40 权重项
> → 推荐层按评分排序并归因。**"AI-driven" 指排序与评分由模型预测驱动；
> 推荐层本身是可审计的确定性逻辑，这是刻意的设计而非能力缺失。**

| 组件 | 能说 | 不能说 |
|---|---|---|
| F5 / M1 | 「模型」「预测」 | 「消费实时预报」（H1 内那条通路没跑过数据，§4.3） |
| F6 | 「加权公式」「三项独立性已实测」 | 「AI 评分」；「天气影响调度建议」（方差 99.4% 在事件之间） |
| F7 | 「基于历史事件的回测」「规则模板 + 降级兜底」 | 「AI 推荐引擎」；「已投产见效」（"could" 是虚拟语气） |
| 顺位 | 「差异稳定且系统性，没有公开文件解释它怎么定的」 | 「十年没变」；任何指向不公平的表述 |
| 影响力 | 「实际影响序：顺位 > 请求量 > 天气」 | 「请求量是最重要的因子」（0.40 是名义权重，不是影响力） |

---

## 10. 开放项

| # | 未定的事 | 现有建议 | 时点 |
|---|---|---|---|
| **O1** | 🔴 F6 的 `load_score` 在 `partial_no_rank` 上到底给不给值：DDL 的 `load_score` 注释说非 `scored` 为 NULL，`score_weight_profile` 注释说 71.2% 的面板按 0.70 权重打分 —— 两条注释互相矛盾 | 按 §6.2 给值（否则 `demand_weather_only` 这个 profile 没有任何行会用到），**但要在 L3-b 第一天确认并记进 launch**；若判定要留 NULL，b9 与 §6.2 一起改，schema 不动 | L3-b 开工第一天 |
| **O2** | 顺位窗口：BO-6 的 0.30 权重不得喂十年均值 | F6 用的是**该事件自身**的 `shift_number`（F2），十年均值问题在这里不存在；它约束的是 `dim_region_crosswalk` 的标定窗口（已取最近 3 个雪季）。**本条在 L3 内已闭合**，保留只为防止有人回头写「平均顺位」 | 已闭合 |
| **O3** | 374 / 924 是从「17 个已对齐犁雪作业」推出来的 | 门禁失败时**先查 17 有没有变**（`dim_plow_event` 的对齐 lag / 事件边界），再改门禁数字。改数字要连带改 §2.1 与 launch | 门禁红时 |
| **O4** | BO-3 事件定义若因滚动累积判据再改 N，面板格数与回测次数同步改 | **不改 schema**，只改行数判据。L3 开工前确认 N=99/59 已冻结（§3 P3 会顺带看到） | L3 开工前 |
| **O5** | `ml` extra 与 pyspark 的共存 | 独立 `.venv-ml`，CI 单开 job。**不要**图省事塞进 `dev` —— O15 那次 pyspark 被静默覆盖，lock 文件还写着旧版本 | L3-a 开工 |
| **O6** | `silver_weather_forecast` 一行数据都没有 | H1 不阻塞（§4.3 回测口径）。有余力手动跑一次 `etl_weather_forecast` 证明字段能落地 | 缓冲期，可选 |
| **O7** | 地址数分母取的是当期快照，却用于归一化 2015 年起的历史事件 | 量级不大，**作为局限主动说明，不等被问**（BO-6 已记） | 讲稿 |

---

## 11. 时间盒

| 窗口 | 段 | 说明 |
|---|---|---|
| 8/20–8/21 | **L3-0** | 前置解锁：O17 → `ONLY=facts` 重跑 → 探针复跑 |
| 8/22–8/26 | **L3-a** | M1 + F5。关键路径：a4 的两个数出不来，L3-b 的 `request_forecast_factor` 没有输入 |
| 8/27–8/30 | **L3-b** | F6/F7，13 条门禁 |
| 8/31–9/6 | 机动 | 伞篇给 E5 的窗口，用来吸收 L3-a 的模型返工 |
| 9/7–9/13 | 缓冲 | **不吃进 L3 的范围** —— 出问题在缓冲里解决，不是拿缓冲加功能 |
| 9/14–9/19 | **L3-c** + 讲稿 | 只收尾，不动 schema |

会期 2026-09-19 不挪，contract 冻结线不挪。
关键路径 = **L3-0 → L3-a → L3-b**；L3-c 可与讲稿并行。
