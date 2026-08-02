# Architecture Decision Records

一个 ADR 记录一个决策：**背景 → 决策 → 被否决的方案 → 后果**。

规则：

- ADR **不改名、不删除**。决策过时了就写一篇新的，把旧的标为 `Superseded by NNNN`。
- 编号连续递增，四位数，文件名 `NNNN-kebab-case-title.md`。
- 每篇顶部必须有 `Status` / `Date` 两个字段。
- 新增 ADR 后在下表登记。

| # | 标题 | 状态 | 日期 |
|---|---|---|---|
| [0001](0001-terraform-and-secrets.md) | Terraform 管理 GCP 基础设施与密钥 | **Superseded by 0006** | 2026-06 |
| [0002](0002-airflow-orchestration.md) | 用 Airflow 编排回填与增量摄取 | Accepted | 2026-06 |
| [0003](0003-incremental-bronze-pipeline.md) | Bronze 增量管道设计 | Accepted | 2026-06 |
| [0004](0004-silver-cleansing-methodology.md) | Silver 清洗规则的制定方法论 | Accepted | 2026-06 |
| [0005](0005-silver-execution-architecture.md) | Silver 执行架构：自建 Docker Spark 替代 Dataproc | Accepted（§4 存储结论被 0006 取代） | 2026-06-29 |
| [0006](0006-storage-compute-query-stack.md) | 全自建栈：MinIO + Spark + Trino，取消双阶段划分 | Accepted | 2026-07-30 |
| [0007](0007-clearing-completion-time-source.md) | "清雪完成时间"的数据口径：采用 plow shift 作业结束时间 | Accepted | 2026-08-02 |

> 编号是 2026-07-28 文档重构时统一分配的。原始文件名带 `week1/week2/week3`
> 前缀，按时间而非主题命名，已废弃。各篇的原始日期保留在 `Date` 字段里。
