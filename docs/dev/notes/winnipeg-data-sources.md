# 温尼伯（Winnipeg, MB）本地数据源调研

> 调研日期：**2026-07-28**
> 目的：为「使用温尼伯本地数据做数据工程项目 → 包装成 Research Paper → 在会议宣讲」这一目标，
> 筛选真正适合的本地数据源。
> 状态：**调研完成，尚未实际拉取数据验证字段**（待办见文末 §9）。

---

## 1. 背景与约束

Data Acquisition 课程老师在结课时单独给出的建议，可归纳为四个要素：

| 要素 | 含义 | 对数据源的要求 |
|---|---|---|
| **Local** | 使用 Winnipeg / Manitoba 本地数据 | 数据必须来自本地政府或本地机构，不能是通用公开数据集 |
| **Big Data Engineering** | 是数据开发项目，不是纯 ML 玩具 | 体量足够撑起分层/分区/增量/调度等工程议题 |
| **Real Pain Point** | 能勾勒出本地核心痛点的真实需求 | 议题在本地有公共讨论度，最好上过本地新闻或市议会 |
| **Conference** | 可在某些场合宣讲，导师可能联名 | 结论要有可迁移性/普适性，不能只是"我做了个管道" |

隐含的第五点：老师本人可能希望从这类项目中积累其晋升所需的产出指标，
因此**项目产出需要对他也可署名、可展示**。

---

## 2. 关键发现：温尼伯开放数据门户与 NYC 同为 Socrata 平台

这是本次调研最重要的结论。

- 门户地址：https://data.winnipeg.ca/
- 平台：**Socrata**（与 `data.cityofnewyork.us` 完全相同的技术栈）
- API：SODA（Socrata Open Data API），端点与查询语法与 NYC 一致
- 授权：Open Government Licence – Winnipeg / Canada OGL

### 2.1 对本项目（NYC-UOIP）的直接意义

现有代码库的 Bronze 摄取层**几乎可以零改动复用**：

| 现有模块 | 复用程度 |
|---|---|
| `ingestion/clients/socrata_client.py` | 完全复用，仅需切换 `domain` 参数 |
| `ingestion/backfill/fetchers/socrata.py` | 完全复用 |
| `ingestion/backfill/facade.py` | 完全复用（daily / monthly / static 三种分区策略均适用） |
| `ingestion/loaders/gcs_loader.py` | 完全复用（可能需在路径中增加 `city` 维度） |
| `scripts/backfill/` 三层架构 | 完全复用（registry 自动发现，新增源 = 丢一个文件） |
| `config/sources/*.yaml` | 新增温尼伯的 YAML 即可 |

已核查：上述核心模块中出现的 "NYC/NYPD/DCP" 字样**全部位于 docstring 与注释**，
逻辑代码中没有任何一处硬编码城市。架构本身就是城市无关的。

### 2.2 对论文选题的意义

因为 NYC 的实现已经存在且可运行，项目可以自然地定位为：

> **一个城市无关（city-agnostic）的城市运营 Lakehouse 架构，
> 在两个规模、气候、治理结构截然不同的城市上验证 ——
> New York City（约 840 万人）与 Winnipeg（约 75 万人）。**

这个定位把 NYC 的存量工作从「遗留包袱」变成了**可移植性的实证基线**，
并且天然带出一个对本地有意义的次级论点：
*小城市缺乏纽约那样的数据工程资源，而可移植架构正是解法。*

> ⚠️ 注意：**项目实现范围 ≠ 论文研究范围**。
> 项目可以支持多城市；论文题目在最终确立时再收窄到老师期待的尺度即可。
> 反过来（先删掉 NYC 再想要这个论证）则不可逆。

---

## 3. 核心数据集清单（data.winnipeg.ca）

以下 Dataset ID 均为 Socrata 四位-四位标识符，可直接用于 SODA API。
标注「已核实」的条目其行数/列数/时间范围来自门户的 catalog / views 元数据接口（2026-07-28 查询）。

### 3.1 第一梯队：足以支撑项目主线

#### 311 Requests — `u7f6-5326` ★最重要

| 属性 | 值 |
|---|---|
| 规模 | **约 16,680,000 行**（已核实） |
| 列数 | 16 |
| 时间范围 | 2008 至今 |
| 更新频率 | 每日 |
| 主要字段 | Case ID, Interaction ID, Channel Type, Subject, Reason, Open Date, Closed Date, Case Status, Neighbourhood, Ward, Geometry, 及若干 computed region 字段 |
| 隐私处理 | 敏感类别的位置在**邻里边界内做了随机化** |

