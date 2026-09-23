# UOIP 英文叙事型 Portfolio 需求

日期：2026-09-13。状态：**已实现（H1 交付，2026-09-19）；2026-09-22 按实现回填**，
见 §0。§10 为 H2 预测内容预留接入规则，方向未定。

本篇取代同文件早先的“分析型 Dashboard”定位。实现位于 `dashboard/`（React + Vite，
静态构建），构建与运行见 `dashboard/README.md`。

## 0. 2026-09-22 回填：文档与实现的偏移

H1 前后站点新增了 `#/zone`、`#/map` 两页，证据库从 23 条扩到 25 条，而本篇没有跟着改。
下表逐条记录偏移与处置。**原则：实现已被 ADR 或已落地的判断支持的，文档跟实现走；
实现违反本篇口径的，记为待修，不在文档里迁就实现。**

| # | 原文档 | 实际实现 | 处置 |
| --- | --- | --- | --- |
| D1 | 两个页面：`/` 与 `/evidence` | 四个：`/`、`#/evidence`、`#/zone`、`#/map`；hash 路由 | 文档跟实现，见 §7 |
| D2 | 无分区查询页；§1 写“不提供我家街道清了没有的查询” | `#/zone`（导航名 **Your zone**），读者选分区拿两个回溯答案 | ADR 0013 §2.3 引入；与 §1 不冲突——它回答的是历史顺位，不是当前状态。新增 §5.2 |
| D3 | 无 | `#/map`：25 个分区的交互地图，首屏可进入 | 新增 §5.3 |
| D4 | Evidence Explorer 收录 23 条（19 核心 + 4 解释） | 25 条：再加 2 条 `carrier: lookup`（`FIG-BO2-06/07`），角色 **Zone lookup** | 文档跟实现，见 §5.1 |
| D5 | 图表由 ECharts 承担 | 全部为手写 SVG（`dashboard/src/charts.jsx`） | 文档跟实现，见 §6 |
| D6 | 首屏主标题 “What ten winters of open data reveal…” | 主标题 **Who gets plowed first?**，数据视觉为按平均班次点亮的分区地图（`FIG-BO4-00` 几何 + `FIG-BO2-01` 数值） | 文档跟实现，见 §4.1 |
| D7 | Act I 主图只有 BO2-01 / BO2-02 | 另有 `FIG-BO2-03` 作业×分区方格（`418 / 418`）与 `FIG-BO2-04` 地址数反证（r = +0.49） | 文档跟实现，见 §4.2 |
| D8 | Act V 用 BO6-01 / BO1-03 / BO8-01 | 另加 `FIG-BO6-03` 两个 profile 分面 | 文档跟实现，见 §4.6 |
| D9 | 冻结时间无规定 | 页脚与 `#/zone` 从数据读取冻结日，多次冻结时显示区间 | 升为要求，见 §5.4 |
| D10 | — | 🔴 Act IV 的 Bento 卡片仍写 **“23 public SQL queries”**，证据库页已写 25 | ✅ **已修（2026-09-22）**：计数改从证据目录读取（`useCatalogueCounts`），证据库页的 25 也一并改成读数据，见 §9 |
| D11 | — | 🔴 页面把 19 写成 “19 city-wide residential plow operations”，未说明它是**发布产物** | ✅ **已修（2026-09-22）**：ADR 0014 §1.1 认定 19 数的是“发了停车禁令并公布班次表”的作业，2017、2023 整年为空。措辞已改，见 §3、§9 |
| D12 | — | 双屏投影（跟随滚动的第二屏、快捷键打开）只在分支 `claude/mobile-dev-repo-migration-msztuc`，**未合入 main** | 不写进需求；合入时再补 §6 |

## 1. 产品定位

这是 UOIP 的公开英文 Portfolio，是 conference talk 和项目展示的延伸。二维码是现场让
观众记住并带走项目入口的传播媒介，不预设他们当场在手机上完成阅读。主要使用场景是观众
在演讲后用电脑打开页面，一边理解故事，一边进入 GitHub、SQL 和实现代码继续核查。

GitHub README 与 Portfolio 的分工：

