# 项目概述

## 项目定位

**Urban Operations Intelligence Platform (UOIP)** —— 一个商业场景驱动的城市运营
数据平台。

模拟城市运营团队（311 调度中心、交通管理部门、应急响应调度组）的数据团队，
整合政府公开 API 与气象数据，评估未来 24 小时各行政区/社区的运营负荷水平，
并为公共资源（急救、市政维修、客服座席等）配置提供决策建议。

项目重点在**数据工程**而非机器学习：

- Lakehouse 分层架构
- Spark ETL
- 数据仓库建模（星型模型 + 空间分析）
- Airflow 编排与增量摄取
- 规则驱动的运营决策支持系统

## 城市无关性

平台设计为**城市无关（city-agnostic）**：城市差异只体现在配置里
（`config/sources/*.yaml` + 一份行政区边界数据），管道代码不含任何城市硬编码。

已有实现基于 **New York City** 开放数据（Bronze 全通、Silver 2/4）。
**当前新增开发的目标城市是 Winnipeg (MB)**，数据源实测结果见
[notes/winnipeg-data-sources.md](../notes/winnipeg-data-sources.md)——
两地开放数据门户同为 Socrata 平台，摄取层几乎可零改动复用。

> 这一定位把 NYC 的存量实现从"遗留包袱"变成了**可移植性的实证基线**：
> 同一套架构在人口规模、气候、治理结构截然不同的两个城市上验证
> （NYC 约 840 万人 vs Winnipeg 约 75 万人）。

**两地的关键差异**（影响建模，不影响管道）：

| | NYC | Winnipeg |
|---|---|---|
| 行政粒度 | 5 个 Borough | **15 个 ward + 242 个 neighbourhood** |
| 作业粒度 | 与行政区一致 | **22 个 plow zone，与行政区不嵌套** |
| 核心运营议题 | 全年综合负载 | **冬季除雪**（"Winterpeg"） |

Winnipeg 多出的「作业几何 ≠ 行政几何」问题是 NYC 部署不存在的新工程前提，
见 [business-objectives.md](business-objectives.md) BO-4。

## 商业背景

城市服务与作业资源有限，调度部门在每次降雪事件中需要回答三个问题：

1. 哪些社区/作业分区在本次事件中**服务负荷最高**？
2. 负荷高的**原因**是什么——降雪量大、作业未覆盖、还是响应超时？
3. 下一次降雪应如何**调整分区作业优先级与资源投放**？

现状是需求侧（311 投诉）、供给侧（犁雪排班/禁令）、驱动侧（气象）三类数据
分散在不同端点，且**使用三套互不对齐的地理口径**，缺少统一平台与自动化评估机制。

痛点有硬证据：CBC 报道 2023 年除雪超预算 **420 万加元**，
除雪议题每年都是市议会与本地新闻的固定项。

## 需求范围（MVP）

MVP 不做预测模型，规则引擎即可。Winnipeg 部署聚焦**冬季运营**：

```
Winter Operational Load Score (0–100)
  = 0.35 × 加权服务请求量因子
  + 0.30 × 作业缺口因子（是否被禁令/排班覆盖）
  + 0.20 × 天气严重度因子
  + 0.15 × 应急事件因子
```

绝对量需用路网公里数归一化为**单位路网负载**，避免大分区天然得分高。

输出三项：分区运营负荷排名、负荷原因归因、资源配置建议。
另有一项官方工具不做的核心交付物：**SLA 合规性审计**。

详细的业务目标拆解、验收标准与已知约束见 [business-objectives.md](business-objectives.md)。

## 数据源

具体登记表见对外文档 [Data Sources](../../guide/data-sources.md)；
Winnipeg 各数据集的实测规模见 [notes/winnipeg-data-sources.md](../notes/winnipeg-data-sources.md)。

| 类别 | 作用 | Winnipeg 实例 | NYC 实例（既有） |
|---|---|---|---|
| 市民服务请求 | 需求侧核心事实表 | 311 Requests `u7f6-5326`（1,835 万行） | 311 Service Requests |
| **作业排班** | **供给侧核心事实表** | Plow Zone Schedule `tix9-r5tc`（418 条 / 19 事件） | — 无对应 |
| **决策事件** | **供给侧事件日历** | Snow Parking Bans `mfzv-893p`（49 条） | — 无对应 |
| 公共安全事件 | 急救负荷 | WFPS Call Logs `yg42-q284`（132 万行） | NYPD Collisions / Shootings |
| 气象数据 | 驱动变量（历史 + 预报） | Open-Meteo | Open-Meteo |
| 作业分区边界 | 空间归属维度 | Plow Zones `39ur-higg`（22 个） | Borough Boundaries GeoJSON |
| 路网 | 负载归一化分母 | Road Network `ngsx-caav` | — 未使用 |
| 清雪状态 | 纵向数据集（需自建快照） | Snow Clearing Status `g3p4-h83y`（快照无历史） | — 无对应 |

> Winnipeg 相对 NYC 最大的结构性增益是**有了供给侧数据**：
> NYC 部署只能观测需求（投诉）与环境（天气），
> Winnipeg 还能观测市政**实际做了什么**，因而可以做问责性分析。

## 数据仓库设计

星型模型：

- **事实表**：`fact_service_requests`（需求侧）、`fact_plow_shifts`（供给侧）、
  `fact_emergency_calls`、`fact_winter_event_zone_load`（事件×分区聚合，承载评分与建议）
- **维度表**：`dim_date`、`dim_time`、`dim_geography`、`dim_weather`、
  `dim_request_type`（3,563 个 `type` 取值的归一化字典，解析优先级与时段）、
  `dim_recommendation_rules`
- 事实表按日期分区，按作业分区（`plow_zone`）聚簇

> `dim_geography` 在 Winnipeg 部署必须同时承载 **neighbourhood / ward / plow_zone**
> 三种归属，因为三者互不嵌套（242 / 15 / 22）。这是与 NYC 部署
> （5 个 Borough 一套几何）最大的建模差异，见 business-objectives BO-4。

## 技术栈

| 层 | Phase 1（当前） | Phase 2（规划） |
|---|---|---|
| 存储 | GCS | MinIO |
| 计算 | 自建 Docker Spark Standalone | Spark + Iceberg |
| 仓库 | BigQuery | Trino + Iceberg |
| 编排 | 自建 Docker Airflow | 同左 |
| 可视化 | Looker Studio / Streamlit | Superset / Metabase |

架构细节见 [architecture/platform-architecture.md](../architecture/platform-architecture.md)，
交付路线见 [architecture/roadmap.md](../architecture/roadmap.md)。

## 设计原则

- 以真实生产系统为标准，不以学习演示为目标
- 优先采用企业级数据工程实践（分页处理、late-arriving facts、幂等性）
- 所有设计需同时解释业务价值与技术价值
- 考虑可扩展性、可维护性和成本控制
- 敏捷交付，逐阶段完成 MVP
