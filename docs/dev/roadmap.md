# 交付路线

> 本篇是**目标形态与阶段划分**，不是进度表。
> 当前真实进度以仓库根目录 `CLAUDE.md` 的 Implementation status 一节为准。

---

## 视野与阶段的对应

三个交付视野的定义与判据见
[requirements/project-overview.md](requirements/project-overview.md#交付视野)。
本篇的功能阶段按视野归属如下——**归属决定的是"现在做不做"，不是"重不重要"**：

| 视野 | 期限 | 覆盖的阶段 |
|---|---|---|
| **H1** 会议交付 | 2026-09-22 | Phase D（退役）· Phase 2W · Phase 3 · Phase 4 · Phase 4.5(M1) · Phase 5 |
| **H2** 企业级可复用服务 | 约 2026 年底 | Phase T（查询层）· Phase 6 · 数据质量框架 · M2 · Iceberg |
| **H3** 跨城市移植 | 多半不做 | 无阶段。只有 `CLAUDE.md` 的三条护栏 + CI grep 门禁 |

H1 的判据是"能讲"，H2 的判据是"陌生团队能接手"。任何工作在动手前先问它服务
哪个视野；服务 H2 的工作在 9 月底之前**一律推迟**，即使它看起来只要两小时。

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

> **Trino / Hive Metastore / Superset 属于 H2，不在 H1 的关键路径上。**
> 它们仍是目标形态的一部分，但 H1 所需的空间归属规模很小
> （22 个分区多边形 × 22 万个点），用 Spark 广播多边形 + 逐点判定即可完成，
> 不必为它先架起整个查询层。Gold 先落 Hive 分区 Parquet，查询层留给 H2。
>
> 反过来说，**H2 不能继续绕过它**：「陌生团队能查得到」是 H2 的判据之一，
> 而"装 Spark 写 PySpark 才能看数"不满足这条。

**Iceberg 是分阶段目标，不是当前形态。** 它的收益（ACID、Schema Evolution 吸收
上游字段变更、`MERGE INTO` 处理晚到更新、Time Travel）真实存在，但引入时机推迟到
Gold 层用 Parquet 跑通之后，避免与"首次打通 Trino + MinIO"叠加排障。

> 以下"功能阶段"的编号（Phase 0–6）指**能力交付阶段**，与已废除的部署阶段无关。
> 双阶段划分取消后这个命名不再有歧义。

---

## 功能阶段

按能力而非周次划分。每阶段有明确交付物，前一阶段不完成不进入下一阶段。

> ⏱️ **外部时间锚**：Prairie Dev Con 会期 **2026-09-21~22**（询问信已发，尚未获回复）。
> Phase D → Phase 5 是这条时间线上的关键路径。
> 哪些 BO 属于必做、哪些可切，见
> [requirements/business-objectives.md](requirements/business-objectives.md) §0.3。
> 本篇不承载进度与排期，只声明阶段依赖。

### Phase 0 · 基础设施 ✅〔已被自建栈取代〕

原为 GCP 项目与 IAM、对象存储 bucket、仓库 dataset、Terraform 声明全部资源。
云侧资源已整体放弃（[ADR 0006](adr/0006-storage-compute-query-stack.md)），
本阶段只剩 Git 仓库 + 代码规范（ruff / sqlfluff）仍然有效。
自建栈的基础设施在 Phase D 与 Phase T 分两次落地。

### Phase 1 · Bronze 摄取 ✅

四个数据源的 API 客户端（分页 + 指数退避重试）、增量拉取逻辑、
统一的 backfill 三层架构、增量 DAG + 回填 DAG + 每日 audit 自愈 DAG。

交付物：13 个 DAG，Bronze 层四源全通。

> **这四个源是 NYC 的。** 真正留下的资产是**三层 backfill 架构与 Socrata 客户端**
> ——两地门户同为 Socrata，摄取层可零改动复用。城市实例随 Phase D 退役。

### Phase 2 · Silver ETL（NYC 源）—— **退役**

原计划四源全通，实际完成 `SRC-Open-Meteo`、`SRC-DCP` 两源，
`SRC-NYC-311` / `SRC-NYPD` 从未开始。

**本阶段不再有"未完成项"**：H3 降为围栏后，NYC 存量不再承担可移植性论证，
`SRC-NYC-311` / `SRC-NYPD` 的 Silver **取消**，不是推迟
（见 [project-overview.md](requirements/project-overview.md#h3--向其他城市移植留围栏不留实现)）。

已完成的两个作业不随城市实例一起删——它们承载的是 Winnipeg 也需要的**能力**：

| 现有作业 | 保留的能力 | 去向 |
|---|---|---|
| `etl_open_meteo.py` | 气象日粒度清洗与日期分区写出 | 换坐标与端点，服务 BO-3 |
| `etl_dcp.py` + `transforms/dcp.py` | GeoJSON → WKT、几何校验、静态维度全量覆写 | 按角色名泛化，服务 BO-4 的 plow zone 边界 |

> 退役的是城市实例，不是能力。**能力泛化必须先于实例删除**——
> 先有可用的 plow zone 边界作业，再删 borough 边界作业。

### Phase D · 退役与去 NYC ❌ —— **当前里程碑，H1 的入口**

Winnipeg 开发的前置条件，也是 H2「不能内含半个别的城市」判据的一次性偿付。
三件事：

1. **删除 NYC 城市实例** —— `config/sources/` 三份 YAML、对应的 backfill 脚本、
   10 个 NYC DAG、borough 专有的 transform/schema 与测试。
2. **泛化被保留的能力** —— 见上表。通用层一律改用角色名
   （`CLAUDE.md` 城市无关护栏 §2），城市字面量只允许出现在 `config/` 与 `sql/`。
3. **清除最后的 GCP 残留** —— `infra/terraform/` 整目录。
   ⚠️ **先在 GCP 控制台撤销 service account 密钥再删本地文件**：
   删文件不等于撤销凭证。

> 本阶段**不新增任何能力**，因此可以整体并行于 BO-7 上线，且必须限时——
> 它服务的是 H2 的判据，但拖在 H1 前面做，是因为它会持续污染 H1 的每一次改动。

### Phase 2W · Winnipeg 摄取与 Silver ❌

**优先级 0（时间敏感，应先于一切开发）**：
`g3p4-h83y` Snow Clearing Status 每日快照采集。该数据集是覆盖式快照、
不保留历史，**每推迟一天上线就永久少一天历史**（BO-7）。

> 两个实现约束，均在 [ADR 0006](adr/0006-storage-compute-query-stack.md) 定案：
> 需要新增 `snapshot` 分区策略（现有四种都表达不了"无时间字段 + 按采集日分区"）；
> 且**以存储节点上的独立定时任务运行，不作为 Airflow DAG**——不可重放的采集
> 不应依赖可重建组件的可用性。采集必须流式写入，全量物化会 OOM。

**优先级 0.5（不写代码，但决定后面所有代码是否白写）**：
实测 `39ur-higg` 能否取到 22 个 plow zone 的多边形。它是 BO-4 的单点依赖，
进而是摘要「三源联结」的单点依赖。取不到就要动摘要措辞
（[ADR 0007](adr/0007-clearing-completion-time-source.md) §4.2），而改措辞需要
导师同意——**这条链路上有人的响应时延，因此必须最先发起**。

其余按依赖顺序（自建栈迁移已完成，执行记录见
[design/20260726-self-hosted-migration.md](design/20260726-self-hosted-migration.md)）：

1. Winnipeg 源 YAML + backfill 脚本（Socrata 复用，`config/sources/`）
2. 气象源改指 Winnipeg：坐标 `49.895, -97.138`，且**日粒度存档**
   （`snowfall_sum` / `temperature_2m_min|max`）与**预报**是两个数据集——
   前者供 BO-3 事件切分与 M1 训练，后者供 M1 前瞻推断
3. `u7f6-5326` 311 Bronze 回填（1,835 万行，`partition_strategy: daily`，
   `timestamp_field: open_date`）
4. `tix9-r5tc` / `mfzv-893p` / `39ur-higg` 供给侧与边界（体量极小，static 或 monthly）
5. Silver：渠道归一化（处理 2022 年 VOF 口径迁移）、
   `dim_request_type` 字典构建（3,563 个取值，解析 P1/P2/P3 与 Reg/After）

> ⚠️ 第 5 项的三类业务语义——冬季关键词、渠道映射、优先级解析——
> **一律不得写进 `spark/transforms/`**，按城市无关护栏 §1 落 `config/` 或 Gold
> 种子表。这是本阶段唯一容易违规的地方，因为它们看起来"就是几行 when/otherwise"。

### Phase 3 · Gold 建模 ❌

星型模型 DDL、维度表加载、事实表增量加载、**空间归属**（`ST_Contains`）。
`sql/ddl/`、`sql/dml/` 目录尚不存在——这是好事：**Gold 层零迁移成本**，
不存在要从 BigQuery 方言改写的 SQL，直接按 Trino 方言书写。
`.sqlfluff` 已把 dialect 钉死为 trino，第一个写下的文件就会被正确 lint。

⚠️ 空间归属比既有的 NYC 实现复杂：需求侧（242 neighbourhood / 15 ward）
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

> Phase 5 是 H1 的终点。**到这里为止，交付物是一次可信的回测**——
> 它足以支撑摘要的每一句，但还不是一套可以交给别人运维的服务。

---

## H2 阶段 · 从"能讲"到"能交接"

以下阶段全部服务 H2 的单一判据：**陌生团队照 [`guide/`](../../guide/)
能否独立跑通并接手运维。** 它们在 2026 年 9 月底之前一律不动。

### Phase T · 查询层 ❌

Hive Metastore（MySQL 后端）+ Trino + Superset。H1 用 Spark 直读 Parquet 绕过了
这一层；H2 不能绕——"必须会写 PySpark 才能看数"不满足可交接。

新增组件前先算内存预算：计算节点是唯一硬约束
（[ADR 0006](adr/0006-storage-compute-query-stack.md) §2.1 实测余量 8 GB）。

### Phase 6 · 报表与运维 ❌

Dashboard（负荷热力图 + 排名 + 建议文本）、CI/CD（PR 门禁已有，缺自动部署 DAG）、
监控告警、数据字典、runbook 补齐。

### Phase E · 环境实测与集成测试 ❌

当前全部 S3 代码只跑过 mock 单测，`tests/integration/` 在 `S3_*` 缺失时整体 skip。
H2 要求它们在真实 MinIO 上绿。这是**"能跑起来"这条判据的唯一可执行验收**。

### 数据质量框架 ❌

- Bronze 数据剖析：字段 null 率、时间戳异常值、枚举脏值、日行数连续性
- Schema 契约冻结：`contracts/` 与实际 Bronze 字段签字锁定
- 采样验证：先跑一个月验证空间命中率、时区、行数比例
- SLA 基线：每源的预期日/月行数，低于基线 50% 自动告警

方法论详见 [ADR 0004](adr/0004-silver-cleansing-methodology.md)。

> **剖析（profiling）是例外，它属于 H1。** 上面四项里只有"数据剖析"是
> Silver 逻辑的前置输入而非运维设施——不先剖析就写不出正确的去重键与清洗规则
> （[ADR 0004](adr/0004-silver-cleansing-methodology.md)）。
> 需要冻结成契约、接上告警、进 CI 的那部分才属于 H2。

### Iceberg 迁移 ❌

Parquet 跑通之后再切 connector，理由见本篇「部署形态」一节。

---

## H3 · 没有阶段

跨城市移植不排任何阶段，也不预留任何抽象层。它在本项目中只以两样东西存在：
`CLAUDE.md` 的三条城市无关护栏，与 CI 里那条 grep 门禁。
判据见 [project-overview.md](requirements/project-overview.md#h3--向其他城市移植留围栏不留实现)。
