# UOIP 英文叙事型 Portfolio 需求

日期：2026-09-13。状态：已重估，待按本需求重构。

本篇取代同文件早先的“分析型 Dashboard”定位。现有 `dashboard/` 中冻结数据、SQL
目录、审计信息和 ECharts 接入可复用，但当前首页的信息架构不再作为目标设计。

## 1. 产品定位

这是 UOIP 的公开英文 Portfolio，是 conference talk 和项目展示的延伸。二维码是现场让
观众记住并带走项目入口的传播媒介，不预设他们当场在手机上完成阅读。主要使用场景是观众
在演讲后用电脑打开页面，一边理解故事，一边进入 GitHub、SQL 和实现代码继续核查。

GitHub README 与 Portfolio 的分工：

| 载体 | 类比 | 主要任务 | 主要读者 |
| --- | --- | --- | --- |
| GitHub README | résumé | 说明项目包含什么、怎样运行、去哪里读文档 | 招聘方、工程师、维护者 |
| Portfolio 首页 | portfolio | 让陌生访客记住问题、发现与项目判断力 | 会议观众、数据从业者、公共治理社区 |
| Evidence Explorer | appendix | 让读者检查数据、SQL、版本与解释边界 | 想复核结论的技术读者 |

首页不是实时运营工具，也不提供“我家街道清了没有”的查询。它展示的是一次可复核的
历史分析及其工程过程。

## 2. 受众与成功标准

读者不需要了解 Lakehouse、Trino、Gold、BO 或评分 profile。桌面端是主要阅读环境，适合
同时浏览 Portfolio、Evidence Explorer 与 GitHub 代码；移动端承担落地、快速理解和保存
入口的职责，内容仍须完整可读，但不以手机上的深度图表操作为主要体验。

阅读目标分三层：

1. **演讲现场：**观众从二维码和页面预览中认出项目名称、核心问题与网址，愿意稍后打开。
2. **桌面端 2 分钟：**理解两个核心发现：排班全覆盖不等于顺位相同；作业分区不能直接
   换成 ward。知道数据曾多次改写分析问题。
3. **桌面端深入阅读：**理解自托管数据管道、评分证据的限制和 AI 层的探索性质，并能从
   结论直接进入对应 SQL、数据与 GitHub 实现。

成功不以图表点击量衡量。验收时应由一位不了解项目的人在不读 README 的前提下复述：
研究问题、两条发现、数据来源的大类，以及项目没有证明什么。

## 3. 内容语言与语气

所有可见界面必须使用英文，包括导航、按钮、图题、图例、tooltip、空状态、错误信息、
可访问性标签、元数据和移动端菜单。`sql/presentation` 中的中文头注继续作为内部来源，
不能直接显示在公开页面。

英文语气应像清晰、克制的展览文字：短句、具体数字、少术语。可以表达好奇心和判断，
不能把相关性写成因果，把排班写成清雪完成时间，把模型实验写成运营改善。

建议的页面标题与开场文案：

> **What ten winters of open data reveal about who gets plowed first.**
>
> Winnipeg publishes snowfall records, service requests, and residential plow
> schedules. We joined them in a self-hosted lakehouse—and the data kept changing
> the question.

主操作：`Explore the story`。次操作：`View the source on GitHub`，指向
`https://github.com/huzhi-zhao/urban-ops-intelligence-platform`。

## 4. 叙事结构

首页是一页连续叙事。章节标题可以在实现时润色，但信息顺序和证据义务固定。

### 4.1 Opening — the question

桌面首屏同时出现项目名、主标题、两句说明、两个入口和一个能代表项目的数据视觉。宽屏
构图要像演讲海报的延伸，并为向下阅读留下明确线索。移动端首屏保留项目名、核心问题和
至少一个入口。首屏不出现查询数量、BO 编号、审计状态、SQL 搜索框或六主题导航。

首屏要提出可继续滚动的问题：

> If every residential zone is scheduled, what does “who gets plowed first”
> actually mean?

### 4.2 Act I — the wrong question

先揭示反直觉事实：19 次全市住宅区犁雪作业，每一次都覆盖 22 个排班分区。因此
“谁被跳过”不是数据支持的问题，故事转向“谁通常排得更早”。

主数字与视觉：

- `19 operations · 22 zones · zero missing schedule cells`
- Zone S 平均第 1.26 班，Zone C 平均第 3.47 班。
- 2.21 个班次约等于 26.5 小时的**计划开工偏移**。

主图使用 `FIG-BO2-01` 的排序条形与 min/max 须。紧接一个短反转：21/22 个分区都曾
进入首班，包括平均最晚的 C；`FIG-BO2-02` 展示 V 与 M 的前后期位移。读者离开本节时
应理解“长期平均有差异”和“顺位会移动”可以同时成立。

