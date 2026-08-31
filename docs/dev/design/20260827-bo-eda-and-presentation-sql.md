# BO 循环 EDA 与呈现层 SQL

> **Status**: Draft · **Date**: 2026-08-27
>
> **本篇的上线记录**: [launch/20260827-bo-eda-and-presentation-sql-launch.md](../launch/20260827-bo-eda-and-presentation-sql-launch.md)
> **产出台账（常青）**: [requirements/bo-conclusions-and-figures.md](../requirements/bo-conclusions-and-figures.md)
> **上位矩阵**: [design/20260812-gold-bus-matrix.md](20260812-gold-bus-matrix.md)（BO → 表 → 列；本篇补第三格 **→ 图**）
> **需求合同**: [requirements/business-objectives.md](../requirements/business-objectives.md) §0.2（Description 即义务）· §0.3（P0− / P0 分级）
> **查询纪律**: [.claude/rules/gold-sql.md](../../../.claude/rules/gold-sql.md) R1–R6
> **相关 ADR**: [0009](../adr/0009-plow-zone-as-the-unit-of-analysis.md)（分析单元）·
> [0010](../adr/0010-gold-fact-grain-and-dimension-layering.md)（粒度与审计列）·
> [0011](../adr/0011-bq-hypothesis-loop-and-requirement-backpropagation.md)（结论与需求的回写回路）·
> [0012](../adr/0012-data-quality-audit.md)（DQ 三批，本篇的前置）
>
> **不新增也不修改任何 Silver / Gold schema。** 本篇只读已落盘的 17 张 Gold 表，
> 产物是 SQL、一批冻结的查询结果和三个载体上的图，不产生第 18 张表。

---

## 1. 问题

管道到 2026-08-23 已经闭合：Silver 12,477,414 行、**17 张 Gold 表全部有生产数据**、
零行的表 0、全空的列 0，DQ 审计三批全部上线（39 条规则 / 87 条检查 / 185 条断言 /
三态认证）。

**但是一张图都没有，而且没有任何流程把「表里的数」变成「台上能讲的一句话」。**

三个可观测的缺口：

| 缺口 | 现状 |
|---|---|
| **BO → 图** | [bus matrix](20260812-gold-bus-matrix.md) 把 `BO → 表 → 列` 逐格对齐了，第三格空着。哪张表的哪几列会变成哪张图，没有任何文档回答 |
| **结论的载体** | 已测出的数散在四处：[metric-feasibility-audit](../requirements/metric-feasibility-audit.md)（探针轮，打在**公开 API** 上）、五篇 launch 的门禁表、`CLAUDE.md` 的实施状态、`scripts/gold/talking_points.py`（只覆盖 BO-8 的四条分布）。**没有一处是按 BO 组织的、可以直接拿去讲的** |
| **呈现** | roadmap 把「Dashboard（负荷热力图 + 排名 + 建议文本）」挂在 **Phase 6 / H2**，而 H1 的会期是 **2026-09-19**。三个载体（Grafana 8092 · Superset 8098 · Trino 8090）都已部署跑通，缺的是内容 |

要补的不是一次性做几张图，而是一条**可重跑、可反驳的**通路：
SQL 问 → 数 → 结论（含否证）→ 图 → 图注，每一步都留在仓库里。
Description 里那句 *"You should be able to disagree with us using our own inputs"*
是一等义务（§0.2 义务映射表最后一行），它要求的正是这条通路，不是几张截图。

### 1.1 与三件已有的事划界

| 已有 | 问什么 | 打在哪 | 有没有 pass/fail |
|---|---|---|---|
| 探针轮（[metric-feasibility-audit](../requirements/metric-feasibility-audit.md)） | 这个指标**算不算得出来** | 公开 API + pandas，绕过 MinIO | 结论是 `成立 / 需改口径 / 不成立` |
| DQ 三批（ADR 0012） | 落盘的数**对不对** | Trino，`uoip_meta` 留痕 | 有，但 finding 不 fail 任务 |
| **本篇** | 这些数**说明什么、怎么呈现** | Trino 读 `uoip_gold` | **没有。EDA 不产生 fail** |

