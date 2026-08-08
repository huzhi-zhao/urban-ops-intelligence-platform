# 温尼伯（Winnipeg, MB）本地数据源调研

> **v2 — 2026-07-29**
> v1（2026-07-28）为门户元数据调研；v2 增加了**实际调用 SODA API 的实测结果**，
> 并据此修正了 v1 中若干推断性表述（见 §3.1、§3.2 的勘误标注）。
> 凡标注 **【实测】** 的数字来自真实 API 调用，可复现；**【元数据】** 来自门户 catalog 接口。
>
> 📌 **本篇只回答「门户上有什么」。** 「我们采纳哪些、哪些留给 H2、启用一个
> H2 预备源要先做什么」在 [data-source-portfolio.md](data-source-portfolio.md)。
> 本篇变更的触发是**上游数据集变了**，那篇是**采纳决策变了**。
> 在本篇里新发现一个数据集，不等于它被采纳——采纳要过那篇 §6 的五问探针。

---

## 1. 背景与约束

Data Acquisition 课程老师在结课时单独给出的建议，可归纳为四个要素：

| 要素 | 含义 | 对数据源的要求 |
|---|---|---|
| **Local** | 使用 Winnipeg / Manitoba 本地数据 | 必须来自本地政府或本地机构 |
| **Big Data Engineering** | 数据开发项目，不是纯 ML 玩具 | 体量足够撑起分层/分区/增量/调度等工程议题 |
| **Real Pain Point** | 能勾勒本地核心痛点的真实需求 | 议题在本地有公共讨论度 |
| **Conference** | 可宣讲，导师可能联名 | 结论要有可迁移性，不能只是"我做了个管道" |

隐含的第五点：老师本人可能希望从这类项目积累其晋升所需的产出指标，
因此产出需要对他也可署名、可展示。

> **项目范围 ≠ 论文范围。**
> 项目实现可以支持多城市；论文选题在最终确立时再收窄到老师期待的尺度。
> 反过来（先删掉既有城市实现、再想要跨城市论证）不可逆。

---

## 2. 关键发现：温尼伯门户与既有实现同为 Socrata 平台

- 门户：https://data.winnipeg.ca/
- 平台：**Socrata**（与 `data.cityofnewyork.us` 完全相同的技术栈）
- API：SODA，端点与 SoQL 语法一致 **【实测确认】**
- 授权：Open Government Licence – Winnipeg / Canada OGL

### 2.1 对现有代码库的意义

Bronze 摄取层**几乎可以零改动复用**：

| 现有模块 | 复用程度 |
|---|---|
| `ingestion/clients/socrata_client.py` | 完全复用，仅切换 `domain` |
| `ingestion/backfill/fetchers/socrata.py` | 完全复用 |
| `ingestion/backfill/facade.py` | 完全复用（daily / monthly / static 三种策略均适用） |
| `ingestion/loaders/gcs_loader.py` | 完全复用（建议在路径中增加 `city` 维度） |
| `scripts/backfill/` 三层架构 | 完全复用（registry 自动发现，新增源 = 丢一个文件） |
| `config/sources/*.yaml` | 新增温尼伯 YAML 即可 |

已核查：上述核心模块中的城市专有名词**全部位于 docstring 与注释**，
逻辑代码无任何硬编码城市。架构本身即城市无关，与平台的 UOIP 定位一致。

### 2.2 对论文选题的意义

> **一个城市无关（city-agnostic）的城市运营 Lakehouse 架构，
> 在两个规模、气候、治理结构截然不同的城市上验证 ——
> New York City（约 840 万人）与 Winnipeg（约 75 万人）。**

这个定位把既有实现从「遗留包袱」变成**可移植性的实证基线**，
并带出对本地有意义的次级论点：*小城市缺乏大都市那样的数据工程资源，可移植架构正是解法。*

---

## 3. 311 Requests 实测剖析 —— `u7f6-5326`

本节全部为 **2026-07-29 实际调用 SODA API 的结果**，是本文档最重要的部分。

### 3.1 真实字段清单【实测】

Socrata 对空值字段直接省略，故不同记录返回的字段数不同。

| 字段 | 类型 | 填充率 |
|---|---|---|
| `case_id` | text（哈希串） | 100% |
| `interaction_id` | text | 100% |
| `channel_type` | text | 100% |
| `subject` | text | 100% |
| `reason` | text | 100% |
| `type` | text | 100% |
| `open_date` | floating timestamp | 100% |
| `closed_date` | floating timestamp | 100% |
| `case_status` | text | 100% |
| `neighbourhood` | text | **20.9%** |
| `ward` | text | **20.9%** |
| `geometry` | point | **20.9%** |
| `:@computed_region_6rfj_69jf` | 计算区域 | 20.9% |
| `:@computed_region_38v8_cedi` | 计算区域 | 20.9% |

> ⚠️ **v1 勘误**：v1 依元数据列出的字段大体正确，但**未发现地理字段的严重缺失**。详见 §3.5。

### 3.2 规模与时间范围【实测】

| 指标 | 值 |
|---|---|
| 总行数 | **18,346,621** |
| 时间范围 | **2008-06-17 13:52:55 ~ 2026-07-28 01:01:25**（18 年） |
| `type` 不同取值数 | **3,563** |
| 更新频率 | 每日 |

> ⚠️ **v1 勘误**：v1 依元数据写「约 16,680,000 行」，实测为 **18,346,621**。数据仍在增长。

### 3.3 三层分类结构【实测】

理解这三个字段的真实语义至关重要。

**第一层 `subject`（业务性质）**

| subject | 行数 | 占比 |
|---|---|---|
| Information Request（纯咨询，无派单） | 11,157,055 | 60.8% |
| Service Request（真实工单） | 7,089,795 | 38.6% |
| VOF | 99,771 | 0.5% |

**第二层 `reason` —— 注意：这是「部门」不是「投诉类型」**

v1 曾误以为 `reason` 是投诉原因。实测显示它是**责任部门**。
以下为 `subject = 'Service Request'` 的分布：

