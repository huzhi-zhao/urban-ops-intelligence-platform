# ADR 0006 — 全自建栈：MinIO + Spark + Trino，取消双阶段划分

> **Status**: Accepted · **Date**: 2026-07-30
> **Supersedes**: [ADR 0001](0001-terraform-and-secrets.md)（GCP/Terraform 部分）·
> [ADR 0005](0005-silver-execution-architecture.md) §4（"存储层仍在 GCS"的结论）

决策：**彻底放弃 GCP**。对象存储从 GCS 换成 MinIO，查询引擎从 BigQuery 换成
Trino，编排与计算维持已有的自建 Docker Airflow + Spark Standalone。
**Phase 1 / Phase 2 的双阶段划分同时取消**，`DEPLOYMENT_PHASE` 环境变量废除。

---

## 1. 背景：三次偏离累积成了一次架构重定义

原计划是"Phase 1 云上验证（GCP）→ Phase 2 自建（MinIO + Trino）"。实际发生的是
云上组件被逐个放弃，到本篇为止已无一存活：

| 组件 | 原计划 | 实际 | 记录 |
|---|---|---|---|
| Dataproc | Phase 1 计算引擎 | 放弃：节点注册失败率高 | ADR 0005 §4 |
| Cloud Composer | Phase 1 编排 | 从未部署：约 $10/天，项目不需要 | ADR 0005 §4 |
| GCS | Phase 1 存储 | **本篇放弃** | 见下 |
| BigQuery | Phase 1 仓库 | **本篇放弃** | 见下 |

放弃 GCS / BigQuery 的直接原因是 **GCP 免费额度于 2026-08 到期，且不转为付费**。
重新注册只能再延三个月。

这一点之所以是决定性的，不是因为省钱，而是因为 **BO-7 的数据资产生命周期比任何
赠金窗口都长**：`g3p4-h83y` 是覆盖式快照、不保留历史，其价值完全来自跨越一整个
冬季（11 月 – 次年 3 月）的连续每日采集。把这份正在增长的资产放在三个月后会失效
的存储上，等于计划在数据攒到一半时被迫迁移。

既然 Phase 1 的四个云组件全部作废，**"Phase 1 → Phase 2"这个划分本身已经没有
指代对象**，继续保留只会让每个新读者误以为存在一条云上路径。故一并取消。

---

## 2. 决策：目标栈

| 层 | 技术 | 说明 |
|---|---|---|
| 对象存储 | **MinIO** | Bronze / Silver / Gold 全部落此，S3 协议 |
| 计算 | **Spark 3.5.1 Standalone**（Docker） | 不变，延续 ADR 0005 |
| 编排 | **Airflow**（Docker, LocalExecutor） | 不变，延续 ADR 0002 |
| 元数据 | **Hive Metastore**（MySQL 后端） | Trino 的 Hive / Iceberg connector 前提 |
| 查询 | **Trino 451** | 取代 BigQuery，见第 3 节选型 |
| 表格式 | Hive 分区 Parquet → 后续可迁 Iceberg | 分阶段，见第 5 节 |
| BI | **Superset** | 取代 Looker Studio |

### 2.1 存储与计算分离部署

对象存储与计算部署在**两个独立节点**上：

- **存储节点**：只跑 MinIO，是项目数据的**唯一真相源**。
- **计算节点**（4 core / 24 GB ARM）：Airflow + Spark + Trino + Hive Metastore +
  Superset，全部容器化，**无状态、可重建**。

分离的理由是可用性边界不同：计算节点整体重建不损失任何数据；存储节点承载的是
不可重新获取的历史资产（BO-7 的每日快照一旦漏采就永久缺失）。两节点间走 S3
协议通信，在本项目的数据量级（单作业 1–2 GB）下网络不是瓶颈。

**容量**：存储节点为本项目专用，磁盘 100 GB，当前可用 **90 GB**。对照
[实测年增量约 10 GB](../data-volume-baseline.md)，余量约 9 年，
容量在项目生命周期内不构成约束，不需要分层归档或生命周期策略。

