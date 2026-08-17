# 开发文档

面向开发者的需求、设计与决策记录。中文书写，文件名用 English kebab-case。
对外操作手册在 [../guide/](../guide/)，两者不混放。

## 当前状态

各层的真实实现进度、已知技术债、容易记错的架构事实，统一维护在仓库根目录
`CLAUDE.md` 的 **Implementation status** 一节。本目录只讲设计意图，不重复进度。

---

## 一、两个性质，一条建目录的规则

文档不按主题分，按**两个性质**分。写之前先定位性质，落点唯一，
不需要判断"这算不算笔记"。

| **常青** —— 描述现状，原地反复改写 | **事件** —— 描述一次性事件，写完冻结、只追加不修订 |
|---|---|
| [roadmap.md](roadmap.md) 交付路线与能力阶段 | [adr/](adr/README.md) 一个**选型**的取舍 |
| [platform-architecture.md](platform-architecture.md) 系统长什么样 | [design/](design/README.md) 一次变更**打算**怎么做 |
| [data-volume-baseline.md](data-volume-baseline.md) 系统会长多大 | [launch/](launch/README.md) 一次变更**实际**怎么上的线 |
| [requirements/](requirements/) 要做什么 + 事实依据 | [postmortem/](postmortem/README.md) 已造成影响的故障复盘 |

**建目录的规则只有一条：目录给会增长的东西。**
事件类单调累积，每类都必须有目录；常青类里只有 `requirements/` 会增长
（每接一个城市多一篇调研），其余三篇不增长——系统只有一个形态、只有一条路线、
只有一份容量斜率——所以直接放顶层，不套目录。

轴外还有 [archive/](archive/README.md)：**临时中转**，三篇失效文档待迁到外部
知识平台，迁完连同目录一起删除；**已关闭，不接收新文档**。

> **`notes/` 与 `architecture/` 已于 2026-07-30 一并撤销。**
>
> `notes/` 的定义是否定式——"不属于其他任何一类"——所以只要有一篇难归类就往里塞，
> 最终六篇文档分属六种性质：调研证据、容量实测、迁移执行清单、个人学习笔记、
> 两篇失效的 BigQuery 记录。一个目录里同时有"需求的事实依据"和"个人 Airflow 笔记"，
> 读者无法预期里面有什么，写者也无从判断该不该放。
>
> `architecture/` 死于另一个原因：它只有两篇且**永不增长**。一个需要靠"长期维持在
> 个位数"来自律的目录，本身就是不该存在的目录。更实际的问题是可预测性——
> `roadmap.md` 已在顶层，若同为常青系统描述的 `platform-architecture.md` 藏在
> 子目录里，读者无从预测该去哪找。撤销后每篇的去向见文末。

### 判定顺序

自上而下问，第一个"是"就是落点：

1. 这篇会不会因为系统变了而**被改写**？会 → 常青类（`requirements/` 或顶层单篇）；
   不会（它记录的是某个时刻发生的事）→ 继续。
2. 它记录的是一个**技术选型的取舍**，且这个取舍以后可能被推翻？→ `adr/`
3. 它记录的是**一次变更打算怎么做**？→ `design/`
4. 它记录的是**一次变更实际上线的过程与验收**？→ `launch/`
5. 它记录的是**出了事之后的复盘**？→ `postmortem/`
6. 都不是 → **它多半不该进本仓库**，见下面第二节的路由表。

> 没有第 7 条。失效的旧文档不再有仓库内的去处：有追溯价值就迁到外部知识平台，
> 没有就直接删（git history 保留原文）。`archive/` 是一次性的清仓中转，不是选项。

---

## 二、不要写进 design doc 的东西

design doc 被污染的方式从来不是"写错了"，而是"写多了"——把本该在别处、
且别处天生更合适的内容塞进来。它们的共同特征是**寿命比 design doc 短**：
design doc 是给三个月后的人读的，下面这四类内容三个月后全是噪音。

| 内容 | 写到哪 | 为什么不写进 design doc |
|---|---|---|
| 这次改了哪些文件、怎么验证的、review 时要重点看哪 | **PR 描述** | 与 diff 同生命周期。PR 合并后 diff 是权威，文档里的文件清单只会过期 |
| 这一行为什么这么写、这个命名要不要换、这里少了个 null 检查 | **Code Review** | 讨论的对象是具体代码行，脱离行号就不可理解；结论若是通用规则，升格进 `CLAUDE.md` / `AGENTS.md` |
| 这个 commit 改了什么、为什么 | **Commit message** | `git log` / `git blame` 是它的索引。写进文档等于建了一份不会更新的 git 副本 |
| 进度、排期、催办、"这块谁来做"、临时阻塞、状态更新 | **Ticket comment**（完全不在 git 仓库，其他系统维护） | 天天变。放进仓库会让每次状态变化都产生一次 commit，且文档很快与真实进度不符 |