`Channel Type` 区分 Voice In / Email / Face-to-Face / Self Service / Social Media / SMS / Mobile，
这本身就是一个有意思的分析维度（渠道随时间的迁移 = 数字化程度）。

**1668 万行是整个调研中体量最大的数据集，足以撑起"大数据"的工程叙事**
（分区策略、增量 MERGE、Spark 性能对比、查询成本优化都有的写）。

#### 311 Service Request — `4her-3th5`（已废弃，仅作参考）

| 属性 | 值 |
|---|---|
| 规模 | 90,180 行（已核实） |
| 时间范围 | 2023-01-01 ~ 2025-03-10 |
| 状态 | **已废弃**，被 `u7f6-5326` 取代；仅含部分工单类型 |

隐私说明（对理解上游口径有用）：狗类投诉、涂鸦、蚊虫投诉、
社区宜居性投诉、下水道倒灌、空置建筑投诉这几类，
位置被在 **500 米范围内随机化**。

#### WFPS Call Logs（消防与护理救护出勤） — `yg42-q284`

| 属性 | 值 |
|---|---|
| 规模 | **1,323,967 行**（已核实） |
| 时间范围 | 2014-12-31 ~ 至今 |
| 更新频率 | 近实时 |
| 字段 | Incident Number, Incident Type, Call Time, Closed Time, Motor Vehicle Incident (YES/NO), Units, Neighbourhood, Ward |

数据非常干净且信息密度高：**有出勤单位、有结案时间（可算响应时长）、
有是否涉及车祸、有邻里与选区**。适合做响应时间公平性分析。

#### Real-Time Midblock Traffic Data — `bh78-7qpb`

| 属性 | 值 |
|---|---|
| 规模 | 537,466 行（已核实，滚动窗口） |
| 列数 | 38 |
| 时间范围 | 2026-05-21 ~ 2026-07-28（**滚动保留，非全量历史**） |
| 更新频率 | **15 分钟** |
| 字段 | Site ID, Lane ID, Location, Travel Direction, Traffic Volume, 速度分位数（50th/85th/95th）, 速度分箱（0–100+ kph）, 经纬度 |

官方声明：数据**未经有效性审核，可能受设备误差影响**——
这反而是数据质量层（Silver 清洗规则）的好素材。

这是门户上**唯一的真·准实时数据源**，如果要展示 streaming 能力，只能靠它。

#### Detailed Building Permit Data — `it4w-cpf4`

| 属性 | 值 |
|---|---|
| 规模 | 160,643 行（已核实） |
| 列数 | 40 |
| 时间范围 | 2010-01-04 ~ 2026-06-30 |
| 更新频率 | 每月 |
| 字段 | Issue Date, Permit Number, Permit Type, Street Address, Work Type, Dwelling Units Created/Lost, Permit Status, 经纬度 |

门户标注为 Gold-ranked 数据集，40,425 次浏览 / 12,593 次下载。
注：官方说明提到近期修订过口径（多户住宅计数方式变更、修正了轻微高估的批准住宅单元数）——
**这是一个真实的 schema/口径漂移案例，写进论文的数据治理章节很有说服力**。

---

### 3.2 冬季运营（除雪）——温尼伯第一民生痛点

温尼伯自嘲为 "Winterpeg"。除雪预算超支、除雪投诉、
停车禁令规则混乱几乎每年都是市议会与本地新闻的固定议题。

| 数据集 | ID | 说明 |
|---|---|---|
| Snow Parking Bans | `mfzv-893p` | 当前生效的除雪停车禁令列表，与 Plow Zone Schedule 配合使用 |
| Snow Clearing Status and Winter Parking Bans (Address-Level) | `g3p4-h83y` | **地址级**除雪状态与停车禁令：住宅街道、后巷、人行道的当前清雪状态 |
| Plow Zone Schedule | `tix9-r5tc` | 计划中的住宅区停车禁令，含日期与犁雪分区 |
| Plow Zones | `39ur-higg` | 犁雪分区边界（地理数据） |
| Map of Plow Zones | `tm8b-h7pb` | 分区地图可视化 |

维护部门：Public Works。授权：Open Government Licence – Winnipeg。

> `g3p4-h83y` 的**地址级清雪状态**是最有价值的一个——
> 它让「降雪事件 → 311 投诉 → 实际清雪完成」的完整因果链条可被量化。