页面措辞必须使用 `scheduled position`、`planned start offset` 或 `scheduled shift`，
不能使用 clearing completion、actual wait 或 fairness verdict。

### 4.3 Act II — the map changes the meaning

第二条核心发现：工作按 plow zone 组织，公共讨论常按 ward 组织，而两种单元不能直接
互换。页面用一次由宏观到局部的视觉转场：25×15 的关系矩阵，再放大 Zone V。

必须出现：

- 只有 2/25 个作业分区的记录权重全部落在一个 ward 标签。
- 主导 ward 标签份额中位约 54%。
- Zone V 的工单横跨 10 个 ward 标签，最大一个约占 26%。

`FIG-BO4-01` 与 `FIG-BO4-02` 是主证据。这里的份额来自 2023-11 至 2026-05 冬季工单
标签计数，不是面积、人口、几何重叠或清雪工作量。页面不能画不存在的 ward 几何，
也不能把 “A work zone is not a ward” 表述成任何一套边界画错了。

### 4.4 Act III — the data kept rewriting the question

这一节体现项目的工程判断力，以紧凑的滚动卡片讲三个“原假设 → 数据事实 → 工程处置”：

1. `%ICE%` 的宽松关键词筛选有 99.8% 行级误伤，会命中 Police、Service、Notice 等词；
   因此建立可审计的类别映射。
2. 单日降雪阈值不足以表达连续小雪；事件规则采用“单日阈值 OR 十日累计”，99 个事件中
   8 个只因累计判据成立。
3. 评分的名义权重没有决定实际展幅；数据要求同时展示因子的实测范围。

本节可以引用 `FIG-BO1-04`、`FIG-BO3-00/01` 和 `FIG-BO6-02`，但每张只服务一个故事
转折，不构成查询目录。case ID 非唯一可作为较长版本的补充卡，首版不承担主线。

### 4.5 Act IV — from pipeline to evidence

用一张简化的端到端流向解释项目能力：Winnipeg Open Data / Open-Meteo → Bronze →
Silver → Gold → findings。第二层再标注 MinIO、Spark、Airflow、Hive Metastore、Trino。

这一节先回答“为什么需要这套系统”：需求、天气、排班和边界分散在不同端点、不同时间
与空间粒度；再回答“工程做了什么”：可重放摄取、显式 schema、事件切分、空间归属、
质量审计、可执行 SQL。技术栈名称不能取代成果叙述。

允许复用 `docs/images/urban-ops-oneline-light-transparent.jpg` 和
`docs/images/platform-architecture.svg`。完整架构图可在桌面端展开查看；页面主线仍使用
专用于网页的简化版本，以免读者必须放大图片才能理解数据流。

### 4.6 Act V — an honest AI layer

结尾展示模型和推荐层为什么有用、证据又到哪里为止：

- 评分面板有 1,298 格；374 格拥有需求、天气和排班三类证据，924 格没有匹配的排班
  证据，占 71.2%。两个 profile 必须分开表达。
- 留出季只有 7 个事件、154 格；M1 MAE 7.345、去月份特征对照 7.919、弱基线 23.628，
  三者必须同框。
- 两个模型版本都产生 188 格上移；rank displacement 是实验输出，不是效果改善。

本节使用 `FIG-BO6-01`、`FIG-BO1-03`、`FIG-BO8-01`。措辞应强调 auditable experiment、
visible assumptions 和 open evidence。不能宣称模型优于合适的运营基线，不能把历史天气
特征上的回测描述成真正的暴风雪前预测。

### 4.7 Closing — disagree with the project using its own inputs

最终 CTA 延续演讲摘要的承诺：

> **Everything is public data and open code.**
>
> Inspect the assumptions, rerun the queries, and disagree with the project
> using its own inputs.

提供两个清楚的出口：`Explore all evidence` 和 `View the GitHub repository`。不增加联系
表单、订阅、账号或泛化的“Get started”。

## 5. Evidence Explorer

完整证据是 Portfolio 的辅助层。可以使用 `/evidence` 路由，或在静态站限制下使用明确的
二级视图；浏览器前进/后退应可恢复位置。

Evidence Explorer 必须收录 `sql/presentation` 当前 23 条查询，其中原始 19 条标为核心
证据，后增 4 条标为解释视图。它提供英文标题和图注、图表、原始数据、SQL、冻结时间、
认证状态与解释边界。支持按故事章节筛选和搜索；BO 编号、carrier、schema 等只放在技术
元数据里。

首页只精选支撑叙事的 8–10 张证据，不承诺把 23 张图全部画在主线中。Evidence Explorer
不得出现“专用图形待补”这样的开发提示；未完成的专用图在公开发布前要么实现，要么以
清楚、可读的证据表呈现，界面称 `Data view`。

