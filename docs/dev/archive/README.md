# Archive —— 临时中转，待迁出后删除

> ⚠️ **本目录的内容对当前系统不成立。** 不要照着写代码，不要引用其中的结论。
>
> 🚚 **本目录是临时的。** 三篇文档将迁移到外部知识平台，迁完后**连同本目录一起
> 从仓库删除**。在此之前保留，只为不丢失迁移前的原文。

---

## 目录已关闭

**不再接收任何新文档。** 这一条是硬约束，不是建议——一个有准入条件的归档目录
等于给"不知道该放哪"留了后门，那正是原 `notes/` 的死因。

今后遇到失效文档，两条路，没有第三条：

| 情况 | 做法 |
|---|---|
| 有追溯价值（删掉会让某个历史决策变得不可理解） | 迁到外部知识平台，仓库内直接删 |
| 没有 | 直接删。git history 已经保留原文，`git log --follow` 可取回 |

判断"活文档是否还引用它"是删除前的唯一检查项：删之前先把指向它的链接改掉或去掉。

---

## 待迁出内容

| 文档 | 失效原因 |
|---|---|
| [airflow-concepts.md](airflow-concepts.md) | 个人学习笔记（Airflow 概念的 Java 类比），非项目知识；其中 Cloud Composer 部署一节随 GCP 放弃失效 |
| [bronze-data-exploration.md](bronze-data-exploration.md) | 用 BigQuery 外部表探查 Bronze 的 SQL，随 GCP 放弃失效。其中与引擎无关的**数据剖析检查清单**已提炼进 [design/README.md](../design/README.md)，迁出时不必再带 |
| [bigquery-external-table-pitfalls.md](bigquery-external-table-pitfalls.md) | 建 BigQuery 外部表踩的 6 个错，整篇依赖 BigQuery |

三篇均已在文件顶部标注失效说明。GCP 放弃的决策见
[ADR 0006](../adr/0006-storage-compute-query-stack.md)。

**迁出后要做的三件事**：删除本目录 → 去掉 [docs/README.md](../../README.md) 与
[docs/dev/README.md](../README.md) 中的 archive 行 → 修掉
`.claude/rules/backfill.md` 里指向 `bronze-data-exploration.md` 的那处引用。