| 载体 | 类比 | 主要任务 | 主要读者 |
| --- | --- | --- | --- |
| GitHub README | résumé | 说明项目包含什么、怎样运行、去哪里读文档 | 招聘方、工程师、维护者 |
| Portfolio 首页 | portfolio | 让陌生访客记住问题、发现与项目判断力 | 会议观众、数据从业者、公共治理社区 |
| Your zone（`#/zone`） | lookup | 让读者对自己的分区拿到两个不越过证据的回溯答案 | 住在温尼伯的读者 |
| Evidence Explorer | appendix | 让读者检查数据、SQL、版本与解释边界 | 想复核结论的技术读者 |

首页不是实时运营工具。`#/zone` 也不是：它只讲**已完成作业**里某分区排第几、
“照抄上次”这条规则历次命中多少，**不回答“现在”**（ADR 0013 §3 的判据：
能回答“现在”的，是市政官方工具的活）。

## 2. 受众与成功标准

读者不需要了解 Lakehouse、Trino、Gold、BO 或评分 profile。桌面端是主要阅读环境，适合
同时浏览 Portfolio、Evidence Explorer 与 GitHub 代码；移动端承担落地、快速理解和保存
入口的职责，内容仍须完整可读，但不以手机上的深度图表操作为主要体验。

阅读目标分三层：

1. **演讲现场：**观众从二维码和页面预览中认出项目名称、核心问题与网址，愿意稍后打开。
2. **桌面端 2 分钟：**理解两个核心发现：排班全覆盖不等于顺位相同；作业分区不能直接
   换成 ward。知道数据曾多次改写分析问题。
3. **桌面端深入阅读：**理解自托管数据管道、评分证据的限制和 AI 层的探索性质，并能从
   结论直接进入对应 SQL、数据与 GitHub 实现；或在 `#/zone` 查自己的分区。

成功不以图表点击量衡量。验收时应由一位不了解项目的人在不读 README 的前提下复述：
研究问题、两条发现、数据来源的大类，以及项目没有证明什么。

## 3. 内容语言与语气

所有可见界面必须使用英文，包括导航、按钮、图题、图例、tooltip、空状态、错误信息、
可访问性标签、元数据和移动端菜单。`sql/presentation` 中的中文头注继续作为内部来源，
不能直接显示在公开页面。

英文语气应像清晰、克制的展览文字：短句、具体数字、少术语。可以表达好奇心和判断，
不能把相关性写成因果，把排班写成清雪完成时间，把模型实验写成运营改善。

🔴 **“19 次作业”的表述边界**（ADR 0014 §1.1，2026-09-19 起生效）：19 数的是
**公布了住宅区停车禁令和逐分区班次表**的全市作业，不是温尼伯十年里的全部清雪。
页面可以说 `19 published city-wide residential operations`；不能让读者读成
“十年只清了 19 次雪”，也不能让 “Nobody is skipped” 超出“在这 19 次公布的作业里”
这个范围。2026-09-22 已按此修正，见 §0 D11。

主操作：`Explore the story`。次操作：`View the source on GitHub`，指向
`https://github.com/huzhi-zhao/urban-ops-intelligence-platform`。

## 4. 叙事结构

首页是一页连续叙事，由 Hero、五幕与 Closing 组成，五幕外包一条 Tracing Beam。
信息顺序和证据义务固定；文案以 `dashboard/src/story.jsx` 为准，本节只记义务。

### 4.1 Opening — the question

实现：主标题 **Who gets plowed first?**，眉题 `Winnipeg · ten winters of open data`，
两句说明、两个入口（`Explore the story` / `View the source on GitHub`），右侧是 25 个
分区按平均班次点亮的地图，下接图例与 `Open the zone map`（进入 `#/map`）。
首屏底部抛出问题：

> If every residential zone is scheduled, what does “first” actually mean?

移动端首屏保留项目名、核心问题和至少一个入口。首屏不出现查询数量、BO 编号、审计状态、
SQL 搜索框或六主题导航。无排班的 3 个分区只画虚线轮廓、不填色。

### 4.2 Act I — the wrong question

先揭示反直觉事实：19 次公布的全市住宅区犁雪作业，每一次都覆盖 22 个排班分区。因此
“谁被跳过”不是数据支持的问题，故事转向“谁通常排得更早”。

主数字与视觉：

