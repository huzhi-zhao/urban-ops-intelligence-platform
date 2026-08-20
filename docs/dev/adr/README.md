# Architecture Decision Records

一个 ADR 记录一个决策：**背景 → 决策 → 被否决的方案 → 后果**。

规则：

- ADR **不改名、不删除**。决策过时了就写一篇新的，把旧的标为 `Superseded by NNNN`。
- 编号连续递增，四位数，文件名 `NNNN-kebab-case-title.md`。
- 每篇顶部必须有 `Status` / `Date` 两个字段。
- 新增 ADR 后在下表登记。

ADR 记录的是**决策**，不限于技术选型：口径/语义决策（哪个字段代表什么业务含义）
与方法论决策（工作按什么流程展开）同样算，判据只有一条——**后面的人要踩着它走，
改它要付代价**。「类别」一列只为检索方便，不影响编号与流程。

| # | 标题 | 类别 | 状态 | 日期 |
|---|---|---|---|---|
| [0001](0001-terraform-and-secrets.md) | Terraform 管理 GCP 基础设施与密钥 | 栈/架构 | **Superseded by 0006** | 2026-06 |
| [0002](0002-airflow-orchestration.md) | 用 Airflow 编排增量摄取，回填留在 CLI | 栈/架构 | Accepted | 2026-06（2026-08-20 重写正文） |
| [0003](0003-incremental-bronze-pipeline.md) | Bronze 增量管道设计 | 栈/架构 | Accepted | 2026-06（2026-08-20 重写正文） |
| [0004](0004-silver-cleansing-methodology.md) | Silver 清洗规则的制定方法论 | 方法论 | Accepted | 2026-06（2026-08-20 重写正文） |
| [0005](0005-execution-architecture.md) | 执行架构：每类工作跑在哪个组件里，边界在哪 | 栈/架构 | Accepted | 2026-06-29（2026-08-20 重写正文） |
| [0006](0006-storage-compute-query-stack.md) | 全自建栈：MinIO + Spark + Trino，取消双阶段划分 | 栈/架构 | Accepted | 2026-07-30 |
| [0007](0007-clearing-completion-time-source.md) | "清雪完成时间"的数据口径：采用 plow shift 作业结束时间 | 口径/语义 | **Superseded by 0008** | 2026-08-02 |
| [0008](0008-plow-schedule-is-a-plan-not-a-record.md) | 排班表是计划而非执行记录：供给侧口径改为「排班顺位」 | 口径/语义 | Accepted | 2026-08-07 |
| [0009](0009-plow-zone-as-the-unit-of-analysis.md) | 统一报告单元：建模与评分落在作业分区，ward 只作展示 | 口径/语义 | Accepted | 2026-08-09 |
| [0010](0010-gold-fact-grain-and-dimension-layering.md) | Gold 层的事实粒度与维度分层 | 口径/语义 | **Proposed**（初稿，逐条待定） | 2026-08-09 |
| [0011](0011-bq-hypothesis-loop-and-requirement-backpropagation.md) | 需求是可证伪的假设：BQ 收敛循环与 BO → Gold → Silver 反推 | 方法论 | Accepted | 2026-08-20 |
| [0012](0012-data-quality-audit.md) | 数据质量审计方案：管道内拦截，管道外复核 | 方法论 | Accepted | 2026-08-20 |

> 编号是 2026-07-28 文档重构时统一分配的。原始文件名带 `week1/week2/week3`
> 前缀，按时间而非主题命名，已废弃。各篇的原始日期保留在 `Date` 字段里。
