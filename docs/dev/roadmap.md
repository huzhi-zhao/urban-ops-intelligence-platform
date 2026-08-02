# 交付路线

> 本篇是**目标形态与阶段划分**，不是进度表。
> 当前真实进度以仓库根目录 `CLAUDE.md` 的 Implementation status 一节为准。

---

## 部署形态：单一自建栈

> **双阶段划分已于 2026-07-30 取消。** 原计划"Phase 1 云上（GCP）→ Phase 2 自建"
> 的四个云组件被逐个放弃，到最后无一存活，这个划分已无指代对象。
> `DEPLOYMENT_PHASE` 环境变量随之废除。决策与取舍见
> [ADR 0006](adr/0006-storage-compute-query-stack.md)。

| 层 | 技术 |
|---|---|
| 对象存储 | MinIO（S3 协议） |
| 计算 | Spark 3.5.1 Standalone（Docker） |
| 编排 | Airflow（Docker, LocalExecutor） |
| 元数据 | Hive Metastore（MySQL 后端） |
| 查询 | Trino |
| 表格式 | Hive 分区 Parquet → 后续可迁 Iceberg |
| BI | Superset |
| 监控 | Airflow 告警（Prometheus + Grafana 为未来项） |

存储与计算分离部署在两个节点上，依据是可用性边界而非性能——见
[platform-architecture.md](platform-architecture.md) §1.1。

> **Trino / Hive Metastore / Superset 不在首轮交付的关键路径上。**
> 它们仍是目标形态的一部分，但首轮交付所需的空间归属规模很小
> （22 个分区多边形 × 22 万个点），用 Spark 广播多边形 + 逐点判定即可完成，
> 不必为它先架起整个查询层。Gold 先落 Hive 分区 Parquet，查询层有余力再补。

**Iceberg 是分阶段目标，不是当前形态。** 它的收益（ACID、Schema Evolution 吸收
上游字段变更、`MERGE INTO` 处理晚到更新、Time Travel）真实存在，但引入时机推迟到
Gold 层用 Parquet 跑通之后，避免与"首次打通 Trino + MinIO"叠加排障。

> 以下"功能阶段"的编号（Phase 0–6）指**能力交付阶段**，与已废除的部署阶段无关。
> 双阶段划分取消后这个命名不再有歧义。

---

## 功能阶段

按能力而非周次划分。每阶段有明确交付物，前一阶段不完成不进入下一阶段。

> ⏱️ **外部时间锚**：Prairie Dev Con 会期 **2026-09-21~22**（询问信已发，尚未获回复）。
> Phase 2W → Phase 5 是这条时间线上的关键路径。
> 哪些 BO 属于必做、哪些可切，见
> [requirements/business-objectives.md](requirements/business-objectives.md) §0.3。
> 本篇不承载进度与排期，只声明阶段依赖。

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
> （见 [requirements/business-objectives.md](requirements/business-objectives.md)）。
> NYC 的 311/NYPD Silver 不再是下一个里程碑，作为可移植性基线保留。

### Phase 2W · Winnipeg 摄取与 Silver ❌ —— **当前里程碑**

**优先级 0（时间敏感，应先于一切开发）**：
`g3p4-h83y` Snow Clearing Status 每日快照采集。该数据集是覆盖式快照、
不保留历史，**每推迟一天上线就永久少一天历史**（BO-7）。

> 两个实现约束，均在 [ADR 0006](adr/0006-storage-compute-query-stack.md) 定案：
> 需要新增 `snapshot` 分区策略（现有四种都表达不了"无时间字段 + 按采集日分区"）；
> 且**以存储节点上的独立定时任务运行，不作为 Airflow DAG**——不可重放的采集
> 不应依赖可重建组件的可用性。采集必须流式写入，全量物化会 OOM。

其余按依赖顺序（**逐文件的执行清单见
[design/2026-07-self-hosted-migration.md](design/2026-07-self-hosted-migration.md)**）：

0. 迁移到自建栈：MinIO loader + gzip + `snapshot` 策略（阻塞以下全部）
1. Winnipeg 源 YAML + backfill 脚本（Socrata 复用，`config/sources/`）
2. `u7f6-5326` 311 Bronze 回填（1,835 万行，`partition_strategy: daily`，
   `timestamp_field: open_date`）
3. `tix9-r5tc` / `mfzv-893p` / `39ur-higg` 供给侧与边界（体量极小，static 或 monthly）
4. Silver：渠道归一化（处理 2022 年 VOF 口径迁移）、
   `dim_request_type` 字典构建（3,563 个取值，解析 P1/P2/P3 与 Reg/After）

### Phase 3 · Gold 建模 ❌