SQL 的 `criterion/caption/must_not_say` 仍是口径来源。需要维护一份人工核对的英文内容
层，不能在浏览器中机翻。每项公开数字绑定自己的冻结结果、认证和来源；测试 fixture 必须
标为 sample，不能显示为 production certification。

## 6. 视觉与动效

视觉论文：**a civic data exhibit on a winter night**。深蓝黑底、雪白正文、冰蓝数据线、
少量琥珀色用于限制与不确定性。排版接近展览海报，有清楚的章节节奏、强数字和宽幅数据
图；避免企业后台侧栏、密集 KPI 墙和通用 SaaS 卡片感。

Aceternity UI 的作用：

- Bento Grid 用于工程转折或能力拼贴，不作为第一屏的导航菜单。
- Sticky Scroll Reveal 可用于三次“问题被数据改写”的顺序叙事。
- Spotlight/Tracing Beam 只承担视线引导。
- 图表仍由 ECharts 或专用 SVG/Canvas 承担，交互不能改变统计口径。

桌面端可以使用宽幅图表、并排证据与 sticky narrative，让读者在故事和代码之间往返。
动效不能锁死滚动，关闭动画时信息仍完整，并尊重 `prefers-reduced-motion`。正文至少
16px，常用操作至少 44px 高，支持键盘与 200% 字体放大。移动端允许把复杂图表收敛成
摘要、关键标记和 `Open evidence on desktop` 提示；核心结论、来源和 GitHub 入口不能缺失。

## 7. 页面、URL 与二维码

公开入口必须是稳定 HTTPS URL，二维码直接指向首页，不携带个人信息或临时查询参数。
二维码的目的，是让现场观众拍照、收藏或稍后转到电脑继续浏览。页面仍应在常见移动网络下
快速出现；核心文字和 GitHub 入口不等待大型图表运行时加载。

站点至少需要：

- `/`：英文叙事型 Portfolio。
- `/evidence`：英文证据库。
- GitHub 外链：新标签打开并有可访问性提示。

不需要登录、持久化、上传、评论、实时 Trino 连接和管理后台。冻结 JSON 随静态构建发布。

## 8. 验收标准

### 内容

- 页面所有可见文字为英文；自动化检查不允许出现 CJK 字符，SQL 源码视图除外。
- 首页完成 §4 的七段故事，两个核心发现位于 AI/架构之前。
- 每个主数字都能追溯到指定 fig_id；19 条核心 SQL 与 4 条解释 SQL 在证据库完整可达。
- BO2-01 与 BO2-02 成对出现；BO6 的两种 profile 不共享色标；BO1-03 保留 nomonth 对照。
- 页面完整保留排班、空间份额、事件锚点、模型与排名位移的解释边界。

### 体验

- 1440×900 桌面首屏同时呈现标题、项目问题、入口和一个代表性数据视觉；正文宽度适合
  持续阅读，并支持从精选结论直接打开对应证据和 GitHub 代码。
- 390×844 手机视图能识别项目、读到核心问题并使用 GitHub/Evidence 入口；复杂图表可以
  提供简化摘要，不以完成全部探索交互作为移动端验收标准。
- 访客从首页进入第一条发现不需要先操作菜单或理解项目术语。
- 主线阅读时不出现 SQL 表格；Evidence Explorer 能从任一精选图进入对应证据详情。
- 无 JavaScript 时至少仍能看到项目标题、两条发现和 GitHub 链接；加载失败有英文回退内容。
- 页面标题、description、favicon、Open Graph 文本使用项目正式英文名称；社交预览图片仅在另行
  明确要求时制作。

### 工程

- 数据目录从 `sql/presentation` 自动生成，不能手工维护第二份 fig_id 清单。
- 静态构建不包含凭据或生产连接；冻结数据保留逐图版本与认证。
- `npm run build`、`make lint` 与 dashboard 定向测试通过；全量测试若仍受现有在线 API
  波动影响，必须单独报告，不能修改契约来使其变绿。
- 浏览器验收以桌面完整阅读、Evidence Explorer 与 GitHub/SQL 跳转为主，同时覆盖移动端
  落地、键盘操作、减少动画和数据加载失败状态。

## 9. 实施顺序

1. 将现有分析型首页移至 Evidence Explorer，并把全部界面文案改为人工核对的英文。
2. 建立英文叙事首页的 Opening、Act I 和 Act II，先完成可识别的核心体验。
3. 增加数据改写问题、架构、AI 边界与 Closing；逐项绑定冻结证据。
4. 完成桌面端精选图、证据与代码跳转；为移动端补简化摘要、元数据与无障碍状态。
5. 完成构建、内容口径与浏览器验收，再决定公开托管和二维码制作。

部署与二维码生成不在本次需求重写中自动执行。