反过来说，**design doc 只写"三个月后重读仍然有用"的内容**：
问题是什么、方案的取舍、被否决的选项及原因、约束与假设、验收判据。

三条推论：

- **进度不进文档。** 本目录任何文档都不写"✅ 已完成 / ⏸️ 待办"。
  唯一的进度真相源是 `CLAUDE.md` 的 Implementation status。
  例外只有 `roadmap.md` 的阶段标记，它的对象是**能力阶段**不是任务。
- **文件级清单不进 design doc。** 逐文件的改动清单属于 PR 描述。
  ⚠️ [design/20260726-self-hosted-migration.md](design/20260726-self-hosted-migration.md)
  违反了这条（Stage G 各节列到了行数），它是撤销 `notes/` 之前写的，
  作为既成事实保留，不作为范例。
- **"我踩了个坑"不进文档。** 先判断：是**一次性的**（环境、手滑）→ 丢进
  Ticket comment；是**下次还会踩的**→ 升格为 `CLAUDE.md` / `AGENTS.md` 的
  一条规则，或 ADR 的一段 Consequences。原 `notes/` 就是死在这一条上。

---

## 三、文档清单

### 顶层单篇 —— 系统现在是什么样（常青）

- [roadmap.md](roadmap.md) —— 目标栈与各能力阶段
- [platform-architecture.md](platform-architecture.md) —— 分层设计意图、部署拓扑与关键设计考虑
- [data-volume-baseline.md](data-volume-baseline.md) —— 单行字节数与压缩比实测，容量规划与压缩策略的依据

这三篇放顶层而不是套一个 `architecture/`：它们**不增长**，目录只给会增长的东西。
`roadmap.md` 尤其如此——它横跨目标形态、能力阶段与优先级决策，塞进任何子目录都是错分。

**顶层的准入判据**：只收**当前系统的可证伪属性**——能通过读代码、跑一次、
或重新测量来推翻的陈述。"分层边界是什么""拓扑怎么分节点""年增量多少 GB"
都可证伪；"我认为应该这样设计"不可证伪，那属于 `design/` 或 ADR。

> **失效信号**：顶层常青文档若涨到五篇以上，说明判据没起作用——
> 那时才该建目录，而不是现在预建一个空壳。

当前形态是**全自建栈**（MinIO + Spark + Trino + Superset），无云托管组件。
GCP 已整体放弃，Phase 1 / Phase 2 双阶段划分已取消——见
[adr/0006](adr/0006-storage-compute-query-stack.md)。

### requirements/ —— 要做什么（常青）

- [project-overview.md](requirements/project-overview.md) —— 项目定位、城市无关性、MVP 范围
- [business-objectives.md](requirements/business-objectives.md) —— BO-1 ~ BO-8（Winnipeg 冬季运营）与预测层，含验收标准与已知约束
- [winnipeg-data-sources.md](requirements/winnipeg-data-sources.md) —— Winnipeg 数据源调研（含 SODA API 实测），上面两篇的事实依据
- [data-source-portfolio.md](requirements/data-source-portfolio.md) —— 数据源采纳台账：哪些源采纳、哪些留给 H2、启用要付什么成本
- [metric-feasibility-audit.md](requirements/metric-feasibility-audit.md) —— 指标可用性台账：每个指标的实测数字、查询入口与结论；【源实测】/【指标实测】两级标记的定义

> 调研放这里而不是 `design/`：它是**需求的证据**，不是某次变更的方案。
> 上游数据集变了要跟着改，因此是常青文档。
>
> 台账与调研分两篇而不合并：调研回答「门户上有什么」（上游变了才改），
> 台账回答「我们采纳什么、启用要付什么」（采纳决策变了才改）。
> 两者的变更触发不同，合成一篇会让每次上游微调都动到决策部分。

### adr/ —— 为什么这么选（事件，不改名不删除）

- [adr/README.md](adr/README.md) —— 索引与编号规则（0001 ~ 0009）

### design/ —— 一次变更打算怎么做（事件）

