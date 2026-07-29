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
| [Operations](guide/operations.md) | Runbook：排期、故障、成本、升级人类 |

## dev/ — 开发文档

| 文档 | 内容 |
|---|---|
| [requirements/project-overview.md](dev/requirements/project-overview.md) | 项目定位、商业背景、MVP 范围 |
| [requirements/business-objectives.md](dev/requirements/business-objectives.md) | BO-1 ~ BO-5 业务目标拆解 |
| [architecture/platform-architecture.md](dev/architecture/platform-architecture.md) | 分层设计意图与关键设计考虑 |
| [architecture/roadmap.md](dev/architecture/roadmap.md) | 两个部署阶段 + 六个功能阶段 |
| [adr/](dev/adr/README.md) | 架构决策记录索引 |
| [notes/](dev/notes/) | 领域知识与踩坑笔记 |

仓库根目录另有 `CLAUDE.md` / `AGENTS.md`（AI 与人共用的强制约定）和
`.claude/rules/backfill.md`（回填层架构 + DAG 清单）。

---

## 写作规则

- 目录名用语义，不用数字前缀；数字只用于 ADR 编号。
- 文件名一律 English kebab-case，**语言差异只体现在正文**。
- 一篇文档只属于一类：`guide/` 讲怎么用，`dev/` 讲为什么这么设计。
- 每篇文档必须被本索引恰好链接一次；没被链接的应当删除。
- 宁可合并不要拆分。目标规模 ≈ 20 篇。
- 图片放 `images/`，文件名不含城市名。