| reason（部门） | 行数 | 占比 |
|---|---|---|
| Water and Waste | 2,158,700 | 30.45% |
| Public Works | 1,363,354 | 19.23% |
| Community Services | 1,009,575 | 14.24% |
| Parking Authority | 573,666 | 8.09% |
| Transit - Winnipeg Transit | 486,245 | 6.86% |
| Corporate Support Services | 481,319 | 6.79% |
| Animal Services Agency | 340,219 | 4.80% |
| Assessment and Taxation | 290,051 | 4.09% |
| Planning, Property and Development | 135,895 | 1.92% |
| City Clerks | 83,936 | 1.18% |
| Police - Winnipeg Police Service | 36,101 | 0.51% |
| Golf Services | 33,120 | 0.47% |
| Corporate Finance | 31,659 | 0.45% |
| Fire Paramedic Service | 29,822 | 0.42% |
| Non City Services | 19,214 | 0.27% |
| Winnipeg Transit Plus | 13,660 | 0.19% |
| Accessibility | 2,279 | 0.03% |
| Community Development | 868 | 0.01% |
| The Office of Sustainability | 72 | 0.00% |
| Flood | 40 | 0.00% |

**第三层 `type`（真正的工单类型，3,563 个取值）**

这是分析的实际粒度。**其命名内嵌了官方的优先级与时段口径**：

```
Snow Removal Street Priority 1 Reg          Sanding Request Priority 2 After
Snow Removal Sidewalks Priority 3 After     Frozen Catch Basin Priority 1 Reg
Snow Removal High Piles Pr 2                Snow Removal Request Sidewalk After Hours P1
Snow Removal Back Lanes                     Snow Removal Front Approach After Hours
Snow Removal Windrow Inquiry                Snow Removal- Snow Plow Damage
Accessibility Snow Clearing Application     Parked Vehicles Impeding Snow & Ice Control Op's
```

> 🔑 **最重要的发现之一**：`Priority 1/2/3`（对应官方 P1/P2/P3 除雪分级）
> 与 `Reg` / `After (Hours)`（常规 / 非工作时间）**直接编码在 type 字符串里**。
> 意味着可以用**官方自己的口径**做 SLA 合规性分析，无需自行定义标准。
> 官方分级政策见 https://winnipeg.ca/publicworks/snow/snow-clearing-policy.stm
>
> 同时 3,563 个取值本身即是工程议题：需建立类型字典与归一化映射，
> 注意 `Pr 2` / `Priority 2` / `P2` 多种写法并存，以及 `_vof` 后缀变体。

### 3.4 冬季运营相关占比：1.50%【实测】

**这是选题的核心质疑点，必须诚实记录。**

按 `type` 关键词精确匹配（SoQL `upper(type) like`，已排除 `Serv-ice`/`Pol-ice` 之类误伤）：

| 关键词 | 行数 |
|---|---|
| `%SNOW%` | 188,651 |
| `%FROZEN%` | 61,108 |
| `%PLOW%` / `%PLOUGH%` | 30,365 |
| `%SANDING%` | 24,685 |
| `%WINDROW%` | 10,276 |
| `%ICE CONTROL%` | 2,212 |
| **并集（去重后）** | **275,243** |
| 其中 `subject = 'Service Request'` | 244,247 |

**275,243 / 18,346,621 = 1.50%**

结论：**除雪确实只占 311 的极小比例**。但见下述三个反转。

### 3.5 反转一：地理字段填充率彻底改变分母【实测】

| 范围 | 有 `neighbourhood` | 填充率 |
|---|---|---|
| 全表 18,346,621 | 3,832,509 | **20.9%** |
| 冬季相关 275,243 | **220,580** | **80.1%** |

（`ward` 3,832,617 / `geometry` 3,833,344，与 `neighbourhood` 基本一致）

**原因**：占 60.8% 的 Information Request 是电话咨询，无地址；只有真实派单才带位置。

**推论**：

- 任何需要空间维度的分析，**有效分母是 383 万而非 1,835 万**
- 在该分母下冬季工单占 **5.8%**，且属填充质量最好的一类
- 冬季工单的地理可用性（80.1%）是全表平均（20.9%）的 **3.8 倍**

> 注意：CLAUDE.md 的「Escalate to human when」规定
> 「`dim_geography` 空间连接 NULL 超过 10% 需上报」。
> 本数据集全表 79% 无地理信息属**上游固有特性而非管道缺陷**，
> 需在 Silver 层显式标记可用性，不可当作异常告警。

### 3.6 反转二：18 年历史 + 4.4 倍年度波动【实测】

带地理信息的冬季工单按年分布：

| 年 | 数量 | 年 | 数量 |
|---|---|---|---|
| 2009 | 7,647 | 2018 | 10,763 |
| 2010 | 8,485 | 2019 | 11,117 |
| 2011 | 11,405 | 2020 | 10,211 |
| 2012 | 6,428 | 2021 | 10,762 |
| 2013 | 19,384 | 2022 | **28,006** |
| 2014 | **27,624** | 2023 | 11,110 |
| 2015 | 8,214 | 2024 | 7,104 |
| 2016 | 13,639 | 2025 | 6,341 |
| 2017 | 15,589 | 2026 | 6,751（半年） |

最高 28,006 / 最低 6,341 = **4.4 倍波动**。
**这个波动正是研究信号**——应与降雪严重程度强相关，是建模的因变量基础。
18 个冬季 × 平均约 1.2 万条，足以支撑事件研究与时序建模。

### 3.7 反转三：22 万行对「分析」从不算小

275K（带地理 220K）不足以炫耀 Spark 集群，但对统计建模绰绰有余。
真正的解法见 §8 —— **管道范围 ≠ 分析范围**。

### 3.8 ⚠️ 已确认的数据质量断层：渠道分类体系迁移【实测】

**(a) 工单总量下降是全局现象，非除雪特有**

| 年 | 总量 | 年 | 总量 |
|---|---|---|---|
| 2008 | 330,251（半年） | 2018 | 942,882 |
| 2009 | 1,251,127 | 2019 | 930,614 |
| 2010 | 1,250,972 | 2020 | 711,295 |
| 2011 | 1,341,452 | 2021 | **556,156**（谷底） |
| 2012 | 1,579,445 | 2022 | 614,411 |
| 2013 | **1,618,563**（峰值） | 2023 | 593,369 |
| 2014 | 1,294,813 | 2024 | 625,707 |
| 2015 | 1,200,290 | 2025 | 777,829 |
| 2016 | 1,115,224 | 2026 | 503,874（半年） |
| 2017 | 1,108,347 | | |

峰谷降幅 **-66%**，近年回升。**除雪工单的下降跟随大盘**，
排除了「除雪类目被重新分类」这一最坏情况。

**(b) 但存在真实断层：数字渠道在 2022 年后归零**

| channel_type | 2021 | 2023 | 2025 |
|---|---|---|---|
| Self Service | 9,514 | **0** | **0** |
| Mobile | 2,572 | **0** | **0** |
| SMS In | 476 | **0** | **0** |
| **VOF** | 3,910 | 33,784 | **91,202** |
| Voice In | 423,727 | 442,653 | 603,851 |
| e-mail In | 71,515 | 68,011 | 46,402 |
| Face2Face | 24,149 | 24,085 | 14,300 |
| Social Media | 17,464 | 20,061 | 12,184 |