三者的**数据来源不同**这一点必须一直记着：探针打的是实时 Socrata / Open-Meteo，
管道打的是已落盘的 Gold。两者**今天就差 1–2 格**（F1 的 908 vs 探针 906，L2 launch §4.9），
成因是 Open-Meteo 回修历史存档导致 `segment_events` 重新切一遍。
因此「探针复现 = 管道正确」不是恒等式，**两个数不一致时先查是不是这个机制，再怀疑管道**。

### 1.2 已有的形状先例

[`scripts/gold/talking_points.py`](../../../scripts/gold/talking_points.py) 已经是本篇要的东西的
1/6：只读、Gold-only、**grouping 而非 filtering**（零值作为一行可见，不被折进比率）、
不能 fail 构建、输出直接是 markdown 表。本篇是把它从 BO-8 一家泛化到八个 BO，
并给每个查询补上「它变成哪张图、图注写什么」。

---

## 2. 约束

**C1 · 写方案的人拿不到线上环境。** 这是本次最硬的一条，它直接决定了工作形态：
EDA 必须**分阶段循环**，一轮 = 「给出可执行 SQL → 人在线上跑 → 结果贴回 → 总结成结论
→ 定图 → 进下一轮」，**一轮一停**。不能一次性写完六个 BO 的图，因为第 2 轮的问题
取决于第 1 轮的数。

**C2 · 时间盒 23 天。** 今天 2026-08-27，会期 **2026-09-19**。留出一周排练与做 slide，
EDA + 出图的可用窗口约 **两周半**。

**C3 · 表述纪律是硬约束，不是风格建议。** 现行禁语与纪律至少七条，且每一条都直接
改图形本身，不是改文案：

| 纪律 | 对图的硬约束 | 出处 |
|---|---|---|
| `load_level` 不得跨 `score_weight_profile` 比较 | **只能分面，不能同色阶** —— 一个天花板 100 一个 70，同色阶就是把「尺子短三成」画成「分区不忙」 | L3 launch §8 ② |
| `rank_delta > 0` 不是「模型优于基线」 | 轴标只能是 `moved_up`/`moved_down`，不能是 better/worse | L3 launch §8 ① |
| 「模型优于基线」不是可辩护的公开结论 | M1 那张图必须同框带三条保留（留出季仅 7 事件 / 目标零膨胀 / "seasonal-naive" 是错名） | BO-8 §0.2.2 |
| 面板非零率讲下界 ≥880，不讲 70.6% | 该数出现在图上时必须是下界形态 | L3 launch §8 ④ |
| 顺位差异 ≠ 不公平 | 顺位图不得用带价值判断的配色/排序（如红=差） | BO-2 / BO-8 表述纪律 |
| 不得说「十年没变」 | 顺位图**必须**同时画出漂移，否则图本身在说那句禁语 | BO-2「顺位不是常量」 |
| 不出 ward / neighbourhood 级评分 | 行政标签只能作为标注图层，且**份额必须随标签一起显示** | ADR 0009 |

推论：**图注是承重件。** 一张图脱离它的口径注就会说出禁语，所以图注与图**同源同仓**，
不能等做 slide 时再补。

**C4 · [gold-sql.md](../../../.claude/rules/gold-sql.md) 的 R1–R6 照常生效**，但代价比想象中小：
EDA 主要读 Gold，最大的 F8 是 141,377 行，F1 13,068，F6/F7 千行级——
4,878 分区那堵墙**只在下钻 Silver 时才碰到**。真正容易踩的是 **R3**（比率与百分位不可
跨块合并），因为 EDA 天然爱算占比。

**C5 · 数会漂。** F1 的 908/916 是先例：工单在变多，而 Open-Meteo 回修历史存档会让
事件边界重切。因此任何进图的数都必须与**一次具体的构建**绑定
（`etl_run_id` / `built_at` / `gold_certification.status`），否则台账里的数与图上的数
会在某一天悄悄分家。

**C6 · 不新增组件。** 计算节点可用内存 7 GB，Grafana / Superset / Trino 三者都已部署
并跑通。本篇**一个新容器都不起**。

**C7 · 现场可能断网，或 Trino 恰好在那天挂了。** 会议交付不能依赖一个要登录、要后端
活着的看板。

---

## 3. 方案

