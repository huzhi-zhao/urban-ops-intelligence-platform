# Launch Records

一篇 launch 记录**一次变更实际上线的过程与结果**：什么时候上的、
实际做法与 design doc 差在哪、验收判据跑出来是什么、上线后要盯什么。

已有七篇：

- [20260819-gold-dimensional-build-launch.md](20260819-gold-dimensional-build-launch.md) ——
  **L2 Gold 维表与事实表**（对应
  [design/20260817-gold-dimensional-build.md](../design/20260817-gold-dimensional-build.md)）。
  **待执行**：14 张 Gold 表从零行填满（9 维 + 5 事实）。与 L1 相反，本次
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

> **"上线后写"仍是默认，但四篇都提前开了篇**，理由各不相同：快照采集是不可逆
> 且大多在 git 之外，城市实例切换是横跨 5 个批次的长跨度变更，表结构那篇是
> 横跨 22 张表 + 一次 16 GB 回填、且**关键信息产生在动手之前**（冻结版的
> 偏差清单），建表那篇是执行清单本身要先写下来才能照着敲（同快照采集）。
> 共同点是**等做完再写会丢掉过程信息**。
> 短的、一次做完的变更仍按上线后写。

> ✅ 四篇命名一致，全部带 `YYYYMMDD-` 前缀（规则见下方「命名」一节，
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

## 2. 与设计的偏差
| 设计怎么写的 | 实际怎么做的 | 为什么改 |
说"没有偏差"也要写一行——那是对设计质量的正面证据。

## 3. 验收判据的实际结果
逐条对照 design doc 第 5 节，贴真实输出（命令 + 结果），不写"已验证"。

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