### 2.1.1 不做备份与容灾（显式接受的风险）

存储节点**没有备份、没有副本、没有异地冷备**，这是决策不是遗漏。

理由是本项目为实验性质，最终交付物是**论文与报告**而非线上服务，数据本身不是
需要保全的资产。丢失的实际代价分两类，均可接受：

| 数据 | 丢失后果 |
|---|---|
| 311 / 供给侧 / 气象 / 边界 | 可从上游 API 重新回填，代价是时间 |
| BO-7 快照历史 | **永久丢失**，只能从丢失当日重新开始积累 |

第二类是真实且不可逆的损失，但在"实验项目 + 单人 + 无 SLA"的前提下，为它引入
备份链路的运维成本高于其期望收益。**若后续该纵向数据集成为论文的核心贡献且已
积累一个完整冬季，应重新评估本决定**——届时资产价值与现在不是一个量级。

注意这条并不削弱 §2.1 的存算分离：分离的收益是"计算节点可以随便重建/折腾而不
牵连数据"，这一点与是否有备份无关，且在没有备份时反而更重要。

### 2.2 BO-7 快照采集不进 Airflow

`g3p4-h83y` 的每日快照采集**以独立定时任务运行在存储节点上**，不作为 Airflow DAG。

理由：它是整个项目里唯一"漏一天则永久丢一天"的任务，而 Airflow 运行在计算节点。
把它放在存储节点上，采集链路不依赖计算节点的可用性，且与 MinIO 同机零网络跳。
Airflow 上只放**可重跑**的批处理——这条边界值得作为一般原则：

> 不可重放的采集任务，其可用性不应依赖可重建的组件。

代价是这个任务不受 Airflow 的重试与告警覆盖，需要自带失败通知。

---

## 3. 查询引擎选型：Trino vs DuckDB

BigQuery 退出后需要重选查询引擎。候选是 Trino 与 DuckDB。

| 维度 | Trino | DuckDB | 对本项目的权重 |
|---|---|---|---|
| Iceberg 读写 | connector 成熟，`MERGE INTO` / schema evolution / time travel 齐全 | 偏读，**不能写** | **高** —— 311 的 7 天回溯去重需要 `MERGE` |
| 内存占用 | JVM，单节点堆 4–6 GB + Metastore ≈ 1 GB | 单进程 2 GB 起，超限自动 spill | **高** —— 计算节点是唯一硬约束 |
| 数据量级适配 | MPP，收益自 TB 级起 | 亿行以内单机最优 | 低 —— 本项目约 10 GB/年，两者均过剩 |
| 空间连接（BO-4） | 有 `ST_Contains`；分布式空间连接需配 KDB-tree 分区才高效 | `spatial` 扩展 + RTree，可直读 GeoJSON | 低 —— 220K 点 × 242 多边形两者都是分钟级 |
| 联邦查询 | 核心能力 | 无 | **零** —— 所有数据同在一个对象存储，无跨源 JOIN |
| 并发 | 多租户为设计目标 | 单进程 | **零** —— 单作者 + 单看板 |

**结论：选 Trino。**

关键在于 Trino 的三个招牌优势（MPP、联邦、并发）**在本项目一个都用不上**，而它唯一
真正有用的能力 —— Iceberg 写入 —— 恰好命中 roadmap 早已记录的需求。反过来，
DuckDB 的优势（轻量）在实测后失去了前提：计算节点上 Trino + Hive Metastore
已随其他服务常驻，实测 23 GB 总内存中 14 GB 已用、**8 GB 可用**，即 Trino 的开销
已经被验证容纳得下，不是待评估的风险。

Trino 的内存需求与数据量关系不大：它流式做聚合，占内存的是 hash table 而非数据集。
本项目最大查询是 1,835 万行按 neighbourhood(242) × type(3,563) 分组，hash table 很小。

次要因素：项目既有编码规范（`CLAUDE.md`）本就把 `trino` 列为 SQL dialect 之一，
选 Trino 的文档改动面小于选 DuckDB。

