# Launch Records

一篇 launch 记录**一次变更实际上线的过程与结果**：什么时候上的、
实际做法与 design doc 差在哪、验收判据跑出来是什么、上线后要盯什么。

已有三篇：

- [20260813-gold-silver-schema-derivation-launch.md](20260813-gold-silver-schema-derivation-launch.md) ——
  Gold / Silver 表结构（对应
  [design/20260809-gold-silver-schema-derivation.md](../design/20260809-gold-silver-schema-derivation.md)
  与 [design/20260812-gold-bus-matrix.md](../design/20260812-gold-bus-matrix.md)）。
  **进行中**：S0–S3 已完成（contract 于 2026-08-13 冻结，提前于 8/23 时间盒），
  S4 未开工。§2 是 **S3→S4 门禁的 Schema Review**——31 项发现，6 项阻塞，
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

> **"上线后写"仍是默认，但三篇都提前开了篇**，理由各不相同：快照采集是不可逆
> 且大多在 git 之外，城市实例切换是横跨 5 个批次的长跨度变更，表结构那篇是
> 横跨 22 张表 + 一次 16 GB 回填、且**关键信息产生在动手之前**（冻结版的
> 偏差清单）。共同点是**等做完再写会丢掉过程信息**。
> 短的、一次做完的变更仍按上线后写。

> ✅ 三篇命名一致，全部带 `YYYYMMDD-` 前缀（规则见下方「命名」一节，
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
