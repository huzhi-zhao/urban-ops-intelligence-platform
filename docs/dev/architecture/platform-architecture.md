# 平台架构

> 本篇是**设计意图**，写给要改架构的人。只想知道"系统长什么样、怎么跑"的读者，
> 看对外文档 [Architecture](../../guide/architecture.md) 即可。
> 具体决策的取舍过程见 [adr/](../adr/README.md)。

![端到端架构](../../images/platform-architecture.svg)

---

## 1. 组件与数据流

| # | 组件 | 职责 | 技术 |
|---|---|---|---|
| 1 | 数据源 | 开放数据 API + 气象 API + 静态边界文件 | Socrata SODA / Open-Meteo / GeoJSON |
| 2 | 摄取层 | 定时触发、分页、增量拉取、错误重试，原始数据落 Bronze | Python + Airflow |
| 3 | Bronze | 不可变原始快照 | 对象存储 + NDJSON |
| 4 | 处理层 | Schema 强制、类型转换、去重、脏数据处理、标准化 | PySpark on Spark Standalone |
| 5 | Silver | 清洗后的列式数据 | 对象存储 + Parquet |
| 6 | Gold | 星型模型、业务逻辑、空间分析 | BigQuery（Phase 1）/ Iceberg + Trino（Phase 2） |
| 7 | 智能引擎 | 负荷分计算、驱动因素识别 | SQL 规则引擎 |
| 8 | 推荐引擎 | 资源配置建议 | SQL 规则表 |
| 9 | 报表 | 负荷地图、排名、建议展示 | Looker Studio / Streamlit |
| 10 | 编排与监控 | 依赖管理、调度、告警 | Airflow |

**分层边界的硬规则**：Airflow 不碰数据，Spark 不做调度，SQL 不做摄取。
DAG 文件里出现业务逻辑视为架构违规。

---

## 2. 分层设计

### 2.1 Bronze —— 不可变原始层

- **目的**：单一真相来源。任何下游算错了都能从这里重放。
- **格式**：NDJSON。不是风格选择——BigQuery `LOAD DATA` 和 `spark.read.json()`
  都只吃换行分隔格式。踩坑记录见
  [notes/bigquery-external-table-pitfalls.md](../notes/bigquery-external-table-pitfalls.md)。
- **分区**：按源的 `partition_strategy`（daily / monthly / static）决定路径布局，
  每个数据文件配一份 manifest。详见
  [Ingestion & Bronze](../../guide/ingestion-bronze.md)。
- **保留策略**：按成本设生命周期规则（冷存储降级 / 过期删除）。
- **红线**：永不覆盖已写入的 Bronze 文件。

### 2.2 Silver —— 清洗层

- **格式**：Parquet，按日期分区。
- **处理内容**：Schema 强制 → 类型转换 → 去重 → 缺失值策略 → 字段标准化 →
  时间戳统一 UTC。
- **去重的难点**：7 天回溯窗口必然重复拉取同一天的数据，每个源必须定义
  **去重键 + 新鲜度规则**，否则重复会累积。
- **被拒绝的行**：写入 `silver/_rejects/{dataset}/` 而不是静默丢弃。
- **幂等**：动态分区覆盖，只重写窗口内涉及的分区。

### 2.3 Gold —— 业务层（未实现）

- **格式**：仓库管理表，星型模型。
- **事实表**：`fact_311_requests`、`fact_vehicle_collisions`、
  `fact_daily_operational_summary`（日度聚合，含负荷分、驱动因素、建议）。
- **维度表**：`dim_date`、`dim_time`、`dim_geography`、`dim_weather_forecast`。
- **分区/聚簇**：事实表按日期分区，按行政区聚簇。
- **空间归属（关键能力）**：原始数据的行政区文本字段错误率高（大小写不一、
  缺失、填错），**必须**用坐标 + 边界多边形做空间 JOIN 作为权威归属：
  `ST_CONTAINS(dim_geography.geometry, ST_GEOGPOINT(lon, lat))`。
  文本字段只作为空间命中失败时的降级方案，仍不可解析则标 NULL 并排除出评分。
- **几何体存储**：Silver 落 WKT 字符串，Gold 用 `ST_GEOGFROMTEXT` 转
  `GEOGRAPHY`。原因见 [ADR 0005](../adr/0005-silver-execution-architecture.md) §7.2。

---

## 3. 关键设计考虑

### 数据质量
Schema 在 Spark 阶段强制执行，脏数据不得进入 Silver。关键字段做范围校验
（坐标是否落在城市范围内）。Bronze→Silver、Silver→Gold 的行数与关键指标做监控，
异常告警。

### 错误处理与幂等性
所有任务可重试；所有 ETL 必须幂等——同一个 `execution_date` 重跑产出完全相同，
不产生重复。Gold 层用 `MERGE` 或 `INSERT OVERWRITE PARTITION`。

### 可扩展性
分区与聚簇设计是大规模查询性能的关键。计算集群规模按数据量调整。

### 安全性
最小权限 IAM；密钥不落任何 git-tracked 文件，走 Secret Manager
（见 [ADR 0001](../adr/0001-terraform-and-secrets.md)）；存储与传输加密。

### 成本控制
- 对象存储：生命周期策略，冷数据降级。
- 计算：临时集群，作业结束即销毁；**任何托管编排环境用完立刻 destroy**。
- 仓库：避免全表扫描，用分区裁剪，设表过期策略。

### CI/CD
DAG、PySpark 脚本、SQL 全部版本控制，PR 门禁跑 `make lint` + `make test-unit`。
（当前尚无 `.github/`，是已知技术债。）

---

## 4. 相关文档

- [ADR 索引](../adr/README.md)
- [交付路线](roadmap.md)
- 各层真实进度：仓库根目录 `CLAUDE.md` 的 Implementation status 一节
