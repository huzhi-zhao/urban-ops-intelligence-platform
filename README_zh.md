# NYC 城市运营智能平台

**[English](./README.md)**

---

## 项目概述

NYC-UOIP 是一个生产级的 Lakehouse 数据管道，整合纽约市开放数据（311 投诉、交通事故、气象数据），每日自动生成各行政区**运营负荷评分**及资源配置建议。

> **这意味着什么？** 想象一下，市政呼叫中心需要预测："明天哪些地区服务请求会激增？哪些行政区需要更多救护车？暴风雪前是否应该增加供暖投诉接线员？" 这个平台可以自动回答这些问题。

### 架构图

```
数据源 (Socrata / Open-Meteo API)
         ↓
数据摄入层 (Airflow 增量拉取)
         ↓
Bronze层  →  Silver层  →  Gold层
(原始JSON)    (Parquet)     (BigQuery / Iceberg)
                                    ↓
                          运营智能引擎
                                    ↓
                          可视化 / 决策建议
```
![](docs/images/nyc_uoip_architecture.svg)
### 包结构
[完整包结构（PDF）](./docs/images/NYC-UOIP-Repository%20Structure.pdf)

```
nyc-uoip/
├── ingestion/           # API 客户端和数据加载器
│   ├── clients/         # Socrata、Open-Meteo 封装
│   ├── loaders/         # GCS / MinIO Bronze 层写入
│   └── schemas/         # Pydantic 数据验证模型
├── spark/               # PySpark ETL 作业
│   ├── jobs/            # 入口脚本（每个数据集一个）
│   ├── transforms/      # 可复用转换函数
│   └── schemas/         # Silver 层 StructType 定义
├── sql/
│   ├── ddl/             # 建表语句
│   ├── dml/             # 增量 MERGE/INSERT
│   └── intelligence/    # 负荷评分和推荐 SQL
├── dags/                # Airflow DAG 定义（仅调度逻辑）
├── contracts/           # 数据源注册表和数据契约
├── infra/
│   ├── terraform/       # GCP 资源（第一阶段）
│   └── docker/          # 自托管部署（第二阶段）
└── tests/
    ├── unit/            # Python 单元测试（无需 Spark/云服务）
    └── fixtures/        # API 模拟响应数据
```

### 数据源

| 数据集 | 来源 | 关键字段 |
|--------|------|----------|
| NYC 311 投诉 | Socrata API | 创建时间、投诉类型、行政区、位置 |
| 交通事故 | Socrata API | 事故时间、行政区、伤亡人数、原因 |
| 气象数据 | Open-Meteo API | 温度、降雪量、降雨量、风速 |
| 行政区边界 | NYC Open Data (GeoJSON) | 地理多边形，用于空间关联 |

### 数据分层

| 层级 | 格式 | 说明 |
|------|------|------|
| **Bronze** | 原始 JSON/GeoJSON | 不可变历史快照，按日期分区存储 |
| **Silver** | Parquet | 清洗、验证、去重后的数据 |
| **Gold** | BigQuery / Iceberg | 星型模型、空间分析、负荷评分 |

### 核心产出

1. **运营负荷评分 (Operational Load Score)** — 预测各行政区未来24小时服务需求（0-100分制）
2. **驱动因素分析 (Driver Analysis)** — 解释高负荷原因（天气影响、311投诉量、交通事故规律）
3. **资源配置建议 (Resource Recommendations)** — 可执行的具体建议（如："布鲁克林供暖投诉队列 +15% 接线员"）

### 两阶段交付

| 阶段 | 技术栈 | 适用场景 |
|------|--------|----------|
| **第一阶段** | GCP (GCS + Dataproc + BigQuery + Cloud Composer) | 云原生企业部署 |
| **第二阶段** | 自托管 (MinIO + Spark + Iceberg + Trino + Docker Airflow) | 本地开发 / 私有化部署 |

通过环境变量 `DEPLOYMENT_PHASE=1` 或 `DEPLOYMENT_PHASE=2` 切换。

### 快速开始

```bash
# 安装依赖
make install

# 代码检查和测试
make lint
make test-unit

# 本地提交 Spark 作业
# （etl_nyc_311.py / etl_nypd_collisions.py 尚未实现，见 CLAUDE.md 的实现状态一节）
make spark-submit JOB=spark/jobs/etl_open_meteo.py

# 启动第二阶段 Docker 环境
docker compose -f infra/docker/docker-compose.yml up -d
```

### 关键约定

- **ETL 作业必须幂等** — 相同 `execution_date` 重复运行产生相同结果
- **DAG 仅含调度逻辑** — 业务逻辑放在 `ingestion/` 或 `spark/`
- **所有时间戳使用 UTC** — 使用 `timestamp_normalizer.py` 进行转换
- **Gold 层 SQL 禁止 `SELECT *`** — 必须明确列出列名