- `418 / 418` 作业×分区格无缺失 —— `FIG-BO2-03` 方格图，两次对不上降雪事件的作业列
  以淡色标出。
- Zone S 平均第 1.26 班，Zone C 平均第 3.47 班；2.21 个班次约等于 26.5 小时的
  **计划开工偏移** —— `FIG-BO2-01`。
- 反转：21/22 个分区都曾进入首班，包括平均最晚的 C；前 9 次与后 10 次比较，V 晚了
  1.31 班、M 晚了 1.02 班 —— `FIG-BO2-02`。
- 一条现成解释不成立：地址多的分区并不排得更早，反而偏晚（r = +0.49）——
  `FIG-BO2-04`。

读者离开本节时应理解“长期平均有差异”和“顺位会移动”可以同时成立，且数据量的是结果、
不是背后的规则。

页面措辞必须使用 `scheduled position`、`planned start offset` 或 `scheduled shift`，
不能使用 clearing completion、actual wait 或 fairness verdict。

### 4.3 Act II — the map changes the meaning

第二条核心发现：工作按 plow zone 组织，公共讨论常按 ward 组织，而两种单元不能直接
互换。实现为左侧数字 + Zone V 的分区几何（多块散落全市），右侧 25×15 关系矩阵。

必须出现：

- 只有 2/25 个作业分区的记录权重全部落在一个 ward 标签。
- 主导 ward 标签份额中位约 54%。
- Zone V 的工单横跨 10 个 ward 标签，最大一个（St. Vital）约占 26%。

`FIG-BO4-01` 与 `FIG-BO4-02` 是主证据。份额来自 2023-11 至 2026-05 冬季工单标签计数，
不是面积、人口、几何重叠或清雪工作量。页面可以画**分区**几何（`FIG-BO4-00`），
不能画 ward 几何（仓库里没有），也不能把 “A work zone is not a ward” 表述成任何一套
边界画错了。

### 4.4 Act III — the data kept rewriting the question

Sticky Scroll Reveal 讲三个“原假设 → 数据事实 → 工程处置”：

1. `%ICE%` 的宽松关键词筛选有 99.8% 行级误伤；因此建立可审计的类别映射
   （`FIG-BO1-04`）。
2. 单日降雪阈值不足以表达连续小雪；事件规则采用“单日 ≥ 3 cm OR 十日累计 ≥ 10 cm”，
   99 个事件中 8 个只因累计判据成立（`FIG-BO3-00/01`）。
3. 评分的名义权重没有决定实际展幅；同时展示因子的实测范围（`FIG-BO6-02`）。

每张图只服务一个故事转折，不构成查询目录。case ID 非唯一可作为较长版本的补充卡，
首版不承担主线。

### 4.5 Act IV — from pipeline to evidence

五步流向：Sources → Bronze → Silver → Gold → Findings，每步第二行标技术栈
（MinIO / Spark · Airflow / Hive Metastore · Trino / SQL · frozen exports）。
下接 Bento Grid：可重放摄取（12.47M）、带分母的空间命中率（读 `FIG-BO4-03` 最新一行）、
事件切分、质量审计、可执行 SQL。

先回答“为什么需要这套系统”，再回答“工程做了什么”。技术栈名称不能取代成果叙述。
Bento 里出现的任何计数（查询条数、事件数）必须与数据或证据库一致，不能手写后
不维护——见 §0 D10。

允许复用 `docs/images/urban-ops-oneline-light-transparent.jpg` 和
`docs/images/platform-architecture.svg`，页面主线仍使用网页专用的简化版本。

### 4.6 Act V — an honest AI layer

结尾展示模型和推荐层为什么有用、证据又到哪里为止：

- 评分面板 1,298 格；374 格有需求、天气和排班三类证据，924 格没有匹配的排班证据
  （71.2%）。两个 profile 分开画、不共享色标或轴 —— `FIG-BO6-01`、`FIG-BO6-03`。
- 留出季只有 7 个事件、154 格；M1 MAE 7.345、去月份对照 7.919、弱基线 23.628，
  三者同框 —— `FIG-BO1-03`。
- 两个模型版本都产生 188 格上移；rank displacement 是实验输出，不是效果改善 ——
  `FIG-BO8-01`。