**未被完全排除**：DuckDB 保留作为本地探查与数据剖析工具。但**生产 SQL 只有一种
dialect**——`sql/` 目录下全部按 Trino 方言书写与 lint。两套方言并行维护是明确的
反模式。

---

## 4. Bronze 格式变更：`.ndjson.gz`

Bronze 数据文件从 `.ndjson` 改为 **gzip 压缩的 `.ndjson.gz`**；manifest 保持
未压缩的 `.json`（每份仅数百字节，压缩无收益，且保持可直接 `head` 查看）。

压缩不是优化项而是前置条件：BO-7 每日快照未压缩为 184 MB/天，压缩后 18.5 MB/天，
差 10 倍。实测压缩比见 [../data-volume-baseline.md](../data-volume-baseline.md)。

### 4.1 必须改文件名，不能只设 `Content-Encoding`

Spark 的 `s3a://` reader **不解析 HTTP 响应头**，它依据**文件扩展名**选择解压
codec。若仅设置 `Content-Encoding: gzip` 而文件仍名为 `.ndjson`，
`spark.read.json()` 会把 gzip 二进制当文本读入 —— **不报错，产出乱码行**。
这是最难定位的一类失败。

因此：改扩展名，且**不要**设置 `Content-Encoding`（设置了会导致部分 S3 客户端
二次解压）。

### 4.2 manifest 语义

`sha256_checksum` 与 `file_size_bytes` 继续描述**未压缩的 NDJSON 载荷**（它们
描述的是数据，不是存储 blob），新增两个字段：

- `compression`: `"gzip"` | `null`
- `stored_bytes`: 压缩后的对象大小

如此 manifest 的幂等校验语义不变——同一批记录重跑得到相同 sha256，不受 gzip
内嵌时间戳影响。

---

## 5. 分阶段引入 Iceberg

**先 Hive 分区 Parquet，后 Iceberg。**

第一阶段 Spark 写 Hive 分区 Parquet、Trino 用 Hive connector 直读（Metastore 已
就绪），把 Gold 层跑通。待 311 的晚到更新真正构成问题时，再切 Iceberg connector
引入 `MERGE INTO`。

理由是排障成本：Iceberg 的复杂度不应与"首次让 Trino 读通 MinIO"叠加。两个问题
分开各是半小时，叠在一起是一整天。

---

## 6. 新增 `snapshot` 分区策略

现有四种 `partition_strategy` 都无法表达"按采集日快照一个无时间字段的数据集"：

- `daily` 强制每个 dataset 声明 `timestamp_field`，而 `g3p4-h83y` **没有任何时间字段**
- `static` 写死单一文件名，次日覆盖前一日 —— 恰好是要避免的

故新增 `snapshot`：按**采集日**（而非记录日期）分区，允许 `timestamp_field: null`。
路径布局复用 `ingestion/loaders` 中已存在但此前未被 facade 调用的
`ingest_date=YYYY-MM-DD/` 布局。

---

## 7. 被否决的方案

| 方案 | 否决理由 |
|---|---|
| 续用 GCP，转为付费 | 不接受长期付费；且已无云上组件存活，付费只为存储 |
| 重新注册 GCP 账号 | 只延三个月，与 BO-7 的跨冬季生命周期根本不匹配 |
| 数据与计算同节点（都放计算节点） | 数据资产会与可重建的无状态组件共享故障域 |
| 用计算节点已有的 HDFS 作为数据湖存储 | 单节点 HDFS 无冗余优势，且把真相源移入可重建节点，违背 2.1 |
| Trino + DuckDB 双方言并行 | `sql/` 维护两套方言，半年后不可维护 |
| 一开始就上 Iceberg | 与"首次打通 Trino + MinIO"叠加，排障成本非线性上升 |
| BO-7 作为 Airflow DAG | 不可重放的采集依赖可重建组件的可用性 |
| 为存储节点建备份 / 异地冷备 | 实验项目，交付物是论文而非服务；运维成本高于期望收益，见 §2.1.1 |
| 迁移 GCS 上的 NYC Bronze 存量到 MinIO | 同上——NYC 存量可从 Socrata 重新回填，且仅作可移植性基线，不值得为它赶在额度到期前做一次性搬运 |

