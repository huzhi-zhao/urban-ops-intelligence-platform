# Launch Records

一篇 launch 记录**一次变更实际上线的过程与结果**：什么时候上的、
实际做法与 design doc 差在哪、验收判据跑出来是什么、上线后要盯什么。

已有十一篇：

- [20260827-bo-eda-and-presentation-sql-launch.md](20260827-bo-eda-and-presentation-sql-launch.md) ——
  **BO 循环 EDA 与呈现层 SQL**（对应
  [design/20260827-bo-eda-and-presentation-sql.md](../design/20260827-bo-eda-and-presentation-sql.md)）。
  **阶段 0+1 的 SQL 已就绪、一条未跑（2026-08-27 开篇）**。形态与前十篇不同：
  写方案的人拿不到线上环境，所以工作是**分阶段循环**的——一轮给出 SQL、
  人在线上跑、结果贴回、总结成结论与图，**一轮一停**；第 2 轮问什么取决于
  第 1 轮答什么，批量出题只会得到一批问错问题的 SQL。
  阶段 1 打 **BO-2 排班顺位**（P0−），10 条查询，判据全部先于结果写下。
  🔴 Q9（班次计划时长是否恒定）必须先于 Q1 跑：要上台的「26 小时」是
  顺位差 × 班次时长，时长不恒定则那一列是编的。
  结论与图表落在常青台账
  [requirements/bo-conclusions-and-figures.md](../requirements/bo-conclusions-and-figures.md)，
  不落在本篇。

- [20260822-cross-layer-reconciliation-and-certification-launch.md](20260822-cross-layer-reconciliation-and-certification-launch.md) ——
  **跨层对账与 Gold 认证（第三批，ADR 0012 收官）**（对应
  [design/20260822-cross-layer-reconciliation-and-certification.md](../design/20260822-cross-layer-reconciliation-and-certification.md)）。
  **一行代码未写（2026-08-22 开篇）**，设计已细化到逐文件的改动清单。
  §0 三条：现有执行器既不能分年切块也连不上对象存储，**先扩能力再写规则**；
  三个「显而易见的等式」里有两个是假的（F8 的 DML 带 `INNER JOIN dim_admin_label`，
  维表里没有的标签被静默丢掉）；`certified`/`suspect` 之外**必须有 `unknown`**，
  否则「审计没跑」和「审计全绿」长得一样。§5 是分阶段的上线发布计划。

- [20260822-out-of-pipeline-dq-audit-launch.md](20260822-out-of-pipeline-dq-audit-launch.md) ——
  **管道外 DQ 审计第二批**（对应
  [design/20260822-out-of-pipeline-dq-audit.md](../design/20260822-out-of-pipeline-dq-audit.md)）。
  **阶段 A–E 完成、生产已跑通（2026-08-22）**：`config/dq/rules.yaml` 33 条规则展开成
  **81 条检查**、`uoip_meta.dq_audit_log`（追加型）、`dag_dq_audit`（`30 8 * * *`）。
  V1–V5 全绿，含 **V3 故障注入**：造一条真违规，规则 ❌ 而**任务 success**、
  Discord 实收——ADR 0012「finding 不 fail 任务」在 DAG 路径上有了证据。
  §4 记了两个「规则跑得动但问错了问题」的缺陷（F1 漏 `is_scheduling_era` 数成 1,436、
  百分比规则不带分母），以及故障注入自身的两个时序坑。

- [20260820-scoring-chain-and-m1-launch.md](20260820-scoring-chain-and-m1-launch.md) ——
  **L3 评分链与 M1**（对应
  [design/20260820-scoring-chain-and-m1.md](../design/20260820-scoring-chain-and-m1.md)）。
  **进行中（2026-08-22）**：L3-0 与 L3-a 已完成（M1 + F5 跑通生产，MAE 7.345 vs 基线 23.628，版本保全实测闭合）；L3-b 代码就绪未跑；余 L3-b 跑生产 + DQ 基线 + S7 冻结。
  与 L1/L2 一样**没有一步不可逆**——那为什么仍然提前开篇？因为本次的关键信息
  只在跑的那一刻存在：🔴 **M1 的 MAE 与 seasonal-naive 基线那一对数**
  （BO-8 已写明未达标不构成失约，但结果必须如实落在某处，那个地方就是这里）、
  探针复跑的漂移数（L2 §4.9 的机制对 L3 直接有效）、以及 a1–a7 / b1–b13 两张门禁表。
  §0 点出四个最容易翻车的地方，其中第一条是 **F5 不能按 R4 原样整表重建**
  ——契约要求旧 `model_version` 永不覆盖，而 R4 的第二步是 purge。