### 3.1 循环协议：一轮一个 BO，一轮一停

一轮五步，缺一步这轮不算完：

| 步 | 产出 | 落在哪 |
|---|---|---|
| ① **提问单** | 这一轮要回答的问题，逐条写死，且每条预先写出**判据**（期望值 + 它的出处） | launch 篇的阶段小节 |
| ② **SQL** | 可原样粘贴执行的查询，一问一条 | launch 篇（探索期）→ `sql/presentation/`（定稿） |
| ③ **数** | 线上跑出的真实结果 | launch 篇的阶段结果表 |
| ④ **结论** | 成立 / 需改口径 / 否证。**没有数字的行不许写结论** | [台账](../requirements/bo-conclusions-and-figures.md) |
| ⑤ **图** | 图 ID、图形、载体、数据源 SQL、**图注** | 台账 + `sql/presentation/` |

**循环单位是 BO，不是表。** 结论按 BO 讲，表是被多个 BO 共用的（`dim_plow_zone` 同时
服务 BO-2 与 BO-4）。按表切会把一条结论劈成两半。

**判据先于结果写。** 这一条抄自探针轮：先写「期望 S ≈ 1.26 / C ≈ 3.47（出处：生效中的
Session Description）」，再去跑。否则跑出什么都会显得合理——L2 阶段 D 的 F8 年份数
19 就是「照分片数推的、从没量过」，等到量了才发现是 18。

### 3.2 三条纪律

🔴 **EDA 不产生 fail。** 一条 EDA 查询不能让任何任务变红。理由与 ADR 0012 规定 2 同源
但更强：DQ 有阈值，EDA 没有——它的输出是**分布**，而分布没有对错。需要门禁的数已经
在 `scripts/gold/gates.py`（管道内等值）和 `config/dq/rules.yaml`（管道外下界）里了，
**不在这里重开第三套**。

🔴 **口径注与图同源同仓。** 每个 `sql/presentation/*.sql` 的文件头注写三样：它回答哪条
BO 判据、它的图注全文、它**不能**被读成什么（C3 那张表里对应的禁语）。图注不是 slide
上的补充说明，它是这张图的一部分。

🔴 **EDA 与冻结口径冲突时，改需求文档，不改数、也不悄悄换分母。** 走
[ADR 0011](../adr/0011-bq-hypothesis-loop-and-requirement-backpropagation.md) 的回写回路 +
[business-objectives §0.2](../requirements/business-objectives.md) 的表述退路（已行使三次）。
这不是假设：BO-3 的四次「无降雪犁雪」、F1 的 908、F8 的 18 个年份，都是这么炸出来的。
**否证是这一轮的正常产物，不是意外。**

### 3.3 载体分工：按读者分，不按图好不好看分

三个载体都在，问题不是选哪个，而是**同一份 SQL 由谁来渲染给谁看**：

| 读者 | 场景 | 载体 | 为什么是它 |
|---|---|---|---|
| **台下听众**（9-19 现场） | 定稿图，约 12–15 张 | **ECharts 冻结导出**（自包含 HTML + JSON） | 离线可放（C7）、排版可控、数与一次具体构建绑定（C5）。会期当天不依赖任何服务活着 |
| **想反驳我们的人** | 改 SQL 重跑 | **Superset**（8098，直连 Trino `uoip_gold`） | 兑现 *"disagree with us using our own inputs"*。改一行 WHERE 就重算，这是 ECharts 的冻结 JSON 给不了的 |
| **我们自己 + 想反驳我们的人** | 运行状态与趋势，以及已经搭好的 BO 面板 | **Grafana**（8092 / `grafana.huzhi.dev`） | `dq_audit_log` / `gold_certification` 天生是时序 + 状态。⚠️ **2026-08-30 更正**：Grafana 上已有 `Winter Ops Intelligence` 看板承载三块 **BO 级**面板（launch §4.2），所以它不只是「运维自用」，与 Superset 并列为可交互载体。台上仍用 ECharts，理由是 C7 而非能力 |

判据一句话：**要冻结的进 ECharts，要被改的进 Superset，要看趋势的进 Grafana。**