星型模型 DDL、维度表加载、事实表增量加载、**空间归属**（`ST_Contains`）。
`sql/ddl/`、`sql/dml/` 目录尚不存在——这是好事：**Gold 层零迁移成本**，
不存在要从 BigQuery 方言改写的 SQL，直接按 Trino 方言书写。
建目录时一并补 `.sqlfluff` 配置把 dialect 钉死（当前仓库无此文件）。

⚠️ Winnipeg 部署的空间归属比 NYC 复杂：需求侧（242 neighbourhood / 15 ward）
与供给侧（22 plow zone）是**三套互不嵌套的几何**，`dim_geography` 必须同时
承载三种归属并产出交叉映射表。详见 business-objectives BO-4。

需求侧不需要空间连接——`neighbourhood` 与 `ward` 是 311 行上的文本字段。
空间工作全部集中在一件事上：把只有 plow_zone 粒度的作业完成时间接到报告粒度上。
这张映射表是对外承诺的"三源联结"的**单点依赖**，且它依赖一个未实测的边界数据源
（`39ur-higg`）——首轮交付的最大风险，须最先验证。

⚠️ 空间命中率告警的分母必须是**有地理信息的子集**——311 全表仅 20.9% 带坐标
（冬季子集 80.1%），这是上游固有特性而非管道缺陷，按全表算会持续误报。

> 此前列在这里的阻塞项「仓库 dataset 所属 project 错位」**已随 GCP 放弃而消失**
> （ADR 0006 §8.1）。Gold 层现在没有遗留的基础设施阻塞项。

### Phase 4 · 智能引擎 ❌

`calc_load_score.sql`、`calc_operational_drivers.sql`（规则识别高负荷来源），
结果落 `fact_winter_event_zone_load`。

Winnipeg 部署的评分公式（权重待标定）：

```
0.40 × 预测服务请求量 + 0.30 × 作业缺口 + 0.30 × 天气严重度
```

请求量项取 Phase 4.5 的 M1 预测值，评分因此是前瞻的而非回顾的。
绝对量按**分区地址数**归一化（`g3p4-h83y` 一次全量拉取即得，无需历史）。
早期版本的应急事件项（WFPS）与路网公里数分母均已移出——两个源都只有元数据、
未经实测，理由见 [business-objectives.md](requirements/business-objectives.md) BO-6。

> 🔴 **有效分析窗口为 2015-12 起**（供给侧 `tix9-r5tc` 的起始时间，约 10 个冬季）。
> 更早的降雪事件没有排班记录，但**缺失不等于缺口**——作业缺口因子在该区间标记
> NULL，不得填 0，否则会凭空造出 7 个冬季的假缺口并传导到 Phase 5 的排序建议。
> 2008–2015 仅用于 M1 的需求侧训练。

### Phase 4.5 · 预测层 ❌

对外标题中的 "AI-Driven" 落在这里。两个模型，读 Gold 层 Parquet，
输出写回 Gold 供 Phase 4 与 Phase 5 消费：

| | 目标 | 单元 | 分级 |
|---|---|---|---|
| **M1** 需求预测 | 给定降雪预报，预测各选区在下一次降雪事件中的请求量 | ward × 降雪事件 | **P0 必做** |
| **M2** 超时风险 | 受理时刻预测工单关闭时长 / 超时概率 | 单工单 | P1（摘要未要求） |

模型清单、特征纪律（防泄漏）、评估协议（时序切分 + 强制基线）见
[business-objectives.md](requirements/business-objectives.md) §4。

> **建模不用 Spark MLlib。** ETL 用 Spark，建模在单机读 Gold Parquet——
> ward × 事件面板与 27 万行工单都是单机秒级的量，为"大数据"上 MLlib 只会
> 拖慢迭代且无收益。这与 Phase 2 的 Spark 选型不矛盾：两者处理的数据量差三个数量级。

### Phase 5 · 推荐引擎 ❌

负荷分区间 × 驱动因素 → 部门建议（BO-8）。由 M1 / M2 的预测值排序驱动；
`dim_recommendation_rules` 表保留，但角色变为**归因文字模板**与
**模型不可用时的降级兜底**，不再是主逻辑。

### Phase 6 · 报表与运维 ❌

Dashboard（负荷热力图 + 排名 + 建议文本）、CI/CD（PR 门禁 + 自动部署 DAG）、
监控告警、数据字典。

---

## 数据质量框架（Phase 2+ 持续）

- Bronze 数据剖析：字段 null 率、时间戳异常值、枚举脏值、日行数连续性
- Schema 契约冻结：`contracts/` 与实际 Bronze 字段签字锁定
- 采样验证：先跑一个月验证空间命中率、时区、行数比例
- SLA 基线：每源的预期日/月行数，低于基线 50% 自动告警

方法论详见 [ADR 0004](adr/0004-silver-cleansing-methodology.md)。