- [20260819-gold-dimensional-build-launch.md](20260819-gold-dimensional-build-launch.md) ——
  **L2 Gold 维表与事实表**（对应
  [design/20260819-gold-dimensional-build.md](../design/20260819-gold-dimensional-build.md)）。
  **待执行**：13 张 Gold 表从零行填满（9 维 + 5 事实）。与 L1 相反，本次
  **没有一步是不可逆的**——Gold 全是 `CREATE OR REPLACE TABLE ... AS SELECT`，
  秒级可重建。§0 点出三个最容易翻车的地方（Trino 无 `INSERT OVERWRITE` 语法、
  `dim_snowfall_event` 的 159 vs 99 口径、`CREATE OR REPLACE` 在外部表上的
  孤儿文件未实测），§2 阶段 A 就是把最后那条在 smoke prefix 上先试掉，
  **先于任何 DML**。§3 是留空待填的 13 行门禁表。

- [20260817-silver-etl-runnable-launch.md](20260817-silver-etl-runnable-launch.md) ——
  **L1 Silver 全链路跑通**（对应
  [design/20260817-silver-etl-runnable.md](../design/20260817-silver-etl-runnable.md)）。
  **阶段 A（代码 + 单测）已完成，B 起待执行**：`silver_service_request`
  从零到全量。§0 是一页纸的阶段/耗时/可否回滚表，§1 是八条前置检查，
  §2 是分 A–F 六阶段的执行清单（可直接粘贴的命令；B 阶段照抄 E0/E1 踩过的
  四个环境坑，不重新发现），§3 是留空待填的门禁表（单季 G1–G12、全量 H1–H6）。
  §4 已先记下四条与设计的偏差。提前开篇的理由是本次含一次
  **4,876 天 / 16 GB 的全量回填**——跑完就是既成事实，只能重跑，
  门禁数字必须在跑的当时逐条填。
  这是 ETL 需求拆成三次上线中的第一次（L2/L3 见 design 索引）。
- [20260817-etl-implementation-launch.md](20260817-etl-implementation-launch.md) ——
  E0/E1 Silver ETL 实测（对应
  [design/20260817-etl-implementation.md](../design/20260817-etl-implementation.md)）。
  **执行中**：E0/E1 的四个 job（`plow_shift` / `parking_ban` /
  `snow_clearing_address` / `plow_zone_boundary`）已对真实 Bronze 数据跑通，
  行数与跨表门禁全部核对。空间命中率 99.996%——`zone_assignment` 在真实 82 个
  多边形上第一次验证。§2 记了四个环境坑（分支合并方向、容器实际命名、
  s3a jar 缺失、`--conf` 里的环境变量在宿主 shell 而非容器 shell 展开导致
  误判为密钥轮换）。§4 是遗留：旧前缀 `silver/snowfall_events/`（复数）待清、
  分支未 push。
- [20260814-table-creation-deployment-launch.md](20260814-table-creation-deployment-launch.md) ——
  建表上线（25 张 Silver/Gold 表建进 Trino）。**执行中**：§3 是分四批的执行清单，
  §5 是风险表，§6 是六条验收判据。与下面那篇的分工是
  **「表结构长什么样」vs「怎么把它建起来」**——前者是评审报告，本篇是运维步骤。
  本次上线**明确不含 ETL**，目标只是把 contract → DDL → Trino → Metastore → MinIO
  这条链跑通再清干净。§4 单独讲了 `--location-prefix`：它不是便利选项而是安全前提
  （external table 的 DROP 不删文件，而 `silver/` 下已有真实数据）。
  同篇定性了 Trino/Hive/Superset 属平台级共享服务（[ADR 0006 §9](../adr/0006-storage-compute-query-stack.md)）。
- [20260813-gold-silver-schema-derivation-launch.md](20260813-gold-silver-schema-derivation-launch.md) ——
  Gold / Silver 表结构（对应
  [design/20260809-gold-silver-schema-derivation.md](../design/20260809-gold-silver-schema-derivation.md)
  与 [design/20260812-gold-bus-matrix.md](../design/20260812-gold-bus-matrix.md)）。
  **进行中**：S0–S4 已完成（contract 于 2026-08-13 冻结，提前于 8/23 时间盒；
  S4 的 25 份 DDL + StructType + 三方一致性单测于 2026-08-14 落地）。
  §2 是 **S3→S4 门禁的 Schema Review**——31 项发现，6 项阻塞，
  它同时充当冻结版的偏差清单：问题不是 S4 实现时产生的，是冻结那一刻就在里面的。
  §5 给了按「改动成本随时间跳变」排的处理顺序。
- [20260803-city-instance-switchover-launch.md](20260803-city-instance-switchover-launch.md) —— 城市实例切换
  （对应 [design/20260802-city-instance-switchover.md](../design/20260802-city-instance-switchover.md)）。
  **进行中**：批 0–3 已完成，批 3.5（上线前代码审查）已完成，批 4–5 未开工。
  §10 是 Bronze 上线的分阶段执行计划。
  这一篇同样在完成前就开写——横跨 5 个批次、多次提交，等全部做完再写会丢掉过程信息。