措辞强调 auditable experiment、visible assumptions 和 open evidence。不能宣称模型优于
合适的运营基线，不能把历史天气特征上的回测描述成真正的暴风雪前预测。
H2 若把预测内容加进来，走 §10，不在本幕里就地扩写。

### 4.7 Closing — disagree with the project using its own inputs

> **Everything is public data and open code.**
>
> Inspect the assumptions, rerun the queries, and disagree with the project
> using its own inputs.

两个出口：`Explore all evidence` 和 `View the GitHub repository`，下列四条禁语
（排班不是清雪时间 · 顺位差异不是公平裁决 · 请求份额不是面积或人口 · 排名位移不是
模型改进）。不增加联系表单、订阅、账号或泛化的 “Get started”。

## 5. 辅助页面

### 5.1 Evidence Explorer（`#/evidence`、`#/evidence/<fig_id>`）

收录 `sql/presentation` 当前 **25** 条查询，分三种角色（`scripts/presentation/portfolio.py`）：

| 角色 | 条数 | 内容 |
| --- | --- | --- |
| Core evidence | 19 | 原始 19 张图 |
| Explanatory view | 4 | `FIG-BO1-04` · `FIG-BO3-00` · `FIG-BO4-00` · `FIG-BO6-00` |
| Zone lookup | 2 | `FIG-BO2-06` · `FIG-BO2-07`（`carrier: lookup`，ADR 0013） |

Lookup 两条单列一个角色而不并入 core：core 19 是故事的发现，这两条是为回答单个分区的
问题而存在，并入会改动 launch 记录里写定的数。

每条提供英文标题与图注、`How not to read this`、Chart / Data view / SQL 三个视图、冻结
时间与认证状态；按故事章节筛选（章节由 BO 映射，BO 编号只放在技术元数据）并支持搜索。
没有专用图的条目以 `Data view` 呈现，不得出现“专用图形待补”一类开发提示。

SQL 的 `criterion/caption/must_not_say` 仍是口径来源，英文内容层人工核对，不在浏览器
机翻。测试 fixture 必须标为 `Sample data — not a production result`。

### 5.2 Your zone（`#/zone`）

ADR 0013 §2.2 的 H2 完成判据落在这一页。读者从下拉框选一个分区，得到两个答案：

1. **Why is my street cleared later than my friend's?** —— 该分区在历次作业里的平均班次、
   在 22 个分区中的位次、班次分布、前后两半的位移。数据：`FIG-BO2-06`。
2. **Will my zone be earlier next time?** —— 只量一条规则：“下次照抄上次”的精确命中率与
   ±1 命中率。数据：`FIG-BO2-07`。

硬性要求：

- 🔴 **两个答案都不是模型输出**，页面也不许这么写（ADR 0013 §2.2）。
- 🔴 第二个答案必须与对照同框：全市同一规则的命中率，以及“每次都猜第 N 班”的 ±1
  命中率。这不是“规则优于基线”的结论。
- 🔴 页面不做算术。所有比率在 `scripts/presentation/zone_lookup.py` 里先加总后算一次，
  浏览器只挑选预先算好的记录（`.claude/rules/gold-sql.md` R3）。
- 无排班的 3 个分区（约占地址数 6.02%）给出一个答案而不是空卡片。
- 页面内列出“What this page is not”：不告诉你街道是否已清（并链到市政官方页面）·
  粒度是分区不是街道 · 只覆盖住宅街道 · 排班是计划不是记录 · 靠后不等于不公平。
- 不使用官方工具的名字，不让读者以为它知道当前状态（单测
  `test_the_page_does_not_present_itself_as_the_city_status_tool`）。

### 5.3 Zone map（`#/map`）

25 个分区的交互地图，悬停或 Tab 聚焦时点亮该分区的全部碎片，侧栏显示分区名、碎片数、
平均班次。无排班分区显示 `no residential schedule data`，地图上只画轮廓。几何来自
`FIG-BO4-00`，由 `make portfolio` 投影成 `zones.json`。

### 5.4 冻结时间

页面上的数不会自己刷新，所以页面必须说出它们冻结于何时，并且这个日期**从数据读取，
不得手写**：

