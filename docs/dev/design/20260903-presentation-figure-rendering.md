# 呈现层：19 张图的图形类型、载体核对与渲染管线

> **Status**: Draft · **Date**: 2026-09-03
> **上承**: [design/20260827-bo-eda-and-presentation-sql.md](20260827-bo-eda-and-presentation-sql.md)
> §3.3（三载体分工的原始判据）与 §3.4（`sql/presentation/` 的交付形态）——
> 那一篇定了「谁给谁看、用哪个载体」，本篇定「同一份 SQL 具体画成什么形状、
> 多大画框」。不重开载体分工的判据，只在核对中发现两处偏离时提出修正。

---

## 1. 问题

`sql/presentation/` 下 19 个 `fig_*.sql` 已经定稿（19/19 lint 绿、138 项单测通过，
见 [launch §19.7](../launch/20260827-bo-eda-and-presentation-sql-launch.md)）。
每个文件的头注声明了 `carrier: echarts|superset`，但这只回答了"谁来渲染"，
没回答两件事：

1. **画成什么形状**——同一张 22 行的表，画成排序条形图还是散点图，取决于
   要讲哪个判据；不看 9-19 现场那张画框，这个问题在仓库里回答不了。
2. **JSON 到台上那张图之间还缺一段**——`scripts/eda/run.py --json` 只把结果
   冻结到 `var/presentation/*.json`，往下没有任何代码把它变成 C7 要求的
   "自包含 HTML"。这是本轮对话里发现的缺口，此前没人问过。

`/private/tmp/.../scratchpad/deck/UOIP-DayOfData-2026-09-19.pptx`
（由 `part1.js` / `part2.js` / `part3.js` 用 `pptxgenjs` 生成）已经把主故事线
26 张 + 附录 9 张的画框尺寸、部分图的具体图形类型写死了。这是**目前唯一的
"谁看什么形状"的真实需求来源**，本篇把它和 19 个 `fig_*.sql` 逐一对照。

🔴 **演讲稿本身不进这个仓库**——outline、speaker notes、时间分配属于
`docs/dev/README.md` 边界规则里"跟着人和日程走"的那一类。本篇只摘取
**画框尺寸与图形类型这一类工程事实**（它们决定 SQL 输出要变成什么渲染代码），
不摘录演讲稿的叙事文本。

---

## 2. 约束