全表 `channel_type` 取值（共 15 种）：
Voice In 16,223,816 · e-mail In 910,466 · Face2Face 364,109 · **VOF 279,248** ·
Self Service 252,574 · Social Media 224,200 · SMS In 37,106 · Mobile 15,973 ·
Dept Create Case 12,531 · Voice Out 11,917 · e-mail Out 10,867 ·
BSC Face to Face 3,575 · Mail In 190 · WAP 35 · Unknown 14

**判定**：三个数字渠道同时归零、VOF 同期暴涨 23 倍，是**记录口径迁移**而非用户行为变化。
佐证：`type` 存在大量 `_vof` 后缀变体（`Snow Removal Street P3_vof`、
`Frozen Catch Basin P3_vof`、`Sanding Street/Roadway P2_vof`），
且 `subject` 亦有独立的 `VOF` 取值（99,771 行）。

**影响与对策**：

| 影响面 | 结论 |
|---|---|
| 2022 前后**渠道结构**对比 | ❌ 不可直接比较 |
| **总量与类型**分析 | ✅ 仍可比（工单未丢失，仅标签变更） |
| Silver 层处理 | ⚠️ 需建立渠道归一化映射（`Self Service + Mobile + SMS In → VOF`） |
| 论文价值 | ✅ **municipal open data 的 taxonomy drift 实证案例** |

### 3.9 行粒度是 interaction 不是 case，去重键为复合键【实测 2026-08-02】

`case_id` **不唯一**，这推翻了「`case_id` 是主键」的推断：

| 项 | 实测值 |
|---|---|
| 总行数 | 18,361,362 |
| `count(distinct case_id)` | 18,018,296（**343,066 行重复，1.87%**） |
| `count(distinct interaction_id)` | 15,649,799（重复更多） |
| `(case_id, interaction_id)` | **无重复 —— 唯一键** |

抽查重复组后语义清楚：**一个 `case_id` 是一个服务案件，一个 `interaction_id`
是一次交互**（市民就同一件事多次来电 / 多渠道联系）。同组各行的
`open_date` / `closed_date` / `type` 完全相同，差异只出现在
`interaction_id`，偶尔还有 `channel_type` / `neighbourhood` / `geometry`。

```
case_id 00005d36…  n=5   同一个 "Pothole May 16 Priority 2 Reg"
  interaction_id 101000824716 / 101000829046 / 101000692093 / …
  open_date、closed_date、type 五行完全一致
```

**结论与影响**：

- **Silver 去重键 = `(case_id, interaction_id)`**，7 天回溯窗口据此去重，
  不会累积重复。单用 `case_id` 会误删 1.87% 的真实交互记录。
- **§3.4 的 275,243 是行数（interaction 粒度）**，不是案件数。
  Silver 保持 interaction 粒度，该数字才复现得出来。
- **「工单量」这个词在 Gold 层必须区分口径**：按行 = 联系次数（需求压力），
  按 `distinct case_id` = 案件数（工作量）。两者相差约 1.9%，
  BO 的度量定义里要写清用哪个。

---

## 4. 供给侧数据实测 —— 资源调度的关键

**仅靠投诉无法做资源调度**：投诉是结果变量，不是驱动变量。
以下为供给侧（实际作业）与驱动侧（气象）的实测结果。

### 4.1 `tix9-r5tc` Plow Zone Schedule —— 排班**计划**表 ⚠️

| 属性 | 值【实测】 |
|---|---|
| 行数 | 418 |
| 时间范围 | **2015-12-19 19:00 ~ 2026-02-28 07:00** |
| 不同 `snow_ban_id` | **19**（即 11 年间 19 次住宅区犁雪事件） |
| 不同 `plow_zone` | **22** |
| 字段 | `id`, `snow_ban_id`, `shift_number`, `shift_start`, `shift_end`, `plow_zone` |

```json
{"id":"1","snow_ban_id":"17567254","shift_number":"1",
 "shift_start":"2015-12-19T19:00:00.000","shift_end":"2015-12-20T07:00:00.000","plow_zone":"D"}
```

**这是整个调研中最有价值的供给侧数据**——但它记录的是**计划**，不是执行结果。

#### 4.1.0 结构实测【2026-08-07】：它是计划表，不是执行记录

> 🔴 此前本节（与 ADR 0007）把 `shift_end` 当作「实际作业结束时刻」。
> 对全表 418 行做结构实测后，该判断**不成立**。

| 实测 | 值 |
|---|---|
| 每个 `snow_ban_id` 的行数 / 分区数 | **恰好 22 / 22**，19 个事件无一例外 |
| 每个事件的总窗口 | **固定 60 小时**（07:00→19:00+2d 或 19:00→07:00+2d） |
| 班次结构 | 22 个分区分进 **5 个班次**，每班整 12 小时 |
| 同一班次内各分区的 `shift_start` / `shift_end` | **完全相同** |

三条推论：

1. `shift_end` 是**计划**班次结束时刻。表中不含出车、完成确认或延误记录。
2. **分区之间在"完成时间"上方差为零**，「哪个区清得慢」在这份数据上无法回答。
3. 真正可问的是 **`shift_number` 排班顺位**：谁排第一批，谁排最后。

各分区平均顺位（19 次事件）：**S 区 1.26 ~ C 区 3.47**，极差 2.2 个班次 ≈ 26 小时，
十年十九次二十二分区零缺失。与分区地址数的相关系数 **r = +0.491**
（脚本 `scripts/analysis/zone_schedule_rank.py`）。

> ⚠️ **本表只记录会发停车禁令的全市住宅区集中犁雪**（十年 19 次，每冬不到 2 次）。
> 主干道日常维护、撒砂、局部清理**根本不在表内**。因此
> **「无排班记录」推不出「没干活」**——原 BO-6 的「作业缺口」因子据此删除。
>
> 决策与被否决的替代方案见
> [ADR 0008](../adr/0008-plow-schedule-is-a-plan-not-a-record.md)。

#### 4.1.1 为什么落 `static` 而不是 `monthly`【决策 2026-08-02】

418 行 / 十年 19 次作业，按月切会产出 120 多个分区，其中绝大多数是空的——夏季不清雪。
而 `dag_audit_bronze` 判定「这个月的 manifest 不存在」就会调 `bulk.py` 去回填，
回填拉不到记录、不写 manifest，**于是每天报一次永远修不掉的缺口**。
`static` 源不进审计（无时间维度），且每次拉取都是全表，不会漏。