🔴 **载体特有的模板语法不进仓库的 SQL**（Grafana 的 `$__timeFilter`、Superset 的 Jinja）。
仓库里的 `sql/presentation/*.sql` 必须是**纯 Trino SQL，能原样粘进 CLI 跑**，
否则它既过不了 `make lint`（sqlfluff 会判 unparsable，然后**连带停掉该文件其余所有检查**
——R6 记过这个失败模式），也不再是「可复核」的那个东西。
载体要参数化，由载体那一侧包一层。

### 3.4 交付物

| 交付物 | 内容 | 性质 |
|---|---|---|
| `sql/presentation/fig_<bo>_<nn>_<slug>.sql` | 一图一份，纯 Trino SQL，文件头注含 BO 判据 + 图注 + 禁语 | **本轮的最终产物** |
| `scripts/eda/` | 批量执行 + markdown 输出 + `--json` 导出，形状照 `talking_points.py`（`_connect` / `schema_name` / `HOST_SHELL_HINT` 全部复用） | 代码 |
| `var/presentation/<fig-id>.json` | 冻结的查询结果，**带 `etl_run_id` / `built_at` / 认证状态**（C5） | 未跟踪产物 |
| `make eda-run` / `make eda-export` | 前者跑一轮打印 markdown，后者冻结 JSON | Makefile |
| [台账](../requirements/bo-conclusions-and-figures.md) | 每个 BO 一节：结论行（数 + 出处 + 日期 + 结论）+ 图表行 | **常青文档** |
| Grafana / Superset 上的看板 | 手工搭，指向同一批 SQL | 线上，不进仓 |

台账与本篇的分工照抄探针轮：**design 只写方法与门禁，数字全部进台账。**
design 是 Accepted 后冻结的，而这些数会随 Gold 重建漂移——把漂移的数写进冻结的文档，
三个月后就是一篇骗人的文档。

### 3.5 阶段地图

| 阶段 | 覆盖 | 优先级依据 | 状态 |
|---|---|---|---|
| **0** 前置核对 | 认证状态 · 构建批次一致性 · 载体连通性 | 图上的数必须来自一次**已认证**的构建 | 与阶段 1 合并成一轮 |
| **1** BO-2 排班顺位 | F2 · F3 · D1 · `dim_plow_event` · F4 | **P0−**，Description 的「发现一」 | 本次展开 |
| **2** BO-4 空间对齐 | D3 · D2 · D1 | **P0−**，Description 的「发现二」 | 待阶段 1 结果 |
| **3** BO-3 + BO-1 | D4 · F1 · F8 · F5 · D5 | P0，事件切分 + 需求侧 + 清洗教训 | 待定 |
| **4** BO-6 + BO-8 | F6 · F7 · D7 | P0，评分与推荐层（含四条禁语最密集处） | 待定 |
| **5** 定稿与落地 | `sql/presentation/` 冻结 · 三个载体搭起来 | — | 待定 |

**先做两条 P0− 的理由是可牺牲边界**（§0.3）：P0 的任何一项跑不完都能降级成「讲设计」，
BO-2 与 BO-4 跑不出来这场 talk 就没有内容。它们也最不依赖模型，数最硬。

### 3.6 图表清单（草案，数字待 EDA 填）

标 ⭐ 的是候选上台图。清单是**草案**——每一轮的实际图由那一轮的数决定，
可能增、可能删（一张图的结论被否证了就删，不硬留）。