- [20260802-snapshot-collection-deployment-launch.md](20260802-snapshot-collection-deployment-launch.md)
  —— 快照采集上线（对应
  [design/20260801-snapshot-collection-deployment.md](../design/20260801-snapshot-collection-deployment.md)）。
  **已完成 2026-08-02。** 动作大多在 git 之外（凭证、systemd、外部监控），
  一步做错就是一天不可再生的历史，因此执行清单先行、执行时逐条填结果。

> **"上线后写"仍是默认，但五篇都提前开了篇**，理由各不相同：快照采集是不可逆
> 且大多在 git 之外，城市实例切换是横跨 5 个批次的长跨度变更，表结构那篇是
> 横跨 22 张表 + 一次 16 GB 回填、且**关键信息产生在动手之前**（冻结版的
> 偏差清单），建表那篇是执行清单本身要先写下来才能照着敲（同快照采集）。
> L3 那篇的理由与前四篇都不同：它不是变更不可逆，而是**结论只在跑的当时可测**——
> 建模的失败模式是「数字对但结论站不住」，事后补不出一对诚实的模型/基线数字。
> 共同点仍是**等做完再写会丢掉过程信息**。
> 短的、一次做完的变更仍按上线后写。

> ✅ 九篇命名一致，全部带 `YYYYMMDD-` 前缀（规则见下方「命名」一节，
> 2026-08-13 修订）。目录按时间正序排。

---

## 为什么它不能并进 design doc

design doc 是**上线前**写的，launch 是**上线后**写的，两者的差异本身就是信息量最大的部分——
"计划分四批、实际合成两批"这句话，比任何一份计划都更能指导下一次迁移。
把上线结果回写进 design doc 会抹掉这个差异，还会让一篇冻结的文档变成活的。

也不并进 PR 描述：一次上线通常横跨多个 PR，且包含 git 之外的动作
（起服务、跑回填、改 cron、撤销密钥）。

---

## 命名

```
launch/<date>-<topic>-launch.md
```

`<date>` 是 `YYYYMMDD`，取**开篇日**（提前开篇的按开篇日，不是完成日）。
带前缀是为了让目录按时间正序排——launch 是事件类文档，
「最近上了什么线」比「叫什么名字」更常是检索的入口。

`<topic>` 与对应 design doc 的 `<topic>` **逐字一致**。
没有对应 design doc 时（例如 `20260814-table-creation-deployment`，它执行的是
另一篇 design doc 里的一个阶段 S4，而那个 topic 已被评审报告占用）自取一个，
判据是**这一篇记的是哪一次上线**，不是它属于哪个设计。
一次变更只上线一次；回滚后重新上线，追加到同一篇，**不改文件名**（与 ADR 同理）。

> ⚠️ **本规则于 2026-08-13 修订**，此前写的是「不带日期前缀，日期写在正文里」。
> 改的原因很实际：三篇之后目录已经排不出顺序了。
> 存量两篇本来就带前缀（README 的旧规则和实际文件名一直是脱节的），
> 因此这次修订**没有产生任何改名**——只有文档里那批指向无前缀路径的
> 失效引用需要修，已一并修掉。

---

## 模板

```markdown
# <变更名> 上线记录

> **Date**: YYYY-MM-DD · **Design**: [../design/YYYY-MM-<topic>.md](...)
> **Result**: Success | Partial | Rolled back

## 1. 时间线
关键动作与时刻。含回滚点：哪一步之前还能退，之后不能。
**失败的那几次也是时间线的一部分**，但只留结论：触发原因、暴露它的那个观测量、
推翻先前归因的证据，各一行。试了几轮、终端刷了什么，不留。
只写"最后成功了"等于没写。

## 2. 与设计的偏差
| 设计怎么写的 | 实际怎么做的 | 为什么改 |
说"没有偏差"也要写一行——那是对设计质量的正面证据。

## 3. 验收判据的实际结果
逐条对照 design doc 第 5 节，写**可重跑的命令 + 真实数字**，不写"已验证"。
不贴终端输出：全绿的批量检查给一个总数，异常与关键项才逐条写。
每个数字带**三件套**：测量时间、测量入口（那条命令）、测量环境
（引擎版本 / 是管道还是探针 / 打在哪套数据上）。缺一件就无法判断它何时失效——
见 [../README.md](../README.md#三什么必须留下)。

## 4. 遗留项
上线时没做完、明确推迟的事 + 各自的去处（Ticket / 下一篇 design / ADR）。

## 5. 上线后需要观察的
盯什么指标、盯多久、超过什么阈值就回滚或升级人类。
```

---

## 与 postmortem 的边界

上线**过程中**发现并当场解决的问题写在这里（第 2 节）。
上线**之后**才暴露、造成实际影响的故障，另写
[postmortem/](../postmortem/README.md)，不追加到 launch 里。

launch 写完即冻结。后来的实测推翻了本篇某个数字或归因时，在原处**追加**一个
带日期的更正块（`> 🔴 YYYY-MM-DD 更正：…，依据 …`），原文一字不动。