- [design/README.md](design/README.md) —— 写作契约、命名、模板
- [20260726-self-hosted-migration.md](design/20260726-self-hosted-migration.md) —— 自建栈迁移
- [20260801-snapshot-collection-deployment.md](design/20260801-snapshot-collection-deployment.md) —— 快照采集上线（BO-7 止血）
- [20260802-city-instance-switchover.md](design/20260802-city-instance-switchover.md) —— 退役存量城市实例、泛化能力、接入新实例（Phase D + Phase 2W）
- [20260808-metric-feasibility-probe.md](design/20260808-metric-feasibility-probe.md) —— 对已立项 BO 的逐指标实测复检（五问探针 + 七项任务 + 两天时间盒）
- [20260809-gold-silver-schema-derivation.md](design/20260809-gold-silver-schema-derivation.md) —— Gold / Silver 表结构推导
- [20260812-gold-bus-matrix.md](design/20260812-gold-bus-matrix.md) —— Gold 层总线矩阵
- [20260816-failure-alerting-and-followups.md](design/20260816-failure-alerting-and-followups.md) —— Airflow 失败告警 + s3a 403 事故收尾。**批 1/批 2 已于 `ba43372` 实现**（`_alerts.py` 挂 `DEFAULT_ARGS`，一处覆盖全部 DAG），批 3 日志噪音未做；欠一次端到端验证
- [20260817-etl-implementation.md](design/20260817-etl-implementation.md) —— 把 S4 建出的 25 张空表填满：4 个 Silver job + Gold DML/intelligence（E0–E6 六批）。**2026-08-17 起退为需求级总计划**，执行拆成下面三篇
- [20260817-silver-etl-runnable.md](design/20260817-silver-etl-runnable.md) —— **L1**：`silver_service_request` 的 job / 两个 DAG / 失败告警通路 / 全量回填切片，失败模式与门禁
- [20260817-gold-dimensional-build.md](design/20260817-gold-dimensional-build.md) —— **L2**（框架）：9 张维表 + 5 张描述性事实表 + 种子语义；Gold 调度入口尚未设计
- [20260817-scoring-chain-and-m1.md](design/20260817-scoring-chain-and-m1.md) —— **L3**（框架）：M1 训练 + 评分链 + DQ 基线；不是 ETL 而是建模

### launch/ —— 一次变更实际怎么上的线（事件）

- [launch/README.md](launch/README.md) —— 写作契约与模板
- [20260802-snapshot-collection-deployment-launch.md](launch/20260802-snapshot-collection-deployment-launch.md) —— 快照采集上线（执行清单先行，见篇首说明）
- [20260803-city-instance-switchover-launch.md](launch/20260803-city-instance-switchover-launch.md) —— 城市实例切换上线（批 0–3.5 已完成，§10 是 Bronze 分阶段执行计划）
- [20260813-gold-silver-schema-derivation-launch.md](launch/20260813-gold-silver-schema-derivation-launch.md) —— Gold / Silver 表结构（§2 是 S3→S4 门禁的 Schema Review：31 项发现、6 项阻塞）
- [20260814-table-creation-deployment-launch.md](launch/20260814-table-creation-deployment-launch.md) —— 25 张表建表上线（§7.2/§7.4 是写 DML 前必须先定的三条定案）
- [20260817-etl-implementation-launch.md](launch/20260817-etl-implementation-launch.md) —— E0/E1 四个小 Silver job 实测（空间命中率 **99.996%**；§2 记了四个环境坑）
- [20260817-silver-etl-runnable-launch.md](launch/20260817-silver-etl-runnable-launch.md) —— **L1** 上线（提前开篇：含 16 GB 全量回填，执行清单先行）

### postmortem/ —— 出事之后的复盘（事件）

- [postmortem/README.md](postmortem/README.md) —— 写作契约与模板（尚无复盘）

### archive/ —— 待迁出的失效文档（临时，已关闭）

- [archive/README.md](archive/README.md) —— 三篇失效文档 + 迁出后的收尾清单

---

## 四、写作规则

- 目录名用语义，不用数字前缀；数字只用于 ADR 编号。
- 文件名一律 English kebab-case，**语言差异只体现在正文**。
- 每篇文档必须被本索引恰好链接一次；没被链接的应当删除。
- 宁可合并不要拆分。**目录只给会增长的东西**——两三篇文档不配一个目录。
- **事件类文档（adr / design / launch / postmortem）写完即冻结**，
  发现结论错了写新的一篇并在旧篇标注被取代，不原地改写。
- **常青类文档（requirements/ 与三篇顶层单篇）原地改写**，不留"v1/v2"痕迹，
  历史交给 git。

---

## 附：原 notes/ 六篇的去向

| 原文件 | 去向 | 依据 |
|---|---|---|
| `winnipeg-data-sources.md` | `requirements/` | 需求的事实依据，常青 |
| `data-volume-baseline.md` | 顶层单篇 | 系统现状（容量与增长斜率），常青，不增长故不套目录 |
| `self-hosted-migration-plan.md` | `design/20260726-self-hosted-migration.md` | 一次变更的方案，事件 |
| `airflow-concepts.md` | `archive/` | 个人学习笔记，非项目知识；Composer 一节随 GCP 失效 |
| `bronze-data-exploration.md` | `archive/` | BigQuery 外部表 SQL，随 GCP 失效。其中仍有效的**数据剖析检查清单**已提炼进 [design/README.md](design/README.md) 的模板 |
| `bigquery-external-table-pitfalls.md` | `archive/` | 同上，整篇依赖 BigQuery |
