# Documentation

文档分两类，受众不同、语言不同，互不混放。

| 目录 | 受众 | 语言 | 内容 |
|---|---|---|---|
| **[guide/](guide/)** | 外部读者、使用者 | English only | 平台是什么、有哪些能力、怎么用、怎么排障 |
| **[dev/](dev/)** | 开发者 | 中文可 | 需求、架构设计、决策记录（ADR）、技术笔记 |

根目录 [README.md](../README.md) 只链接 `guide/`。

---

## guide/ — Operations manual

| 文档 | 内容 |
|---|---|
| [Getting Started](guide/getting-started.md) | 安装、配置、质量门禁、起停服务 |
| [Architecture](guide/architecture.md) | 分层、组件职责、部署阶段 |
| [Data Sources](guide/data-sources.md) | 数据源登记表、接入新源/新城市 |
| [Ingestion & Bronze](guide/ingestion-bronze.md) | Bronze 布局、分区策略、增量与自愈 |
| [Silver ETL](guide/silver-etl.md) | Silver 作业规范与进度 |
| [Backfill](guide/backfill.md) | CLI 与 DAG 回填 |
| [Snapshot Collection](guide/snapshot-collection.md) | 不可重放的每日快照采集：部署、告警、排障 |
| [Operations](guide/operations.md) | Runbook：排期、故障、成本、升级人类 |

## dev/ — 开发文档

文档分两性质：**常青**（描述现状，原地反复改写）与**事件**（描述一次性事件，
写完冻结、只追加）。**目录只给会增长的东西**——事件类单调累积，所以有目录；
常青类除需求外不增长，直接放 `dev/` 顶层。判定顺序与写作契约见
[dev/README.md](dev/README.md)。

**常青 —— 系统现在是什么样**

| 文档 | 内容 |
|---|---|
| [roadmap.md](dev/roadmap.md) | 目标栈 + 七个能力阶段 |
| [platform-architecture.md](dev/platform-architecture.md) | 分层设计意图、部署拓扑与关键设计考虑 |
| [data-volume-baseline.md](dev/data-volume-baseline.md) | 单行字节数与压缩比实测，容量规划依据 |
| [requirements/project-overview.md](dev/requirements/project-overview.md) | 项目定位、商业背景、MVP 范围 |
| [requirements/business-objectives.md](dev/requirements/business-objectives.md) | BO-1 ~ BO-7 业务目标拆解 |
| [requirements/winnipeg-data-sources.md](dev/requirements/winnipeg-data-sources.md) | Winnipeg 数据源调研（SODA API 实测），上面两篇的事实依据 |

**事件 —— 发生过什么（写完冻结，按目录累积）**

| 目录 | 内容 |
|---|---|
| [adr/](dev/adr/README.md) | 一个**选型**的取舍，不改名不删除 |
| [design/](dev/design/README.md) | 一次变更**打算**怎么做 |
| [launch/](dev/launch/README.md) | 一次变更**实际**怎么上的线 |
| [postmortem/](dev/postmortem/README.md) | 已造成影响的故障复盘 |
| [archive/](dev/archive/README.md) | 🚚 **临时**：三篇失效文档待迁到外部知识平台，迁完连同目录删除。已关闭，不接收新文档 |

> `dev/notes/` 已于 2026-07-30 撤销——它的定义是否定式（"不属于其他任何一类"），
> 因此成了六种不同性质文档的堆放处。同批撤销的还有 `dev/architecture/`：
> 它只有两篇且永不增长，两篇文档撑一个目录是噪音。六篇的去向见
> [dev/README.md](dev/README.md#附原-notes-六篇的去向)。

仓库根目录另有 `CLAUDE.md` / `AGENTS.md`（AI 与人共用的强制约定）和
`.claude/rules/backfill.md`（回填层架构 + DAG 清单）。

---

## 写作规则

- 目录名用语义，不用数字前缀；数字只用于 ADR 编号。
- 文件名一律 English kebab-case，**语言差异只体现在正文**。
- 一篇文档只属于一类：`guide/` 讲怎么用，`dev/` 讲为什么这么设计。
- 每篇文档必须被本索引恰好链接一次；没被链接的应当删除或进 `dev/archive/`。
- 宁可合并不要拆分。目标规模 ≈ 20 篇。
- 图片放 `images/`，文件名不含城市名。

### 有四类内容不进本仓库的文档

它们寿命比文档短，且在别处天生更合适。写之前先对一遍这张表，
细则与推论见 [dev/README.md](dev/README.md#二不要写进-design-doc-的东西)：

| 内容 | 写到哪 |
|---|---|
| 这次改了哪些文件、怎么验证的 | **PR 描述** |
| 这一行为什么这么写、命名、遗漏的检查 | **Code Review** |
| 这个改动做了什么、为什么 | **Commit message** |
| 进度、排期、催办、临时阻塞、状态更新 | **Ticket comment**（不在 git 仓库，其他系统维护） |
