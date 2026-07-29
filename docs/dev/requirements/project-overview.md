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

当前生产部署使用 **New York City** 开放数据。第二个候选城市 **Winnipeg (MB)**
的数据源调研见 [notes/winnipeg-data-sources.md](../notes/winnipeg-data-sources.md)——
两地开放数据门户同为 Socrata 平台，摄取层几乎可零改动复用。

> 这一定位把 NYC 的存量实现从"遗留包袱"变成了**可移植性的实证基线**：
> 同一套架构在人口规模、气候、治理结构截然不同的两个城市上验证。

## 商业背景

城市服务与巡逻资源有限，调度部门每天需要回答三个问题：

1. 哪些行政区未来 24 小时**服务请求和突发事件的负荷最高**？
2. 负荷上升的**主要原因**是什么（暴雪引发供暖投诉、恶劣天气导致事故激增）？
3. 如何**跨部门优化资源配置**（提前增派铲雪车、增加供暖组接线员、在事故高发
   路段增派救护车）？

现状是数据分散在不同的开放数据端点和气象系统中，缺少统一平台和自动化评估机制。

## 需求范围（MVP）

MVP 不做预测模型，规则引擎即可：

```
Operational Load Score
  = 0.4 × 服务请求量因子
  + 0.4 × 交通事故严重度因子
  + 0.2 × 天气严重度因子
```

输出三项：区域运营负荷排名、负荷原因分析、资源配置建议。

详细的业务目标拆解见 [business-objectives.md](business-objectives.md)。

## 数据源

四类数据源，具体登记表见对外文档 [Data Sources](../../guide/data-sources.md)：

| 类别 | 作用 | 当前实例（NYC） |
|---|---|---|
| 市民服务请求 | 核心事实表（市政服务负荷） | 311 Service Requests |
| 公共安全事件 | 核心事实表（交通与急救负荷） | NYPD Collisions / Complaints / Shootings |
| 气象数据 | 环境影响因子（历史 + 预报） | Open-Meteo |
| 行政区边界 | 空间归属维度 | Borough Boundaries GeoJSON |

## 数据仓库设计

星型模型：

- **事实表**：`fact_311_requests`、`fact_vehicle_collisions`、
  `fact_daily_borough_load`（日度聚合，承载评分与建议）
- **维度表**：`dim_date`、`dim_time`、`dim_geography`、`dim_weather_forecast`
- 事实表按日期分区，按行政区聚簇

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