### 3.3 温尼伯特色维度

#### 蚊虫控制（温尼伯以"北美蚊都"闻名）

| 数据集 | ID | 说明 |
|---|---|---|
| Daily Adult Mosquito Trap Data | `du7c-8488` | 市区内及市界外 10 km 的成蚊诱捕器数据，**每日** |
| Insect Control – Adult Mosquito Trap Areas | `ii5g-muqb` | 诱捕器所属区域（精确位置因隐私未公开） |
| Insect Control – Mosquito Larval Control Listings | `j9wn-wnhr` | 常见孑孓滋生地的巡查与处理区域 |
| Insect Control – Helicopter Flight Spray | `pk9u-zvrf` | 直升机喷洒作业区域 |
| Map of Adult Mosquito Trap Areas | `x7rh-xfqy` | 区域地图 |

这是**极具地方辨识度**的数据，但体量小，建议作为"彩蛋维度"而非主线。

#### 树木资产（与荷兰榆树病 Dutch Elm Disease 危机相关）

| 数据集 | ID | 说明 |
|---|---|---|
| Tree Inventory | `hfwk-jp4h` | 全市公共树木清单：学名、俗名、**精确坐标** |
| Tree Inventory Map | `n7eq-raej` | 地图版 |
| Tree Inventory (New Visual Experience) | `xyma-gm38` | 新版可视化 |
| tree_inventory (Shapefile) | `h923-dxid` | Shapefile 格式 |

温尼伯拥有北美规模最大的美洲榆树群之一，荷兰榆树病是长期公共议题。
门户上未检索到专门的 Dutch Elm Disease 数据集，需另行确认（见 §9）。

### 3.4 其他可用数据集

| 数据集 | ID | 说明 |
|---|---|---|
| River Water Levels | `tgrf-v2zc` | 红河（Red）与阿西尼博因河（Assiniboine）各监测点水位，**实时**。与红河春汛/洪水风险直接相关 |
| Assessment Parcels | `d4mq-wa44` | 全部估价地块（门户浏览量最高，144,988 次） |
| Map of Assessment Parcels | `7shc-stst` | 地图版 |
| Recycling, Garbage & Yard Waste Collection Days | `6rcy-9uik` | 按地址的垃圾/回收/庭院废物收集日程（浏览量 125,387，民生高频） |
| 311 Call Wait Times | `vrzk-mj7v` | 311 呼叫中心等待时长与通话时长，每行为**最近 30 分钟的平均值** |
| Accessibility Disruptions | `fxq5-ign2` | 人行道无障碍与公交线路的中断信息 |
| WPA Paystation | `b85e-mbuw` | 路边与地面停车场缴费机位置及费率 |
| LRS Speed Limits | `j5wn-5wz7` | 全市街道与公园的限速 |
| Cycling Network Map | `e9ms-78q6` | 道路内/外骑行网络（浏览量 30,786） |

---

## 4. 门户之外的数据源

### 4.1 Winnipeg Transit Open Data API ★

- 开发者门户：https://api.winnipegtransit.com/home/api/v3
- 说明页：https://info.winnipegtransit.com/en/open-data/open-data-web-service/

| 属性 | 值 |
|---|---|
| 实时数据 | 提供服务的实时信息（车辆位置、到站预测等） |
| 静态时刻表 | 单文件下载，**每日更新**，遵循 **GTFS** 规范 |
| 请求方式 | `GET https://api.winnipegtransit.com/<path>?<params>`，默认返回 XML；路径后加 `.json` 返回 JSON |
| 认证 | 需注册账号获取 **API key** |
| 限流 | 每个 API key **每 IP 每分钟 100 次请求** |

这是做流式 / 准实时管道的理想素材，并且 GTFS 是国际通用规范，
论文里讨论"可迁移性"时可以直接引用（任何采用 GTFS 的城市都适用）。

> 限流 100 req/min/IP 是一个**真实的工程约束**，
> 正好可以写进论文的「速率限制与退避策略」章节。

### 4.2 Winnipeg Police Service 数据

- **CrimeStat 已停用**：2007 年上线的犯罪统计网站已被 **CrimeMaps** 取代。
- **2024 Statistical Report（ArcGIS Hub）**：https://wps-statistical-report-2024-wpsgis.hub.arcgis.com/
  支持导出 **CSV / KML / Zip / GeoJSON / GeoTIFF / PNG**。
- 开放程度**明显低于** NYC 的 NYPD 数据（后者有完整的 Socrata 逐条记录）。