---

## 8. 后果

### 8.1 消失的技术债

以下三项随 GCP 一起作废，**不再需要处理**：

- `google_composer_environment.main` 一旦 apply 就产生约 $10/天账单的陷阱
- `google_bigquery_dataset.main` 在 tfstate 中错误指向旧 project `pace-lab-bdp`，
  与 IAM 授权的 project 不一致，Gold 层写入必然失败
- Terraform 本地 state 无远程后端、丢失即需重新 import

`infra/terraform/` 整个目录退役。

### 8.2 需要改动的代码面

约 25 个文件涉及 GCP。按性质分类：

| 性质 | 范围 |
|---|---|
| 存储客户端重写 | `ingestion/loaders/`：只有客户端构造与上传方法碰存储；路径构造、manifest 生成、NDJSON 序列化三块逻辑与存储无关，原样保留 |
| Spark 配置与路径 | `fs.gs.*` → `fs.s3a.*`；`gs://` → `s3a://`；`.ndjson` → `.ndjson.gz`。ADR 0005 记录的 Python 3.11 / PYTHONPATH 相关 conf 与存储无关，**必须保留**（Shapely UDF 依赖） |
| 客户端机械替换 | facade / 审计 DAG / 回填脚本 / 剖析脚本 |
| 配置 | 新增 `snapshot` 策略；`GCS_BUCKET_NAME` → S3 系列环境变量；**删除 `DEPLOYMENT_PHASE`** |
| 删除 | `infra/terraform/`、Makefile 的 Composer / Terraform target、GCP SA key 挂载 |
| 测试 | 集成测试改指向本地 MinIO 容器（比 GCS 更易测——可真实往返） |

Bronze 审计 DAG 有一处运气好：它按**精确路径检查 manifest 存在性**，而 manifest
保持未压缩的 `.json`，故**缺口检测逻辑无需改动**，仅换客户端。

### 8.3 实现约束（会消耗整天的坑）

1. **`hadoop-aws` 版本必须精确匹配 Spark 自带的 Hadoop 版本。** Spark 3.5.1 自带
   Hadoop 3.3.4 → 必须 `hadoop-aws:3.3.4` + `aws-java-sdk-bundle:1.12.262`。
   差一个小版本即 `NoSuchMethodError`，与 ADR 0005 中 GCS connector 遭遇的
   是同一类 classpath 冲突。用 `--jars` 挂精确版本，不要用 `--packages`。
2. **MinIO 必须启用 path-style access**（`fs.s3a.path.style.access=true`）。
   MinIO 无 virtual-host 风格 DNS，不设则全部请求解析失败。
3. **S3 密钥不得经 `--conf` 传递。** 会出现在 Spark UI 环境页、进程列表与 Airflow
   任务日志中。走 worker 上的 `spark-defaults.conf`（权限 600）或环境变量注入。
   此条是 `AGENTS.md` 安全规则的直接应用。
4. **快照采集必须流式写入。** 现有 fetch 路径把全量记录物化为 Python list；
   23.8 万条记录的快照在小内存节点上会 OOM。需分页拉取、边写临时 gzip 文件、
   再分片上传，使内存占用只与单页大小成正比。其他源不受影响——回填按天/月切片，
   311 的 1,835 万行摊到 18 年约每天 2,800 行，每片都很小。

### 8.4 文档影响

本篇为设计意图，属 `dev/`。对外手册 `guide/` 描述的是**系统实际怎么用**，应在代码
迁移完成后再更新，而非现在——否则手册会描述一个还不存在的系统。
`CLAUDE.md` / `AGENTS.md` / `.claude/rules/backfill.md` 中的 GCP 陈述需同步修正。

一处规范变更：`CLAUDE.md` 的「Escalate to human when」规定空间连接 NULL > 10%
需上报。Winnipeg 311 全表 79% 无地理信息属**上游固有特性**，按现行阈值会持续
误报。阈值分母须改为"有地理信息的子集"。详见
[business-objectives.md](../requirements/business-objectives.md) §2.1。
