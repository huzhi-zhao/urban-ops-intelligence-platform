# ADR 0004 — Silver 清洗规则的制定方法论

> **Status**: Accepted · **Date**: 2026-06 · **Revised**: 2026-08-20
>
> 决策没变：写 transform 代码之前先做数据探查 + 冻结 schema 契约，而不是先写
> 再迭代修。原文按 NYC 四源（311/NYPD/Open-Meteo/DCP）+ GCP 执行者（BigQuery
> External Table 自动探测 GCS 上的 Bronze 文件）描述落地方式，二者均已作废——
> NYC 实例已退役（CLAUDE.md「城市无关护栏」§3），GCP 组件已于 2026-07-30 全部
> 放弃（[ADR 0006](0006-storage-compute-query-stack.md)）。这次重写换的是落地
> 方式和「已完成/未完成」的现状，方法论本身不变，故不另开 ADR。

---

## 决策

写 Silver / Gold 转换代码之前，先做探查 + 定契约，不是先写代码再迭代修：

1. **Explore Bronze 真实数据**：抽样读原始 NDJSON，核对字段名、类型、空值率、
   时间字段格式是否和 `ingestion/config/source_config.py` 的校验一致——
   Socrata 字段历史上出现过漂移（分页事故就是一例，见下）。
2. **`contracts/api-contracts/` 先于 Spark 代码存在**：字段名、类型、填充率、
   低基数取值域必须对真实 API 实测，不能照抄设计文档假设。
3. **`spark/schemas/` 的 StructType 基于探查结果**，不是凭记忆。
4. **`sql/ddl/` 提前定好**，尤其空间字段（`dim_geography` 存 WKT）与联合主键
   （311 的 `(case_id, interaction_id)`，见下）。
5. **明确分区/去重/幂等策略**：Silver 按 date 分区，哪个字段做唯一键去重。

权衡取舍：花时间先探查 vs 直接写转换代码再迭代修。上游 Socrata 字段有漂移史，
先探查能避免返工——这条判断被 2026-08 的分页事故反向印证：**没有**先建立
「Bronze 行数 vs 上游 `count(*)` 对账」这条探查步骤，55 天的重复/丢行在
existence 检查下完全不可见（复盘见
`docs/dev/postmortem/bronze-socrata-pagination-incident.md`）。

---

## 现状：探查已完成的部分

原文列的五步，当前实际完成度：

| 步骤 | 状态 |
|---|---|
| Bronze 数据质量审计 | ✅ `scripts/analysis/`（探针，一探针一模块）+ `dag_audit_bronze` 的 B/C 两个内容校验 |
| `contracts/` 冻结 | ✅ 四份 Winnipeg 契约字段实测；两处已知残留见 CLAUDE.md「各层进度」 |
| `spark/schemas/` StructType | ✅ S4（2026-08-14）落地，25 张表三方一致性校验 |
| `sql/ddl/` | ✅ S4 落地，8 Silver + 17 Gold |
| 分区/去重/幂等策略 | ✅ 见下 |

探查阶段（[指标可用性探针](../design/20260808-metric-feasibility-probe.md)）
额外做了原文没有的一件事：**在写任何 Silver job 之前，先验证目标指标算不算得
出来**——BO-2/BO-3/BO-6 三个核心指标的可行性各自有一篇产出物记录判据与实测
数字。这不是本 ADR 决定的步骤，但属于同一条「先探查后写代码」的方法论，值得
在这里记一笔：`config/sources/winnipeg_snow_clearing.yaml` 的 82 个多边形、
25 个 `plow_zone` 取值、311 的 `(case_id, interaction_id)` 联合主键（`case_id`
本身 1.87% 重复），全部是探查阶段发现、写 Silver 代码前就已定案的事实，不是
写完代码后补的修正。

### 分区 / 去重 / 幂等（原文第 5 点的答案）

- **Silver 分区**：按 `open_date_local`（本地日，不是 UTC 日），
  `partitionOverwriteMode=dynamic`，一个日分区一个文件
  （`repartition(N, "open_date_local")`）。
- **去重键**：311 是 `(case_id, interaction_id)`，不是 `case_id`——行粒度是
  interaction 不是 case（详见 ADR 0009 附近的 Winnipeg 数据事实记录）。
- **幂等**：见 CLAUDE.md「Data architecture rules」C6——Silver 用
  `INSERT OVERWRITE PARTITION`，Gold 用整表重建四步（R4，
  `.claude/rules/gold-sql.md`）。

---

## 原文两处已作废的内容

1. **BigQuery External Table 自动探测**（`gs://nyc-uoip-prod/bronze/raw/...`）：
   GCS 与 BigQuery 均已放弃，当前查询层是 Trino + Hive Metastore
   （ADR 0006 §9），schema 靠显式 StructType + DDL，不依赖任何自动探测。
2. **SLA 基线表**：原文写的 `311: ~8,000 条/天`、`NYPD: ~300 条/天` 是 NYC 数字，
   随实例退役一起作废。Winnipeg 侧的对应基线（311 工单量、事件粒度的非零率等）
   落在 `docs/dev/requirements/metric-feasibility-audit.md` 和 E1 的 DQ 基线
   （L2 launch 文档 §3.x，14 张表逐列空值率），不在本 ADR 重复。

---

## 参考

- Silver 执行架构：[ADR 0005](0005-execution-architecture.md)
- 存储/计算/查询栈：[ADR 0006](0006-storage-compute-query-stack.md)
- Gold 幂等规则：`.claude/rules/gold-sql.md` R4
- 指标可行性探查：`docs/dev/design/20260808-metric-feasibility-probe.md`
- 分页事故复盘：`docs/dev/postmortem/bronze-socrata-pagination-incident.md`