### 4.3 Statistics Canada

- Incident-based crime statistics, by detailed violations, police services in Manitoba（1998–2024）
  https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=3510018101
- 聚合级数据，适合做宏观对照，不适合做逐条事件分析。

### 4.4 其他线索

- Manitoba Collaborative Data Portal（本地数据门户汇总）：http://www.mbcdp.ca/local-data-portals.html
- University of Winnipeg 图书馆的数据集指南（可能含学术可用的本地数据）

---

## 5. 三个候选项目方向

### 方向一：Winnipeg Winter Operations Intelligence ★最推荐

**痛点**：除雪是温尼伯最具公共讨论度的市政议题，年年上新闻与市议会。

**数据组合**：
- `u7f6-5326` 311 Requests（筛出除雪 / 冻管 / 坑洼类工单）
- `g3p4-h83y` 地址级清雪状态
- `tix9-r5tc` Plow Zone Schedule + `mfzv-893p` Snow Parking Bans + `39ur-higg` 分区边界
- Open-Meteo 历史天气（**本项目已有现成 client，零成本接入**）
- `yg42-q284` WFPS 中 `Motor Vehicle Incident = YES` 的记录（恶劣天气与事故的关联）

**产出**：按邻里/选区的**冬季运营负载评分 + 除雪资源调度建议**
（与现有 NYC-UOIP 的 Operational Load Score 完全同构，可直接复用 Gold 层设计）。

**叙事优势**：「降雪事件 → 311 投诉滞后 → 清雪完成时间」是一条清晰的因果链，
既有工程含量（多源时空对齐、事件窗口关联），又有一句话能讲清的故事。

**推荐理由**：本地性最强、痛点最真实、与现有代码复用度最高、故事最好讲。

### 方向二：Emergency Services Response Analysis

**数据**：`yg42-q284` WFPS Call Logs（132 万行）为主，
辅以 Open-Meteo（极寒天气与医疗呼叫的相关性）、邻里社会经济数据。

**产出**：响应时间的**空间公平性分析**——
不同邻里/选区的响应时长是否存在系统性差异。

**优势**：数据干净、体量适中、社会意义强、伦理议题突出（公平性容易写成论文）。
**劣势**：工程复杂度略低于方向一（单一主数据源）。

### 方向三：Real-Time Traffic / Transit Pipeline

**数据**：`bh78-7qpb` Midblock（15 分钟）+ Transit API 实时 + GTFS 静态。

**产出**：准实时交通/公交运行看板与延误预测。

**优势**：唯一能展示 **streaming**（Kafka / Spark Structured Streaming）能力的方向。
**劣势**：痛点故事性弱于前两者；Midblock 数据只有滚动窗口，缺乏长历史。

> 备注：仓库历史中曾有一条 Kafka → Spark Structured Streaming → Snowflake
> 的天气流式管道（Q3 课程作业，12 个提交）。若走方向三，那套编排骨架可作参考。

### 推荐组合

**主线走方向一**，把方向二作为一个分析模块并入（WFPS 数据在方向一里本来就要用），
方向三的实时能力可作为后期扩展。蚊虫与树木数据作为"夏季运营负载"的彩蛋维度。

---

## 6. 为什么这个选题对求职与论文都有利

1. **架构复用，冷启动成本极低** —— 同为 Socrata，Bronze 层几乎零改动；
   省下的时间可以全部投入 Silver/Gold 与分析层。
2. **体量真实** —— 1668 万行 311 + 132 万行 WFPS，
   足以支撑分区、增量、性能调优等真正的大数据工程议题，不是玩具项目。
3. **对老师的价值对齐** —— 本地数据 + 本地痛点 + 可在本地会议宣讲，
   正好是他建议的路线；且"跨城市可复制架构"这个角度让论文有真正的贡献点，
   而不只是一个工程报告。
4. **可讲的会议场合** —— 本地技术会议（如 Prairie Dev Con Winnipeg）、
   加拿大的数据/GIS 类会议、或 IEEE Big Data 之类的 workshop / poster track。
   （具体会议与截稿日期需另行核实，见 §9。）

---

## 7. SODA API 使用要点（待实测确认）

Socrata 标准端点形式（与 NYC 一致）：

```
https://data.winnipeg.ca/resource/<dataset_id>.json?$limit=1000&$offset=0
```

常用查询参数（SoQL）：