| 图 ID | 图形 | 数据源 | 载体 | 承担的 BO 判据 |
|---|---|---|---|---|
| ⭐ FIG-BO2-01 | 横向条形 + 极差须 | F2 × D1 | ECharts | 分区平均顺位与轮候时长（S vs C ≈ 26 h） |
| ⭐ FIG-BO2-02 | 斜率图（前 9 次 → 后 10 次） | F2 × `dim_plow_event` | ECharts | 「大方向稳定但个别位移超一班次」——**这张图不画就等于在说禁语** |
| FIG-BO2-03 | 事件 × 分区顺位热力图（19×22） | F2 | Superset | 顺位序列 418/418 无缺失 |
| FIG-BO2-04 | 散点 + 拟合线 | F2 × D1 | ECharts | 顺位 × 地址数交叉验证（十年 / 近期两条） |
| FIG-BO2-05 | 表 | `dim_plow_event` × F4 | Superset | 对齐 17 / 未对齐 2 显式标注 |
| ⭐ FIG-BO4-01 | zone × ward 权重矩阵热力图 | D3 × D2 | ECharts | 不嵌套，主导份额 34.1% |
| FIG-BO4-02 | 主导份额分布 | D3 | ECharts | 「标签可以贴，数不能搬」 |
| FIG-BO4-03 | 单值 + 分母 | D3 / F1 | Superset | 空间命中率 99.9%，**分母必须同框** |
| FIG-BO4-04 | 静态 choropleth | D1 `geometry_wkt` | ECharts | 边界不嵌套的直观形态 —— **待 O3 判定是否越 §0.1 的界** |
| FIG-BO3-01 | 事件时间线（含 `accum_flag`） | D4 | ECharts | 双判据切分，N=99 / 排班期 59 |
| FIG-BO3-02 | 剂量反应曲线 | D4 × F1 | ECharts | 降雪量 → 请求量 |
| FIG-BO1-01 | 对照条形 | D5 | ECharts | 关键词误伤 99.8% 的清洗教训 |
| FIG-BO1-02 | 时序 | F8 | Superset | 十八冬趋势（§2.4 的 −66%） |
| FIG-BO1-03 | 预测 vs 实际 + 基线 | F5 | ECharts | M1，**同框三条保留** |
| ⭐ FIG-BO6-01 | 22 × 59 评分面板热力图 | F6 | ECharts | 满面板 1,298，缺失由 `score_status` 表达 |
| ⭐ FIG-BO6-02 | 三因子实际影响序 | F6 | ECharts | 名义权重 0.40/0.30/0.30 vs 实际 0.300/0.270/0.167 |
| FIG-BO6-03 | `load_level` 分布，**按 profile 分面** | F6 | Superset | C3 第一条 |
| FIG-BO8-01 | `rank_delta` 位移分布 | F7 | ECharts | 位移不是优劣 |
| FIG-BO8-02 | 归因规则命中 | F7 × D7 | Superset | 每条建议可追溯驱动因素 |
| FIG-OPS-01/02/03 | 时序 + 状态 + 新鲜度 | `uoip_meta` | Grafana | 不上台，运维自用 |

---

## 4. 被否决的选项

| 选项 | 否决理由 |
|---|---|
| **把 EDA 结论写进本篇正文** | design 一旦 Accepted 就冻结，而这些数会随 Gold 重建漂移（C5）。冻结的文档配漂移的数 = 三个月后骗人 → 进常青台账 |
| **复用 `config/dq/rules.yaml` 跑 EDA** | 语义不同：DQ 有阈值、有 severity、会进趋势线；EDA 的输出是分布，没有对错。混进去的直接后果是**规则被静音**——而 DQ 第二批的头号缺陷正是「规则跑得动但问错了问题」 |
| **直接在 Superset 里点图，不留 SQL** | 违背 *"open code"* 这条一等义务：点出来的图无法 review、无法 lint、无法在另一台机器上重跑，也无法回答「这个数怎么来的」 |
| **只做 ECharts（不用 Superset）** | 冻结的 JSON 不能被反驳者改一行 WHERE 重算 |
| **只做 Superset（不用 ECharts）** | 现场断网或 Trino 挂掉就没有图（C7）。且看板排版不适合投影 |
| **为 EDA 起 Metabase / Jupyter / 新容器** | 计算节点 7 GB，三个载体已跑通（C6） |
| **给仓库里的 SQL 加载体模板语法** | sqlfluff 判 unparsable 后**连带停掉该文件其余所有检查**（R6 的原始教训），且 SQL 不再能原样重跑 |
| **把 EDA 结果物化成新的 Gold 表** | R2：执行策略不是 schema。而且新表绕过 bus matrix 的「无 BO 指向即删表」门禁，等于在 Gold 里开后门 |
| **一次性写完六个 BO 的 EDA** | C1：第 2 轮的问题取决于第 1 轮的数。批量出题会得到一批问错问题的 SQL——DQ 第二批已经付过这个学费 |

---

## 5. 验收判据