- 页脚显示全部导出中最早的冻结日；多次冻结不一致时显示区间，不用一个日期代表多个。
- `#/zone` 在答案旁显示两条 lookup 查询各自的冻结时间与认证状态；两者来自不同运行时
  明说“可能不是同一个 Gold build”。
- `zones.json` / `lookup.json` 的导出缺失时删除旧文件而不是保留——浏览器里上周的副本
  与今天的无法区分。

## 6. 视觉与动效

视觉论文：**a civic data exhibit on a winter night**。深蓝黑底、雪白正文、冰蓝数据线、
少量琥珀色（`sodium`）用于限制与不确定性。字体：Big Shoulders Display / IBM Plex Sans /
IBM Plex Mono。排版接近展览海报；避免企业后台侧栏、密集 KPI 墙和通用 SaaS 卡片感。

Aceternity UI（JSX + Tailwind v4 + Motion 改写）的作用：

- Spotlight：Hero 视线引导。
- Tracing Beam：五幕的阅读进度。
- Sticky Scroll Reveal：Act III 三次“问题被数据改写”。
- Bento Grid：Act IV 工程能力拼贴，不作为第一屏导航。

图表全部是**手写 SVG**（`dashboard/src/charts.jsx`，`CHART_FOR` 映射 fig_id → 组件），
交互不能改变统计口径。动效不能锁死滚动，关闭动画时信息仍完整，尊重
`prefers-reduced-motion`（`MotionConfig reducedMotion="user"`）。正文至少 16px，常用
操作至少 44px 高，支持键盘与 200% 字体放大。移动端允许把复杂图表收敛成摘要，核心结论、
来源和 GitHub 入口不能缺失。

## 7. 页面、URL 与二维码

hash 路由，构建产物是一个可从任意路径部署的静态目录，不需要服务端 rewrite：

| 路由 | 页面 |
| --- | --- |
| `/`（含 `#act-1` 等锚点） | 英文叙事首页 |
| `#/zone` | Your zone |
| `#/map` | Zone map |
| `#/evidence`、`#/evidence/<fig_id>` | Evidence Explorer 与单条详情 |

导航：Story · Your zone · Evidence · GitHub（外链新标签打开，并有可访问性提示）。
公开入口必须是稳定 HTTPS URL，二维码指向首页，不携带个人信息或临时查询参数。核心文字和
GitHub 入口不等待大型图表加载。

不需要登录、持久化、上传、评论、实时 Trino 连接和管理后台。冻结 JSON
（`evidence.json` / `zones.json` / `lookup.json`）由 `make portfolio` 生成、随静态构建发布，
不进版本库。

## 8. 验收标准

### 内容

- 页面所有可见文字为英文；`dashboard/src` 与 `index.html` 不允许出现 CJK 字符，SQL 源码
  视图除外（`tests/unit/test_portfolio.py`）。
- 首页完成 §4 的七段，两个核心发现位于 AI/架构之前。
- 每个主数字都能追溯到指定 fig_id；25 条查询在证据库完整可达，角色标注正确。
- BO2-01 与 BO2-02 成对出现；BO6 的两种 profile 不共享色标；BO1-03 保留 nomonth 对照。
- 页面完整保留排班、空间份额、事件锚点、模型与排名位移的解释边界；“19 次作业”按 §3
  表述为公布的作业。
- `#/zone` 满足 §5.2 全部硬性要求。

### 体验

- 1440×900 桌面首屏同时呈现标题、项目问题、入口和一个代表性数据视觉。
- 390×844 手机视图能识别项目、读到核心问题并使用 GitHub/Evidence 入口。
- 访客从首页进入第一条发现不需要先操作菜单或理解项目术语。
- 主线阅读时不出现 SQL 表格；Evidence Explorer 能从任一精选图进入对应证据详情；从证据库
  返回故事锚点时定位正确。
- 无 JavaScript 时至少仍能看到项目标题、两条发现和 GitHub 链接；加载失败有英文回退内容。
- 页面标题、description、favicon、Open Graph 文本使用项目正式英文名称。

### 工程

- 数据目录从 `sql/presentation` 自动生成，不能手工维护第二份 fig_id 清单。
- 静态构建不包含凭据或生产连接；冻结数据保留逐图版本与认证。
- `npm run build`、`make lint` 与 dashboard 定向测试通过。

## 9. 实施状态与待修

