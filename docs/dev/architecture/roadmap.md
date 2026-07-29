# 交付路线

> 本篇是**目标形态与阶段划分**，不是进度表。
> 当前真实进度以仓库根目录 `CLAUDE.md` 的 Implementation status 一节为准。

---

## 两个部署阶段

| | Phase 1 | Phase 2 |
|---|---|---|
| 定位 | 云上 PoC/MVP，验证端到端可行性与成本 | 自建集群，生产级可靠性 |
| 存储 | GCS | MinIO |
| 表格式 | Parquet + BigQuery 管理表 | Apache Iceberg |
| 计算 | 自建 Docker Spark Standalone | Spark（Standalone / K8s） |
| 查询 | BigQuery | Trino |
| 元数据 | BigQuery 自带 | Hive Metastore 或 Nessie |
| 编排 | Docker Airflow | Docker / K8s Airflow（HA） |
| 监控 | 云监控 + Airflow 告警 | Prometheus + Grafana + Loki |
| 可视化 | Looker Studio | Superset / Metabase |

用 `DEPLOYMENT_PHASE=1|2` 切换。**当前处于 Phase 1。**

> Phase 1 原计划用 GCP Dataproc + Cloud Composer，两者都已放弃：
> Dataproc 节点注册失败率高，Composer 约 $10/天而项目并不需要它。
> 计算与编排都改为自建 Docker，**存储层不变，仍在 GCS**。
> 详见 [ADR 0005](../adr/0005-silver-execution-architecture.md) §4。

Phase 2 相对 Phase 1 的核心增益是 **Iceberg**：ACID 事务、Schema Evolution
（吸收上游 API 字段变更）、`MERGE INTO`（生产级去重与晚到更新）、Time Travel。

---

## 功能阶段

按能力而非周次划分。每阶段有明确交付物，前一阶段不完成不进入下一阶段。

### Phase 0 · 基础设施 ✅

GCP 项目与 IAM（最小权限服务账号）、对象存储 bucket、仓库 dataset、
Git 仓库 + 代码规范（ruff / sqlfluff）、Terraform 声明全部资源。

### Phase 1 · Bronze 摄取 ✅

四个数据源的 API 客户端（分页 + 指数退避重试）、增量拉取逻辑、
统一的 backfill 三层架构、增量 DAG + 回填 DAG + 每日 audit 自愈 DAG。

交付物：13 个 DAG，Bronze 层四源全通。

### Phase 2 · Silver ETL 🟡 2/4（NYC 源）

PySpark 清洗管道：Schema 强制、类型转换、去重、UTC 标准化、
被拒行落 `_rejects/`、行数基线告警。

- ✅ `SRC-Open-Meteo`、`SRC-DCP`
- ⏸️ `SRC-NYC-311`、`SRC-NYPD` —— **已让位于 Winnipeg 方向，见下**

311 的难点是 7 天回溯窗口造成的重复，去重键要定清楚；
NYPD 的难点是 `crash_date` + `crash_time` 两字段拼 UTC 时间戳。

> **优先级变更（2026-07-29）**：当前新增开发的目标城市已切换为 Winnipeg
> （见 [requirements/business-objectives.md](../requirements/business-objectives.md)）。
> NYC 的 311/NYPD Silver 不再是下一个里程碑，作为可移植性基线保留。

### Phase 2W · Winnipeg 摄取与 Silver ❌ —— **当前里程碑**

**优先级 0（时间敏感，应先于一切开发）**：
`g3p4-h83y` Snow Clearing Status 每日快照采集 DAG。该数据集是覆盖式快照、
不保留历史，**每推迟一天上线就永久少一天历史**（BO-7）。

其余按依赖顺序：

1. Winnipeg 源 YAML + backfill 脚本（Socrata 复用，`config/sources/`）
2. `u7f6-5326` 311 Bronze 回填（1,835 万行，`partition_strategy: daily`，
   `timestamp_field: open_date`）
3. `tix9-r5tc` / `mfzv-893p` / `39ur-higg` 供给侧与边界（体量极小，static 或 monthly）
4. Silver：渠道归一化（处理 2022 年 VOF 口径迁移）、
   `dim_request_type` 字典构建（3,563 个取值，解析 P1/P2/P3 与 Reg/After）

### Phase 3 · Gold 建模 ❌

星型模型 DDL、维度表加载、事实表增量加载（外部表 → 管理表）、
**空间归属**（`ST_CONTAINS`）。`sql/ddl/`、`sql/dml/` 目录尚不存在。

⚠️ Winnipeg 部署的空间归属比 NYC 复杂：需求侧（242 neighbourhood / 15 ward）
与供给侧（22 plow zone）是**三套互不嵌套的几何**，`dim_geography` 必须同时
承载三种归属并产出面积加权映射表。详见 business-objectives BO-4。

⚠️ 空间命中率告警的分母必须是**有地理信息的子集**——311 全表仅 20.9% 带坐标
（冬季子集 80.1%），这是上游固有特性而非管道缺陷，按全表算会持续误报。

⚠️ 开工前先解决仓库 dataset 所属 project 错位的问题，见交接文档第 8 节。

### Phase 4 · 智能引擎 ❌

`calc_load_score.sql`、`calc_operational_drivers.sql`（规则识别高负荷来源），
结果落 `fact_winter_event_zone_load`。

Winnipeg 部署的评分公式（权重待标定）：

```
0.35 × 加权服务请求量 + 0.30 × 作业缺口 + 0.20 × 天气严重度 + 0.15 × 应急事件
```

绝对量需按路网公里数归一化为单位路网负载。
另有一项独立交付物：**SLA 合规性审计**（BO-5），利用 `type` 字段内嵌的
官方 P1/P2/P3 口径计算达成率——这是官方工具不做、且论文分量最重的部分。

### Phase 5 · 推荐引擎 ❌

负荷分区间 × 驱动因素 → 部门建议。规则存 `dim_recommendation_rules` 表而非
硬编码在 SQL 里，便于运营侧维护。

### Phase 6 · 报表与运维 ❌

Dashboard（负荷热力图 + 排名 + 建议文本）、CI/CD（PR 门禁 + 自动部署 DAG）、
监控告警、数据字典。

---

## 数据质量框架（Phase 2+ 持续）

- Bronze 数据剖析：字段 null 率、时间戳异常值、枚举脏值、日行数连续性
- Schema 契约冻结：`contracts/` 与实际 Bronze 字段签字锁定
- 采样验证：先跑一个月验证空间命中率、时区、行数比例
- SLA 基线：每源的预期日/月行数，低于基线 50% 自动告警

方法论详见 [ADR 0004](../adr/0004-silver-cleansing-methodology.md)。