| # | 判据 | 怎么验 |
|---|---|---|
| A1 | 六个 BO 各有一节结论，**每条结论带数 + 出处 + 日期 + 可重跑入口** | 台账里没有无数字的结论行 |
| A2 | 每张上台的图都能指到 `sql/presentation/` 里的一个文件，且反向也成立（没有孤儿 SQL） | 台账的图表列与目录逐一对照 |
| A3 | 每个 `sql/presentation/*.sql` 的头注含 BO 判据 + 图注 + 禁语三样 | 单测扫文件头注 |
| A4 | `make lint` 对 `sql/presentation/` 零告警 | sqlfluff |
| A5 | C3 那张表的七条纪律逐条在图上有对应处置 | 台账里一条一行，写明落在哪张图 |
| A6 | 冻结的 JSON 带 `etl_run_id` / `built_at` / 认证状态，且与台账里的数同源 | 抽查任意一张图 |
| A7 | 三个载体各自至少一张图跑通，且都指向同一批 SQL | 线上截图 + SQL 路径 |
| A8 | EDA 的任何一条查询都不能让任务变红 | `scripts/eda/` 无非零退出路径（除「连不上」） |

---

## 6. 开放项

| # | 开放项 | 建议 | 什么时候必须定 |
|---|---|---|---|
| ~~**O1**~~ | ~~Grafana 的数据源是否已连到 Trino~~ | ✅ **已关闭（2026-08-30）**：已连，且已有三块 BO 面板直读 Gold。**新的活是反向**——把这三块面板的 SQL 落进 `sql/presentation/`，否则 A2 不满足（launch §4.2） | 已关闭 |
| **O2** | ECharts 库如何离线自包含（vendored JS 进仓 or 只出静态 SVG/PNG） | 倾向 vendored + 自包含 HTML：SVG 丢交互，而热力图与散点现场被追问时要能悬停 | 阶段 5 之前 |
| **O3** | 静态 choropleth（FIG-BO4-04）是否越 §0.1「不做除雪状态查询地图」的界 | 倾向**不越界**——它是回溯性的边界对比，不是单点状态查询；但要显式判一次，不默认 | 阶段 2 |
| **O4** | 上台图的数量上限 | 12–15 张，超出的降为 Superset 备查 | 阶段 4 结束 |
| **O5** | 图注的语言（台上英文 / 仓库中文） | SQL 头注中文（`dev/` 惯例），图注**双语**：英文进图，中文进头注说明为什么 | 阶段 5 |
| **O6** | `scripts/eda/` 是否要单测 | 只测「头注三件套齐全」与「SQL 能 parse」，**不测数字**——数字会漂，钉死它等于给自己造假警报 | 阶段 2 |

---

## 7. 明确不做

- **不做实时进度看板 / 分区查询工具**（§0.1，与官方 Know Your Zone 重复）。
- **不给 EDA 加阈值告警**——那是 DQ 的活，重开第三套只会互相静音。
- **不动 schema、不建新表、不改任何 DML**。契约自 2026-08-13 冻结。
- **不为出图重建 Gold**。图跟着当前那次已认证的构建走；真要重建，重建后整批重导。
- **不做 ward / neighbourhood 级评分图**（ADR 0009），行政单元只作标注图层。
- **不在 H1 内接 BO-5 / BO-7 的图**（P1 与不进 Gold，§0.3）。

---

## 8. 时间盒

| 里程碑 | 日期 | 判据 |
|---|---|---|
| 阶段 0+1 跑完（BO-2） | 2026-08-29 | 台账 BO-2 一节写满 |
| 阶段 2 跑完（BO-4） | 2026-09-01 | 两条 P0− 全部有结论与图 |
| 阶段 3–4 跑完 | 2026-09-08 | 六个 BO 全部有结论 |
| 阶段 5 落地 | 2026-09-12 | 三个载体跑通，A1–A8 全绿 |
| 缓冲 + 排练 | 2026-09-13 → 09-18 | — |
| **会期** | **2026-09-19** | — |

🔴 **P0− 两条（阶段 1–2）是不可压缩的**。若 9-08 时阶段 3–4 未跑完，
砍的是 BO-1/BO-6/BO-8 的图数量（降级为讲设计），不是砍 BO-2/BO-4 的深度。