代价：上游若改写历史，我们的副本会被覆盖。这是只增的历史记录表，风险可接受；
真出现改写，迁移路径是 `snapshot` 而不是 `monthly`。

> ⚠️ 这个选择在实现上不是免费的。平台此前没有 `static` + 普通 Socrata 的组合
> （原有的 `static` 源都是 GeoJSON），而 `static` **禁止** `timestamp_field`、
> 窗口式 Socrata fetcher 又**必须**有 —— 两层配置直接打架，实测时表现为
> `missing timestamp_field` 取不到任何数据。已在批 3 修复：`build_fetcher`
> 现在接收数据集的**生效分区策略**，`static` 走全表拉取（`$order=:id`）。

### 4.2 `mfzv-893p` Snow Parking Bans —— 决策事件日历 ✅

| 属性 | 值【实测】 |
|---|---|
| 行数 | **仅 49** |
| 字段 | `id`, `description`, `description_french`, `ban_type_id`, `ban_start`, `ban_end`, `residential_ban_id` |
| 禁令类型 | `ANNUAL SNOW ROUTE`（年度）· `EXTENDED SNOW ROUTE`（延长）· `RESIDENTIAL (KNOW YOUR ZONE)`（住宅区） |

```json
{"id":"17567254","description":"RESIDENTIAL (KNOW YOUR ZONE)","ban_type_id":"4",
 "ban_start":"2015-12-19T19:00:00.000","ban_end":"2015-12-22T07:00:00.000",
 "residential_ban_id":"17558567"}
```

通过 `id` ↔ `tix9-r5tc.snow_ban_id` 可与排班表关联。
**这是市政的决策记录**：何时判定降雪严重到需要发布禁令。

#### 4.2.1 🔴 关联完整性单向成立【实测 2026-08-02】

拉全表逐一比对，两个方向的结论不同：

| 方向 | 结论 |
|---|---|
| 排班 → 禁令 | ✅ 无孤儿。`tix9-r5tc` 的 19 个 `snow_ban_id` 全部能在本表 49 个 `id` 中找到 |
| 禁令 → 排班 | 🔴 **49 次禁令里只有 19 次有班次记录，30 次没有** |

**这 30 行不是缺失数据。** 禁令是「发布」，班次是「执行」，两者本就不是一一对应：
`ANNUAL SNOW ROUTE` 一发布就覆盖整个冬季，本身不产生分区级班次。
`ban_type_id` 只有 `1` / `2` / `4` 三个取值（**没有 3**），按它分开统计即可区分。

Gold 层的处置口径与 §7.2 的「无排班分区」同类：**不能让它退化成响应时长指标里的
静默 NULL**。契约见 `contracts/api-contracts/winnipeg-parking-bans.yaml`。

另有一处坑：`residential_ban_id` 的 `"0"` 是「无」的哨兵值，不是指向 id=0 的行，
不要拿它做联结。

### 4.3 `g3p4-h83y` 地址级清雪状态 —— ⚠️ 只有快照，无历史

| 属性 | 值【实测】 |
|---|---|
| 行数 | 237,867（≈ 地址 × 街道侧） |
| 字段 | `address_id`, `address`, `address_number`, `street_name`, `street_type`, `postal_code`, `street_side`, **`has_street`**, **`has_alley`**, **`has_walk`**, `location`(Point), `street_name_cap` |
| **时间字段** | **无 —— 一个都没有** |

`has_street` / `has_alley` / `has_walk` 是**布尔型的当前清雪状态**。

> 🚨 **重大约束**：该数据集是**纯快照**，覆盖式更新，不保留历史。
> 想做回溯分析，**必须自建每日快照采集**。
>
> 但这同时是机会：自建快照等于**创造一个此前不存在的纵向数据集**，
> 论文中可明确作为贡献声明
> （"we construct the first longitudinal record of address-level clearing status"）。
> 落 Bronze 用 **`partition_strategy: snapshot`**——按采集日而非记录日期分区。
> `daily` 强制要求 `timestamp_field` 而本数据集一个时间字段都没有，`static`
> 的单一文件名会次日覆盖前一日。见 [ADR 0006](../adr/0006-storage-compute-query-stack.md) §2.2。
>
> **越早上线越好——数据只能从上线当天开始攒。**

> 🚨 **本数据集不能承担"清雪完成时间"这一角色。** 它没有时间字段，历史需自建，
> 因此在采集积累出一个完整冬季之前无法回答"何时清完"。
>
> 🔴 **而 `tix9-r5tc` 也承担不了**（2026-08-07 修正）：它是排班计划表，
> `shift_end` 是计划值，同班次分区完全相同（见 §4.1.0）。
> **"清雪完成时间"这个量在门户上任何粒度都不存在**，摘要表述已相应改为
> **排班顺位**，见 [ADR 0008](../adr/0008-plow-schedule-is-a-plan-not-a-record.md)。
> 这也正是本数据集作为 BO-7 贡献声明的意义所在。
>
> 顺带一提：**求"各分区地址数"这个归一化分母不需要历史**——一次全量拉取即可，
> 与快照采集共用同一次调用。已用于 §4.1.0 的顺位交叉验证。

### 4.4 `rsyj-x68c` Cost of Road Maintenance —— ⚠️ 待解决

| 属性 | 值【实测】 |
|---|---|
| 行数 | 2,862 |
| 内容 | City of Winnipeg Adopted Operating Budget By Service (and Sub Service)，逐年合并 |
| 问题 | **JSON 端点返回空对象 `{}`**，字段未序列化 |

需改用 CSV 导出或检查列名特殊字符。若取到，可支撑「预算 vs 实际服务水平」分析
（呼应 CBC 报道的 2023 年除雪超预算 420 万加元）。

### 4.5 Open-Meteo 历史降雪 —— 驱动变量 ✅【实测通过】

温尼伯坐标 `49.895, -97.138`，Archive API 实测正常：

```
date         snowfall_cm   tmin     tmax
2022-01-04       3.01     -22.7   -14.6
2022-01-07       1.12     -34.1   -16.7
2022-01-08        2.1     -21.2    -9.3
```

字段：`snowfall_sum`（cm）、`temperature_2m_min/max`（°C），日粒度，历史存档。
**本项目已有 Open-Meteo client（`SRC-Open-Meteo`），零成本接入。**

### 4.6 供给侧规模的诚实评估