**C1 · 不重开载体分工。** ECharts=冻结台上图（离线、C7）、Superset=可被
"想反驳我们的人"改 WHERE 重算、Grafana=时序与审计状态——三条判据继承自
[20260827 §3.3](20260827-bo-eda-and-presentation-sql.md#33-载体分工按读者分不按图好不好看分)。
本篇只在发现 SQL 头注的 `carrier:` 与 deck 里实际的用法冲突时提出修正（见 §3.3
两处）。

**C2 · PPTX 画框只能放静态图。** `pptxgenjs` 的 `addImage` 不能嵌入一个活的
iframe。所以 deck 里的 `tag: "ECHARTS"` / `"SUPERSET"` 标注，说的是**这张图
由哪条渲染管线产出这张静态图片**，不是"现场是不是活连接"——两者不是一回事，
下面 §3.1 把它写成一条显式规则，因为它是本轮发现的两处"载体标注冲突"
（FIG-BO4-02、FIG-BO6-03）背后的真实原因，而不是 deck 写错了。

**C3 · 断网即挂，图必须自包含。** 继承 [20260827 design C7]（会场可能没网，
Trino 可能挂）。ECharts 渲染出的 `.html` 不得引用任何 `http(s)://`
（CDN 的 echarts.min.js、`fetch()` 外部 JSON 都不许），数据与库全部内嵌进
文件本体。

**C4 · 不是每个 `fig_id` 都必须变成一张台上的图。** deck 现状已经证明了四种
落地形态，19 张图逐一对照时按这四种归类，不能默认"19 个 SQL = 19 张画面"：

| 形态 | 例子 |
|---|---|
| (a) 原样渲染成一张独立的图 | FIG-BO2-01、FIG-BO4-01 |
| (b) 聚合后变成 `pptxgenjs` 原生柱状图（appendix A2/A8/A9） | FIG-BO2-03 聚合成"每班次格数" |
| (c) 只留文字/数字引用，不画图——细节留在 Superset 里给会后追问的人 | FIG-BO2-05、FIG-BO4-03 |
| (d) 与另一张图共用同一条 SQL，只是换一种取值方式，不算新图 | FIG-BO2-01b 复用 `fig_bo2_01` 的查询 |

**C5 · 计算节点不新增组件。** 继承 20260827 C6。渲染脚本跑在开发者本机或
CI，不需要计算节点跑任何新服务。

---

## 3. 方案

### 3.1 渲染管线：补上 JSON → 自包含 HTML 这一段

现状（本轮对话里查实）：

```
sql/presentation/*.sql
        ↓ scripts/eda/run.py --json
var/presentation/<fig_id>.json   ← 带 etl_run_id / built_at / 认证状态，管线到此为止
        ↓ ??? 缺失
台上放的那份 .html
```

新增 `scripts/presentation/render_html.py`：

- 输入：`var/presentation/<fig_id>.json`（数据）+ 每个 `carrier: echarts` 的
  `fig_id` 对应一个 `option` 构造函数（下面 §3.3 给出每张图的图形类型，
  据此写 `option`）。
- 输出：`var/presentation/html/<fig_id>.html`，单文件，双击可离线打开：
  - ECharts 库内嵌为 `<script>`（vendor 进仓库一份 `echarts.min.js`，
    不connect CDN——C3）。
  - 数据内嵌为 JS 字面量，不用 `fetch()`。
  - 图注全文（`caption:` 头注）与 `must_not_say:` 的对立表述**不**画进图里，
    但 `caption:` 要作为 `<p>` 印在图下——图注是图的一部分（20260827 R6 同一条纪律
    在 `sql/presentation/README.md` 里的表述）。
- `carrier: superset` 的图不走这条管线——它们的"渲染"是在 Superset 里手工建
  chart（§3.4），`var/presentation/*.json` 对它们只是核对用的冻结基准，不是
  渲染输入。

### 3.2 画框只决定"静态图从哪条管线导出"，不决定载体分工

deck 里同一个 `figureBox` 的 `tag:` 字段（`"ECHARTS"` / `"SUPERSET"`）回答的是
**这张 PPT 上的静态图片，导出自哪条渲染管线**——ECharts 管线导出的是
`render_html.py` 生成的页面截图/导出图，Superset 管线导出的是 Superset 图表
的静态截图。两者最终都变成 PPT 里的一张 `addImage`，现场谁都不是"活连接"。

这条规则解释了下面两处看起来像"deck 写错了"的地方，实际是**两件事被叠在一起
问**：SQL 头注的 `carrier:` 回答"哪个载体承载可交互版本"，deck 的 `tag:`
回答"哪条管线产出这张静态图"——多数情况两者一致，但不必然：

| fig_id | SQL 头注 `carrier:` | deck 里的 `tag:` | 判断 |
|---|---|---|---|
| FIG-BO4-02 | `echarts` | `SUPERSET`（A5，与 FIG-BO4-01 的矩阵并列） | **建议改头注为 `superset`**——它在 A5 的定位是"配合可交互矩阵的排序条形图"，天然该活在 Superset 仪表盘里，跟旁边的矩阵同一张卡片，而不是单独导出一张 ECharts 静态图 |
| FIG-BO6-03 | `superset` | `ECHARTS`（A7，与 FIG-BO6-01 拼进同一个 `figureBox`） | **不改头注，是 deck 的 `tag:` 用得偏松**——A7 的说明写的是"配套：按 profile 分面的等级分布"，两个 profile 各自独立坐标系（C3 纪律）用 ECharts 更容易控制成两个并排的小图；执行时按 SQL 头注（Superset）建两个 small-multiple 图表，导出静态图拼进 A7，`tag:` 改成 `SUPERSET` 更准确，但这是 deck 侧的文案修正，不是本仓库的动作 |

第二处不是"两个数不一致"（20260827 R6 记的是查询结果不一致的失败模式，
这里是**两份文档对同一张图的标注不一致**，性质不同，不升格进 gold-sql.md）。

### 3.3 十九图逐图判定

判定依据：SQL 的 `SELECT` 列（数据形状）+ deck 里已经写死的画框尺寸/图形类型
（有的话）+ 常规可视化形式启发（数据形状本身建议的图形，没在 deck 里出现时）。

主故事线画框≈ 1.9:1 的宽横图（部分到 3.6:1 的通栏，如单事件全景图）；
附录画框≈ 1.6:1（右侧留一列数字列表）——这条比例差异本身是 deck 的固定
排版规律，不是每张图单独决定的。

| fig_id | carrier | 数据形状 | 图形类型 | 台上位置 |
|---|---|---|---|---|
| FIG-BO1-01 | superset | 36 行：年 × 标签类型 × 计数 | 折线（2 条序列，x=年） | 不上台——L10（ward 计数 2019 年起持续高于 neighbourhood）成因未查，未查清的异常不上台（CLAUDE.md 城市无关护栏同源纪律：不把开放项讲成结论）。留 Superset 供内部追踪 |
| FIG-BO1-02 | echarts | 1 行：五数概括（0/p25/中位/p75/p95/max，`mean` 列名自带"别画"标记） | 单箱体箱线图/百分位横条 | 建议补进附录 A8，放在"目标高度零膨胀"那条保留旁边——现在那条只有文字，图能把"中位 2.9、最大 381"一眼看出来 |
| FIG-BO1-03 | echarts | 308 行：2 个 `model_version` × 154 格 | ① 单事件：分区渲染的顺序色阶地图（主故事线 slide 23，已定稿） ② 聚合：3 柱 MAE 对比（附录 A8，`pptxgenjs` 原生柱状图，已定稿） | 两处均已在 deck 定稿，核对通过 |
| FIG-BO2-01 | echarts | 22 行：mean/min/max/sd | 横向排序条形图 + min/max 须 | 主故事线 slide 8，已定稿（1.95:1） |
| FIG-BO2-01b | 复用 01 | 同上 | 纯须条（range strip），标 shift-1 参考线 | 主故事线 slide 9，已定稿（1.47:1）。不是独立 SQL（C4-d） |
| FIG-BO2-02 | echarts | 22 行：early/late/drift | 斜率图（22 条线，仅 2 条高亮） | 主故事线 slide 10，已定稿（1.95:1） |
| FIG-BO2-03 | superset | 418 行原始粒度 | 附录聚合成 5 柱（按 `shift_number` 计数），原始 418 行留 Superset 透视表 | 附录 A2 已有聚合柱状图；细粒度钻取留 Superset，不必再画一张 |
| FIG-BO2-04 | echarts | 22 行：地址数 × 两种口径的均顺位 | 散点图 + 拟合线，2 个系列 | 附录 A3，已定稿（1.66:1） |
| FIG-BO2-05 | superset | ~49 行：禁令记录 | Superset 表格（按 `ban_type_id` / `match_state` 可筛） | 不上台——附录 A2 已用文字表格把 3 个 `ban_type_id` 的匹配率讲清楚，细节留 Superset |
| FIG-BO3-01 | echarts | 99 行：18 季全history | 横向时间轴散点（x=日期或雪季，高度/颜色=`total_snowfall_cm`，标记区分 `accum_flag` / `has_no_winter_request`） | **未上台，建议补**——现在主故事线只用一段虚构不了但经过裁剪的 14 天窗口讲"什么是一个事件"这个概念，99 个事件的全貌一次都没出现。建议加进附录 A6，配合已有的文字事实列表 |
| FIG-BO3-02 | superset | 18 行：逐季汇总 | Superset 柱状图（一季一根） | 不上台，Superset 内供追问用 |
| FIG-BO3-03 | echarts | 19 行：两种锚点的滞后天数 + `is_aligned` | 点图（x=`days_from_event_end`，颜色区分对齐/未对齐），必须同屏带出 `days_from_event_start` 两个锚点（FIG-BO3-03 头注的锚点纪律） | **未上台，建议补**——主故事线 slide 17 用状态表讲了 4 条特例，但"11 次提前开工"这个更普遍的分布只在附录 A6 的文字里，配一张点图更直观 |
| FIG-BO4-01 | echarts | 25×15 格：面积权重矩阵 | 地图叠加（主呈现）；矩阵热力图（非地图受众的替代形式，deck 已写明） | 主故事线 slide 13，已定稿（1.87:1） |
| FIG-BO4-01b | 同上，裁剪 | 单分区（V）裁剪 | 同上地图裁剪 | 主故事线 slide 14，已定稿。不是独立 SQL（C4-d） |
| FIG-BO4-02 | echarts→**建议改 superset**（见 §3.2） | 25 行：每分区的主导选区份额 | 横向排序条形图 | 附录 A5，与矩阵热力图并列，已定稿（1.66:1） |
| FIG-BO4-03 | superset | DQ 审计日志行 | Superset KPI/大数字卡片（`hit_rate_pct` + 分母 `rows_checked` 同框，不许只显示百分比） | 不单独成图——数字已引用在附录 A9 文字里；这是"当天读数"不是趋势，趋势类的空间命中率归 Grafana（20260827 §3.3 判据），不是这张冻结图的职责 |
| FIG-BO6-01 | echarts | 1,298 行：59×22，两个 `score_status` | ① 单元格：自定义标注面板，三个数字叠在图上，不配第二张图（主故事线 slide 18，已定稿，3.58:1 通栏） ② 全景：59×22 热力图，按 `score_status` 拆两块、两套色阶（附录 A7，已定稿，1.61:1） | 两处均已定稿，核对通过 |
| FIG-BO6-02 | echarts | 3 行：三因子的五数概括 | 箱线图（3 个箱体并排） | **未上台，建议补**——附录 A7 现在只用文字引用"实际影响序 0.300/0.270/0.167"，箱线图能同时带出"权重加权后的展幅"这个 SQL caption 强调的重点 |
| FIG-BO6-03 | superset（deck 的 `tag:` 写宽了，见 §3.2） | ~7 行：两个 profile × 等级 | 分面柱状图（2 个子图，各自独立坐标系，不共享数值轴——C3 纪律） | 附录 A7，与 BO6-01 拼进同一张图，已定稿（1.61:1） |
| FIG-BO8-01 | echarts | 按 `model_version` × `rank_delta` 的格数 | 发散直方图（x=`rank_delta`，正负发散配色，按 `model_version` 分面） | **未上台，建议补，且优先级最高**——这是"位移不是优劣"这条纪律唯一的量化证据，附录 A8 现在只有文字复述这条结论，没有图能让人自己去核对"两个版本移动格数一样多"这句话 |
| FIG-BO8-02 | superset | ~6 行：`attribution_rule_id` × 格数/占比 | Superset 柱状图或环形图 | 不上台——57.6% 的 `RULE-BALANCED` 目前整个 deck 都没提，可以作为 Q&A 备用点，不强求 9-19 前一定出现在幻灯片上 |

**汇总**：19 张里 10 张已在 deck 定稿（核对通过，其中 1 张的头注建议改
`carrier`）；4 张判定为不必上台（细节留 Superset）；5 张判定为"有真实缺口，
建议补进附录"——FIG-BO1-02 / FIG-BO3-01 / FIG-BO3-03 / FIG-BO6-02 / FIG-BO8-01。
Grafana 未分到任何一张：19 张都是"这一次构建的冻结快照"，Grafana 的职责是
趋势与审计状态（20260827 §3.3），性质上不适配这批图。

### 3.4 Superset 侧的图形类型

`carrier: superset` 的 8 张图（BO1-01、BO2-03、BO2-05、BO4-02→superset、
BO4-03、BO6-03、BO8-02，以及仍留在 echarts 的 BO2-01/02 之外没有变化）
建 chart 时的类型对照：

| fig_id | Superset chart type |
|---|---|
| FIG-BO1-01 | Line Chart（x=`request_year`，系列=`label_type`） |
| FIG-BO2-03 | Table（原始 418 行，可按 `plow_event_id`/`plow_zone` 筛） |
| FIG-BO2-05 | Table（49 行，可按 `ban_type_id`/`match_state` 筛） |
| FIG-BO4-02 | Bar Chart（横向，按 `dominant_share` 排序） |
| FIG-BO4-03 | Big Number with Trendline 或 KPI（`hit_rate_pct` 主数字，`rows_checked` 副标题） |
| FIG-BO6-03 | Bar Chart，`score_weight_profile` 作 dashboard 过滤器实现分面（不是同一张图的两个系列——避免共轴，同 C3 纪律） |
| FIG-BO8-02 | Bar Chart 或 Pie/Donut（`pct_of_all_cells`） |

---

## 4. 被否决的选项

| 选项 | 否决理由 |
|---|---|
| 19 张图全部导出成 ECharts 静态图塞进 PPT，不用 Superset | 违反 20260827 已定案的"要被改的进 Superset"——反驳者没法在一张静态图上改 WHERE 重算，且附录只有 9 张位置，塞不下 |
| `render_html.py` 从 CDN 拉 `echarts.min.js` | 违反 C3——会场断网或 CDN 抽风，图就是空白 |
| 把 5 张"建议补"的图直接判定为不需要，理由是"deck 已经用文字讲过了" | 文字复述不能被现场核对，而这几张图（尤其 FIG-BO8-01）恰恰是"位移不是优劣"这类高风险表述的可核验证据；不补等于把可核验的图退化成一句要听众相信的断言 |
| 把 FIG-BO6-03 的头注也改成 `echarts`，跟 deck 的 `tag:` 保持字面一致 | deck 的 `tag:` 本来就不是"载体权威"（C2），跟着它改头注是本末倒置；该改的是 deck 的文案标注，不是本仓库的 carrier 归属 |
| 用 Streamlit 统一渲染 19 张图 | 已在本次对话前一轮否决——C3（断网）与 20260827 C6（不新增组件）同时不满足，参见上一条回答 |

---

## 5. 验收判据

| # | 判据 | 怎么验 |
|---|---|---|
| A1 | `render_html.py` 对每个 `carrier: echarts` 的 `fig_id` 产出一个 `.html`，文件内不含任何 `http://`/`https://` 字面量 | `grep -L "https\?://" var/presentation/html/*.html` 应输出全部 11 个 echarts 文件名 |
| A2 | 每个产出的 `.html` 双击可在无网络环境下渲染出图 | 断网状态下用本地浏览器打开抽查 3 个 |
| A3 | FIG-BO4-02 的头注 `carrier:` 改为 `superset`，且 `test_dml_files_...`（若适用）或对应单测更新后仍通过 | `make lint` + 现有 `test_presentation_figures.py` |
| A4 | 5 张"建议补"的图，若 9-19 前决定采纳，各自能一句话对上本篇 §3.3 的图形类型；若不采纳，deck 里对应的文字表述保持不变 | 人工核对，不是本仓库门禁 |
| A5 | Superset 里 8 张 `carrier: superset` 的图，图形类型与 §3.4 一致 | 线上截图 + Superset 图表配置逐一核对 |

---

## 6. 开放项

| # | 内容 | 归属 |
|---|---|---|
| O1 | `echarts.min.js` 具体版本与存放位置（vendor 进 `scripts/presentation/vendor/` 还是别处） | 实现前必须定 |
| O2 | 5 张"建议补"的图是否真的加进 9-19 的 deck，属于 deck 自己仓库的决定，本篇只给出图形类型建议，不代为决定 | deck 作者 |
| O3 | FIG-BO6-03 的 deck 文案（`tag: "ECHARTS"` → `"SUPERSET"`）需要在 deck 侧改，不在本仓库范围内，只在本篇 §3.2 记录发现 | deck 作者 |
| O4 | `render_html.py` 的 `option` 构造是否需要为每张图单独写一个函数，还是能按图形类型（横向条形图/斜率图/散点/箱线/热力图/发散直方图）抽出 6 类共用模板——19 张图只对应 6 种图形类型，抽公共模板大概率划算，但要等第一批 3-4 张画完再判断，避免过早抽象 | 实现阶段决定 |