| 参数 | 用途 |
|---|---|
| `$limit` / `$offset` | 分页 |
| `$where` | 过滤，如 `$where=open_date between '2024-01-01' and '2024-12-31'` |
| `$select` | 列裁剪 |
| `$order` | 排序（分页时**必须**指定稳定排序，否则翻页会漏数据） |
| `$$app_token` | App Token，用于提高限流额度 |

> ⚠️ 以上端点格式基于 Socrata 通用规范推断，**本次调研未实际调用验证**。
> 字段名（如 `open_date`）也需以实际返回为准，不可凭记忆假设。
> 参见 AGENTS.md：「Never assume field names from memory」。

官方文档：https://dev.socrata.com/docs/queries/

---

## 8. 数据集速查表

| 用途 | Dataset | ID | 规模 | 频率 |
|---|---|---|---|---|
| 主数据源 | 311 Requests | `u7f6-5326` | ~16.68M 行 | 每日 |
| 应急响应 | WFPS Call Logs | `yg42-q284` | 1,323,967 行 | 近实时 |
| 实时交通 | Real-Time Midblock Traffic | `bh78-7qpb` | 537,466 行 | 15 分钟 |
| 城市发展 | Detailed Building Permit Data | `it4w-cpf4` | 160,643 行 | 每月 |
| 除雪（地址级） | Snow Clearing Status & Winter Parking Bans | `g3p4-h83y` | — | 冬季实时 |
| 除雪（排程） | Plow Zone Schedule | `tix9-r5tc` | — | 冬季 |
| 除雪（禁令） | Snow Parking Bans | `mfzv-893p` | — | 冬季实时 |
| 除雪（边界） | Plow Zones | `39ur-higg` | — | 静态 |
| 洪水风险 | River Water Levels | `tgrf-v2zc` | — | 实时 |
| 地方特色 | Daily Adult Mosquito Trap Data | `du7c-8488` | — | 每日（夏季） |
| 地方特色 | Tree Inventory | `hfwk-jp4h` | — | — |
| 呼叫中心 | 311 Call Wait Times | `vrzk-mj7v` | — | 每 30 分钟 |
| 基础地理 | Assessment Parcels | `d4mq-wa44` | — | — |

---

## 9. 待办与待验证事项

调研阶段未完成、需要在动手前确认的事项：

- [ ] **实际调用 SODA API**，验证 `u7f6-5326` 与 `yg42-q284` 的真实字段名与类型
- [ ] 确认 311 的地理字段（Neighbourhood / Ward / Geometry）**空值率**
      —— 若空值率过高，空间分析路线不成立
- [ ] 确认 `u7f6-5326` 的 `Reason` / `Subject` 枚举值中，除雪相关类别如何标识
- [ ] 确认冬季数据集（`g3p4-h83y` 等）在**非冬季是否仍可访问**、是否保留历史
      —— 若只有当前状态无历史快照，需要自建每日快照采集
- [ ] 确认 `bh78-7qpb` 的滚动窗口长度（当前看到约 2 个月），
      判断是否需要自建长期归档
- [ ] 注册 Winnipeg Transit API key，实测限流行为
- [ ] 确认门户是否有 Dutch Elm Disease 专门数据集
- [ ] 申请 / 确认是否需要 Socrata App Token（匿名调用限流较低）
- [ ] 核实可投的本地会议名单与截稿日期
- [ ] 与老师确认论文选题的**期望范围**（预期比本文档的构想更窄）

---

## 10. 参考链接

- City of Winnipeg Open Data Portal — https://data.winnipeg.ca/
- 311 Requests — https://data.winnipeg.ca/Contact-Centre-311/311-Requests/u7f6-5326
- WFPS Call Logs — https://data.winnipeg.ca/Fire-and-Rescue/WFPS-Call-Logs/yg42-q284
- Winnipeg Transit API — https://api.winnipegtransit.com/home/api/v3
- Winnipeg Transit Open Data 说明 — https://info.winnipegtransit.com/en/open-data/open-data-web-service/
- WPS 2024 Statistical Report — https://wps-statistical-report-2024-wpsgis.hub.arcgis.com/
- CBC 报道：CrimeStat → CrimeMaps — https://www.cbc.ca/news/canada/manitoba/police-crime-maps-winnipeg-1.5048523
- Statistics Canada 曼尼托巴犯罪统计 — https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=3510018101
- Manitoba Collaborative Data Portal — http://www.mbcdp.ca/local-data-portals.html
- Socrata SODA 查询文档 — https://dev.socrata.com/docs/queries/