| 侧 | 规模 |
|---|---|
| 需求侧（投诉） | 275,243 条（220,580 带地理） |
| 供给侧（作业） | **49 个禁令 + 418 条排班 + 19 次住宅区犁雪** |

供给侧**远比需求侧稀疏**，这直接决定了分析设计必须调整 —— 见 §5。

---

## 5. 修正后的项目设计：从「投诉驱动」改为「事件驱动」

v1 设想的「降雪 → 311 投诉 → 清雪完成」链条方向正确，
但**分析单元必须改变**：不是「单条投诉」，而是**「降雪事件 × 分区/社区」**。

```
Open-Meteo 降雪量(cm)  →  是否/何时发布禁令  →  排班执行         →  投诉响应
   驱动变量                 决策变量             供给变量            结果变量
  (日粒度, 18年)         (mfzv-893p, 49)    (tix9-r5tc, 418)   (311, 22万带地理)
```

**观测单元数**：49 事件 × 22 分区 ≈ 1,000；再展开到 neighbourhood 维度可进一步细分。
这是标准的 **event study** 设计。

**关键优势**：该设计**不依赖投诉量大**。投诉仅用于度量「市民感知的服务缺口」，
而非主要预测变量。这正面回应了「除雪投诉只占 1.5%」的质疑。

### 5.1 官方工具的边界 —— 必须避开的坑

市政府**已经做了显而易见的那个产品**：