§9 原“实施顺序”的 1–4 步已于 H1 前完成；第 5 步（公开托管与二维码）不在本篇范围。

回填时发现的两处实现缺陷，已于 2026-09-22 修正：

1. **D10**：Act IV Bento 的 “23 public SQL queries” 与证据库页头的 “25 queries: 19 / 4 / 2”
   原本都是手写的，现在都由 `data.jsx` 的 `useCatalogueCounts()` 从 `evidence.json` 计数。
   **页面文案里不再出现手写的查询条数。**
2. **D11**：Hero 图注、Act I 导语、`418 / 418` 标签、前后期比较句、`#/zone` 页头与
   `index.html` 的 noscript 回退，都改成“公布了班次表的作业”；Act I 的
   `How not to read this` 补了一句：2017、2023 两个整年没有公布记录，而这两年当然清过雪。

## 10. H2 预留：预测内容的接入规则

H2 的方向仍在 `20260921-h2-model-and-scoring-improvements.md` §1 留白，本节**不构成排期**，
只规定：如果 H2 的某条需求要把预测放上这个站点，必须满足什么。写在前面，是为了不在看到
结果之后再找说法。

### 10.1 可能进入页面的产出

| 需求 | 可能的页面产出 | 前置 |
| --- | --- | --- |
| H2-R13 自助区间 | 排名稳定性：“Zone X 在 82% 的副本里进前三”，替代裸名次 | 按事件整簇重采样 |
| H2-R1 / R2 对照与滚动评估 | Act V 的模型对照从单一留出季扩到多轮，报名次保持不变的轮数 | R2 是 R1 下结论的前置 |
| H2-R4 权重稳健性 | 在所有权重下都稳居前列的分区集合，名次位移分布 | 无 |
| H2-R11 前瞻链路 | 降雪前的低 / 中 / 高情景 | 首个有预报快照的冬季（2026-11 起） |

### 10.2 接入规则

1. 🔴 **回测与预测分开标注。** 用天气存档跑出的结果一律叫 backtest；只有用**当时真正
   可得的预报**产出的结果才能叫 forecast，并带 `issued at` 时间戳。页面不得用 forecast
   一词描述前者（H2-R11）。
2. 🔴 **不确定性与点估计同框。** 任何名次或预测数都带区间或稳定性比例；只报点估计等于
   假装名次没有抽样误差（H2-R13）。三种不确定性（抽样 / 模型设定 / 权重）不可互相替代。
3. 🔴 **`#/zone` 的第二个答案不能悄悄换成模型输出。** ADR 0013 §2.2 规定它是一条规则的
   回测命中率、不是模型输出。若 H2 要让模型回答“下次是不是轮到我优先”，先写新 ADR
   改判据，再改页面；页面上两种答案若并存，必须并列且各自带对照，不能让模型答案取代
   规则答案。
4. 🔴 **不越过“现在”这条线。** 前瞻情景讲的是一场尚未到来的雪的**需求估计**，不是清雪
   进度或当前状态。页面仍须把读者指回市政官方工具（ADR 0013 §3）。
5. **预测也走冻结与认证。** 每个预测产出都对应 `sql/presentation` 里的一条查询和一份冻结
   导出，带 `model_version`、冻结时间与认证状态；证据库为其新增一个角色（暂称
   `Forecast`），不并入 core 19。
6. **刷新节奏要先定。** 现在的站点是“冻结一次、长期不变”。前瞻情景过期很快，发布时必须
   决定重新冻结的频率与负责方；在没有定之前，页面上过期的预测比没有预测更糟——
   §5.4 的冻结时间规则对预测同样适用，且过期情景要明确标注或下线。
7. 禁语延续 §4.6 与 L3 launch §8：不说模型优于运营基线；`rank_delta > 0` 不是改进；
   `load_level` 不跨 profile 比较；不把分区级天气表述为影响调度建议（H2-R7 实测天气方差
   99.73% 在事件之间）。

### 10.3 放在哪里（待方向确定后再定）

两个候选，不在本篇决定：扩写 Act V 为“what the model can and cannot say”，或新增一页
（如 `#/outlook`）承载前瞻情景、首页只留入口。判据是：**首页的两条核心发现必须仍然
排在任何模型内容之前**（§8）。