- **Know Your Zone** app（[Google Play](https://play.google.com/store/apps/details?id=ca.winnipeg.pwd.KnowYourZone) + Chrome 扩展）
- **近实时除雪进度地图**（[官方介绍](https://www.winnipeg.ca/people-culture/our-city-our-stories/new-map-you-track-snow-clearing-progress)）

> 🚫 **不要做「除雪状态查询地图」** —— 那不是烂大街，是与官方产品直接重复。

**饱和度调研结论**：未发现温尼伯除雪数据分析的 GitHub 项目
（只找到多伦多的 `Plowing-Pandas-Toronto`）；学术侧只有一篇偏城市规划的硕士论文
*Urban Winter: Applying Winter City Planning Principles*，非数据方向。
本地有 Open Data Hackathon 2017 与 Open Data Day 年度 Datathon，
社区活跃但产出以小工具为主。**分析 / 数据工程方向未饱和。**

### 5.2 官方不做的业务需求（每条均有数据支撑）

官方工具回答「我家这条街清了没」（当前状态、单点查询）。以下均超出其范围：

1. **SLA 合规性审计** —— 利用 `type` 内嵌的官方 P1/P2/P3 与 Reg/After 口径，
   对比 `open_date` → `closed_date` 实际时长，计算达成率。**问责性分析，官方不会自己做。**
2. **空间公平性分析** —— 220,580 条带地理冬季工单 × 社区 / 选区，
   检验响应时长是否存在系统性差异。
3. **降雪—响应剂量反应曲线** —— 多少 cm 降雪触发多少投诉，以及各分区在全市犁雪中
   **被排在第几批**（`tix9-r5tc.shift_number`，分区 × 事件粒度）。
   **这是预测与资源调度的基础。**
   ⚠️ 不要写成"每条街多久清完"，**也不要写成"分区何时完成"**——
   前者数据不存在（§4.3），后者是计划值且同班次分区完全相同（§4.1.0、ADR 0008）。
4. **禁令时机有效性** —— 49 个禁令事件的 `ban_start` 相对降雪峰值的滞后，评估决策及时性。
5. **单位路网负载归一化** —— 用 `ngsx-caav` 路网算出各分区街道公里数作分母，
   把投诉密度转换为真正的「单位路网负载」。
6. **构建纵向清雪数据集** —— 自建 `g3p4-h83y` 每日快照，填补公开数据空白（见 §4.3）。

---

## 6. 其他数据集清单

### 6.1 第一梯队（可作主线或重要辅助）

#### WFPS Call Logs（消防与护理救护出勤）— `yg42-q284`

| 属性 | 值【元数据】 |
|---|---|
| 行数 | **1,323,967** |
| 时间范围 | 2014-12-31 ~ 至今 |
| 更新 | 近实时 |
| 字段 | Incident Number, Incident Type, Call Time, Closed Time, **Motor Vehicle Incident (YES/NO)**, Units, Neighbourhood, Ward |

数据干净、信息密度高：有出勤单位、可算响应时长、有车祸标记、有邻里与选区。
适合做响应时间公平性分析；`Motor Vehicle Incident = YES` 可直接关联恶劣天气与事故。

#### Real-Time Midblock Traffic Data — `bh78-7qpb`

| 属性 | 值【元数据】 |
|---|---|
| 行数 | 537,466（**滚动窗口，非全量历史**） |
| 列数 | 38 |
| 时间范围 | 2026-05-21 ~ 2026-07-28（约 2 个月） |
| 更新 | **15 分钟** |
| 字段 | Site ID, Lane ID, Location, Travel Direction, Traffic Volume, 速度分位数(50/85/95th), 速度分箱(0–100+ kph), 经纬度 |

官方声明数据**未经有效性审核，可能受设备误差影响** —— 是 Silver 层清洗规则的好素材。
**门户上唯一的真·准实时源**，展示 streaming 能力只能靠它。

#### Detailed Building Permit Data — `it4w-cpf4`

| 属性 | 值【元数据】 |
|---|---|
| 行数 | 160,643 |
| 列数 | 40 |
| 时间范围 | 2010-01-04 ~ 2026-06-30 |
| 更新 | 每月 |

官方说明提到近期修订过口径（多户住宅计数方式变更、修正高估的批准住宅单元数）——
**真实的 schema / 口径漂移案例**，可与 §3.8 的渠道漂移一并写进数据治理章节。

#### 311 Service Request — `4her-3th5`（已废弃）

90,180 行，2023-01-01 ~ 2025-03-10，已被 `u7f6-5326` 取代，仅含部分工单类型。
隐私说明（对理解上游口径有用）：狗类投诉、涂鸦、蚊虫投诉、社区宜居性投诉、
下水道倒灌、空置建筑投诉这几类，位置在 **500 米范围内随机化**。

### 6.2 冬季运营配套 —— `39ur-higg` 已实测（2026-08-02）

| 数据集 | ID | 说明 |
|---|---|---|
| Plow Zones | `39ur-higg` | 犁雪分区边界（地理数据），**82 个多边形 / 25 个分区取值** ✅ 实测 |
| Map of Plow Zones | `tm8b-h7pb` | 同为 82 行，`39ur-higg` 的备选几何源。未逐字段比对 |

**取到了 —— BO-4 的交叉映射表输入成立，摘要的「三源联结」措辞无需改。**
可复现的调用：

```bash
curl -s "https://data.winnipeg.ca/resource/39ur-higg.json?\$limit=200"
```

| 项 | 实测值 |
|---|---|
| 行数 | **82**（不是 22 —— 22 是分区数，一个分区由多个不相邻多边形组成） |
| 字段 | `entity_id`（唯一，82/82）· `city_area` · `plow_zone` · `the_geom` |
| 几何类型 | **全部 MultiPolygon**，82/82 非空 |
| 坐标系 | **WGS84 (EPSG:4326)**，lon `-97.3265..-97.0266` / lat `49.7136..49.9501` |
| `city_area` | East 31 · South 26 · North 25 |
| 每分区多边形数 | 1 个的 9 区，2 个的 6 区，5–8 个的 9 区 |

> 🔴 **`plow_zone` 取值两侧不一致 —— 这是本次实测最重要的发现。**
>
> `tix9-r5tc` 排班表恰好 22 个取值（`A`–`V`，各 19 行）。
> `39ur-higg` 边界表有 **25 个**：`A`–`V` 全部存在，**另有 `X`(8 个多边形)、
> `B/D`(1)、`Downtown`(1)**，合计 **10/82 个多边形、约 31% 的包围盒面积**
> 在排班表里没有任何对应记录。
>
> 影响：空间归属本身不受影响（82 个多边形几何完整，任一点都能落到某个分区——
> 实测对 237,867 个地址点命中率 **100.0%**）；受影响的是**分区 → 排班的联结**——
> 落在这 10 个多边形里的工单查不到作业班次。
> 这三个值大概率是真实的运营区分而非数据缺陷（`Downtown` 有独立清雪计划、
> `X` 疑为不按分区作业的区域），但**必须在 Gold 层显式建模为「无排班分区」，
> 不能让它以 NULL 的形式静默传播**。待与 BO-4 一并确认口径。
>
> 🔴 **地址数实测【2026-08-07】**：`B/D` **11,150** · `X` **2,590** ·
> `Downtown` **574**，合计 14,314，占已归属地址 **6.0%**。
> `B/D` 单独就占 4.7%，需先弄清它是边界表的历史遗留合并标签、还是真有一块区域
> 不进全市犁雪排班。**这三个取值不得进入任何顺位排名**——「无排班记录」不是
> 「排在最后」，把它读成后者正是 ADR 0008 删掉「作业缺口」因子的那个错误。

**降级方案未被触发**：[ADR 0007](../adr/0007-clearing-completion-time-source.md)
§4.2 的降级路径本次不需要启用——82 个多边形已实测可取，空间命中率 100.0%，
`location-aware` 表述无需减弱（[ADR 0008](../adr/0008-plow-schedule-is-a-plan-not-a-record.md) §4.5）。

### 6.3 路网与基础地理（负载归一化必需）

| 数据集 | ID | 说明 |
|---|---|---|
| Road Network | `ngsx-caav` | 单车道路网，含桥梁与地址范围 |
| Map of Road Network | `2eba-wm4h` | 地图版 |
| City of Winnipeg LRS | `jwfi-vjqw` | 线性参考系统基础几何 |
| LRS Block Segments | `sr8r-ehr3` | 街道分级、地址范围、车道数、行车方向 |
| LRS Speed Limits | `j5wn-5wz7` | 依市政条例的限速 |
| Assessment Parcels | `d4mq-wa44` | 全部估价地块（门户浏览量最高，144,988） |
| Map of Assessment Parcels | `7shc-stst` | 地图版 |

### 6.4 温尼伯特色维度（建议作彩蛋，非主线）

**蚊虫控制**（温尼伯以「北美蚊都」闻名）

| 数据集 | ID | 说明 |
|---|---|---|
| Daily Adult Mosquito Trap Data | `du7c-8488` | 市区及市界外 10 km 成蚊诱捕器数据，每日 |
| Adult Mosquito Trap Areas | `ii5g-muqb` | 诱捕器所属区域（精确位置因隐私未公开） |
| Mosquito Larval Control Listings | `j9wn-wnhr` | 孑孓滋生地巡查与处理区域 |
| Helicopter Flight Spray | `pk9u-zvrf` | 直升机喷洒作业区域 |
| Map of Adult Mosquito Trap Areas | `x7rh-xfqy` | 区域地图 |

**树木资产**（关联荷兰榆树病 Dutch Elm Disease）

| 数据集 | ID | 说明 |
|---|---|---|
| Tree Inventory | `hfwk-jp4h` | 全市公共树木：学名、俗名、精确坐标 |
| Tree Inventory Map | `n7eq-raej` | 地图版 |
| Tree Inventory (New Visual Experience) | `xyma-gm38` | 新版可视化 |
| tree_inventory (Shapefile) | `h923-dxid` | Shapefile 格式 |

注：311 的 `type` 中存在 `DED Service IR`（1,741 条），DED 疑为 Dutch Elm Disease，待确认。

### 6.5 其他

| 数据集 | ID | 说明 |
|---|---|---|
| River Water Levels | `tgrf-v2zc` | 红河 / 阿西尼博因河水位，实时。关联春汛与洪水风险 |
| Recycling, Garbage & Yard Waste Collection Days | `6rcy-9uik` | 按地址的收集日程（浏览量 125,387，民生高频） |
| 311 Call Wait Times | `vrzk-mj7v` | 呼叫中心等待与通话时长，每行为最近 30 分钟均值 |
| Accessibility Disruptions | `fxq5-ign2` | 人行道无障碍与公交线路中断 |
| WPA Paystation | `b85e-mbuw` | 路边与地面停车场缴费机位置及费率 |
| Cycling Network Map | `e9ms-78q6` | 骑行网络（浏览量 30,786） |
| Midblock Traffic Counts | `buvf-b9wp` | 气动管交通计数（2–7 天周期），历史 |
| Combined Sewer Overflow Annual Results | `ng8w-yxut` | 合流制污水溢流年度报告（联邦 / 省级报送） |
| School Zone Signage | `5298-dhjx` | 校区限速标识位置 |

---

## 7. 门户之外的数据源

### 7.1 Winnipeg Transit Open Data API

- 开发者门户：https://api.winnipegtransit.com/home/api/v3
- 说明页：https://info.winnipegtransit.com/en/open-data/open-data-web-service/

| 属性 | 值 |
|---|---|
| 实时数据 | 车辆位置、到站预测等 |
| 静态时刻表 | 单文件下载，每日更新，遵循 **GTFS** |
| 请求方式 | `GET https://api.winnipegtransit.com/<path>?<params>`，默认 XML；路径加 `.json` 返回 JSON |
| 认证 | 需注册获取 **API key** |
| 限流 | **每 key 每 IP 每分钟 100 次** |

GTFS 是国际通用规范，论文讨论「可迁移性」时可直接引用。
限流 100 req/min 是真实工程约束，可写进「速率限制与退避策略」章节。

### 7.2 Winnipeg Police Service

- **CrimeStat 已停用**，被 **CrimeMaps** 取代
- **2024 Statistical Report（ArcGIS Hub）**：https://wps-statistical-report-2024-wpsgis.hub.arcgis.com/
  支持导出 CSV / KML / Zip / GeoJSON / GeoTIFF / PNG
- 开放程度**明显低于**大都市级的逐条 Socrata 犯罪数据

### 7.3 Statistics Canada

**① 普查 Census Profile + 普查地理边界（Dissemination Area 级）— 🆕 已采纳进 H1**

- 内容：DA 级人口、年龄结构、语言、收入。Winnipeg CMA 约数千个 DA
- 时间：普查年（2021 / 2016 / 2011），与分析窗口 2015–2026 存在**时间错配，须声明**
- 获取：一次性下载，无认证，不是 API 轮询。边界文件走 GeoJSON→WKT 通路（BO-4 已有）
- 用途：**它是门户之外唯一一个同时解决两个问题的源**——
  ① 311 投诉密度的「报告倾向偏差」从只能声明变成可以度量；
  ② 排班顺位差异是否与社会经济分布相关
- 分级与启用细节见 [data-source-portfolio.md](data-source-portfolio.md) §3.5

**② Incident-based crime statistics, Manitoba（1998–2024）**

- https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=3510018101
- 聚合级数据，适合宏观对照，不适合逐条事件分析。**与 ① 是两回事**，未采纳

### 7.4 其他线索

- Manitoba Collaborative Data Portal：http://www.mbcdp.ca/local-data-portals.html
- University of Winnipeg 图书馆数据集指南
- CBC 报道：[2023 年除雪超预算 420 万加元](https://www.cbc.ca/news/canada/manitoba/winnipeg-snow-clearing-budget-1.7126424) —— 痛点的可引用证据

---

## 8. 分层策略：管道范围 ≠ 分析范围

这是解决「1,835 万 vs 27.5 万」张力的正解，也与现有 Bronze/Silver/Gold 架构一致：

| 层 | 范围 | 理由 |
|---|---|---|
| **Bronze** | **全量 18,346,621 条**，immutable NDJSON，不做筛选 | 真实工程规模；3,563 个 type 的字典管理、20.9% 地理缺失、渠道口径漂移，**都是全量才暴露的真问题** |
| **Silver** | 清洗、渠道归一化、UTC 时间戳、地理可用性标记 | 可全量做，也可只做冬季切片 |
| **Gold** | 按业务需求建模；冬季运营负载分数只需冬季切片 + 天气 + 排班 | 分析范围由问题决定，与摄取范围解耦 |

**叙事上的诚实表述**：管道摄取全量是真实的工程贡献；分析聚焦冬季是一个演示应用。
两者不矛盾，且 311 只是数据源之一（另有 WFPS 132 万、Midblock、建筑许可 16 万等）。

此外，**Operational Load Score 不必只用除雪工单** ——
可用全部 7,089,795 条 Service Request，冬季运营只是切入视角。

---

## 9. 三个候选项目方向

### 方向一：Winnipeg Winter Operations Intelligence ★最推荐

**数据组合**（均已实测确认可用）：

- `u7f6-5326` 311（冬季切片 275,243 条，其中 220,580 带地理）
- `tix9-r5tc` 排班 418 条 + `mfzv-893p` 禁令 49 条 + `39ur-higg` 分区边界
- Open-Meteo 历史降雪（已验证）
- `yg42-q284` WFPS 中 `Motor Vehicle Incident = YES`
- `ngsx-caav` 路网（负载归一化分母）
- 自建 `g3p4-h83y` 每日快照（纵向数据集）

**产出**：按社区 / 选区的冬季运营负载评分 + 模型驱动的资源调度建议。
与现有平台的 Operational Load Score 同构，可复用 Gold 层设计。

> 本方向已被采纳为项目主线。落地后的目标口径以
> [business-objectives.md](business-objectives.md) 为准——其中评分公式、
> 分析单元与"完成时间"的定义均已按定稿的对外表述调整，与本节的早期设想有出入。

**推荐理由**：本地性最强、痛点最真实（有 CBC 与市议会背书）、
代码复用度最高、且官方口径直接内嵌在数据里。

### 方向二：Emergency Services Response Analysis

**数据**：`yg42-q284`（132 万行）为主 + Open-Meteo（极寒与医疗呼叫相关性）。
**产出**：响应时间的空间公平性分析。
**优劣**：数据干净、伦理议题突出、易写成论文；但工程复杂度略低（单一主源）。

### 方向三：Real-Time Traffic / Transit Pipeline

**数据**：`bh78-7qpb`（15 分钟）+ Transit API 实时 + GTFS 静态。
**优劣**：唯一能展示 streaming 能力；但痛点故事性弱，且 Midblock 仅滚动窗口无长历史。

### 推荐组合

**主线方向一**，把方向二作为分析模块并入（WFPS 在方向一本就要用），
方向三的实时能力作后期扩展。蚊虫与树木作为「夏季运营负载」彩蛋维度。

---

## 10. SODA API 使用要点【实测确认】

```
https://data.winnipeg.ca/resource/<dataset_id>.json?$limit=1000&$offset=0
```

已验证可用的 SoQL 用法：

| 用法 | 示例 |
|---|---|
| 计数 | `$select=count(*)` |
| 去重计数 | `$select=count(distinct type)` |
| 分组聚合 | `$select=type,count(*) as cnt&$group=type&$order=cnt desc` |
| 时间截断分组 | `$select=date_trunc_y(open_date) as yr&$group=yr` |
| 大小写不敏感匹配 | `$where=upper(type) like '%SNOW%'` |
| 区间过滤 | `$where=open_date between '2024-01-01' and '2024-12-31'` |
| 空值过滤 | `$where=neighbourhood is not null` |
| 分页 | `$limit` / `$offset`（**翻页必须配 `$order`，否则会漏数据**） |
| App Token | `$$app_token=<token>`，提高限流额度 |

⚠️ 两个踩过的坑：

1. `$group` 查询默认返回上限 1,000 行，需显式加 `$limit`；
   本次调研中 `$limit=2000` 曾截断 3,563 个 type 取值，改用 `count(distinct)` 才得真值。
2. 用关键词过滤 `type` 时，`%ICE%` 会误伤 `Serv-ice` / `Pol-ice` / `Not-ice` / `Invo-ice`，
   必须用 `%ICE CONTROL%` 等更精确的模式。v2 的 1.50% 即为修正后的结果
   （粗糙匹配曾误得 10.40%）。

官方文档：https://dev.socrata.com/docs/queries/

---

## 11. 数据集速查表

| 用途 | Dataset | ID | 规模 | 频率 | 验证状态 |
|---|---|---|---|---|---|
| 主数据源 | 311 Requests | `u7f6-5326` | **18,346,621** | 每日 | ✅ 实测 |
| 应急响应 | WFPS Call Logs | `yg42-q284` | 1,323,967 | 近实时 | 元数据 |
| 实时交通 | Real-Time Midblock Traffic | `bh78-7qpb` | 537,466（滚动） | 15 分钟 | 元数据 |
| 城市发展 | Detailed Building Permit | `it4w-cpf4` | 160,643 | 每月 | 元数据 |
| **作业排班** | **Plow Zone Schedule** | **`tix9-r5tc`** | **418（19 事件 / 22 分区）** | 冬季 | ✅ 实测 |
| **决策事件** | **Snow Parking Bans** | **`mfzv-893p`** | **49** | 冬季 | ✅ 实测 |
| 清雪状态 | Snow Clearing Status | `g3p4-h83y` | 237,867（**快照无历史**） | 冬季实时 | ✅ 实测 |
| 预算成本 | Cost of Road Maintenance | `rsyj-x68c` | 2,862（**JSON 端点异常**） | 年度 | ⚠️ 待解决 |
| **分区边界** | **Plow Zones** | **`39ur-higg`** | **82 多边形 / 25 分区值** | 静态 | ✅ 实测 |
| 路网归一化 | Road Network | `ngsx-caav` | — | — | 元数据 |
| 驱动变量 | Open-Meteo Archive | — | 日粒度 18 年 | 每日 | ✅ 实测 |
| 洪水风险 | River Water Levels | `tgrf-v2zc` | — | 实时 | 元数据 |
| 地方特色 | Daily Adult Mosquito Trap | `du7c-8488` | — | 每日（夏） | 元数据 |
| 地方特色 | Tree Inventory | `hfwk-jp4h` | — | — | 元数据 |

---

## 12. 待办与待验证事项

**已在 v2 中解决**（v1 遗留项）：

- [x] 实际调用 SODA API，验证真实字段名与类型 → §3.1
- [x] 确认地理字段空值率 → §3.5（全表 20.9%，冬季 80.1%）
- [x] 确认除雪相关类别如何标识 → §3.3（`type` 内嵌 P1/P2/P3 与 Reg/After）
- [x] 确认冬季数据集是否保留历史 → §4.3（`g3p4-h83y` 无历史，必须自建快照）
- [x] 调查工单量下降风险 → §3.8（全局现象 + 渠道口径迁移）
- [x] 确认是否「烂大街」 → §5.1（分析方向未饱和，但须避开官方 app 的重复）
- [x] 验证降雪驱动变量可得性 → §4.5（Open-Meteo 实测通过）

**已在 2026-08-02 的实测中解决**：

- [x] **实测 `39ur-higg` 分区边界** → §6.2（82 个 MultiPolygon / WGS84 / 25 个
      `plow_zone` 取值，其中 `X`、`B/D`、`Downtown` 共 10 个多边形在排班表中无对应）
- [x] **确认 311 去重键** → §3.9（`case_id` 不唯一，`(case_id, interaction_id)` 才是）
- [x] **上线 `g3p4-h83y` 每日快照采集** → 已于 2026-08-02 上线，见
      [launch 记录](../launch/20260802-snapshot-collection-deployment-launch.md)

**仍待处理**：
- [ ] 解决 `rsyj-x68c` JSON 端点返回空对象的问题（尝试 CSV 导出）
- [ ] 确认 `bh78-7qpb` 滚动窗口的确切保留长度，判断是否需自建长期归档
- [ ] 注册 Winnipeg Transit API key，实测限流行为
- [ ] 建立 3,563 个 `type` 取值的归一化字典（处理 `Pr 2` / `Priority 2` / `P2` / `_vof` 变体）
- [ ] 核实 `mfzv-893p.id` ↔ `tix9-r5tc.snow_ban_id` 的关联完整性
- [ ] 确认 `type` 中 `DED Service IR` 是否为 Dutch Elm Disease
- [ ] 申请 Socrata App Token（匿名调用限流较低）
- [ ] 核实可投的本地会议名单与截稿日期
- [ ] 与老师确认论文选题的期望范围（预期比本文档构想更窄）

---

## 13. 参考链接

- City of Winnipeg Open Data Portal — https://data.winnipeg.ca/
- 311 Requests — https://data.winnipeg.ca/Contact-Centre-311/311-Requests/u7f6-5326
- WFPS Call Logs — https://data.winnipeg.ca/Fire-and-Rescue/WFPS-Call-Logs/yg42-q284
- Plow Zone Schedule — https://data.winnipeg.ca/City-Planning/Plow-Zone-Schedule/tix9-r5tc
- Winnipeg Transit API — https://api.winnipegtransit.com/home/api/v3
- Winnipeg Transit Open Data 说明 — https://info.winnipegtransit.com/en/open-data/open-data-web-service/
- Know Your Zone app — https://play.google.com/store/apps/details?id=ca.winnipeg.pwd.KnowYourZone
- 官方除雪进度地图介绍 — https://www.winnipeg.ca/people-culture/our-city-our-stories/new-map-you-track-snow-clearing-progress
- 官方除雪与冰控政策（P1/P2/P3 分级）— https://winnipeg.ca/publicworks/snow/snow-clearing-policy.stm
- CBC：2023 年除雪超预算 420 万 — https://www.cbc.ca/news/canada/manitoba/winnipeg-snow-clearing-budget-1.7126424
- WPS 2024 Statistical Report — https://wps-statistical-report-2024-wpsgis.hub.arcgis.com/
- Statistics Canada 曼尼托巴犯罪统计 — https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=3510018101
- Manitoba Collaborative Data Portal — http://www.mbcdp.ca/local-data-portals.html
- Socrata SODA 查询文档 — https://dev.socrata.com/docs/queries/
- Open-Meteo Archive API — https://open-meteo.com/en/docs/historical-weather-api
