# Launch Records

一篇 launch 记录**一次变更实际上线的过程与结果**：什么时候上的、
实际做法与 design doc 差在哪、验收判据跑出来是什么、上线后要盯什么。

已有两篇：

- [city-instance-switchover-launch.md](city-instance-switchover-launch.md) —— 城市实例切换
  （对应 [design/20260802-city-instance-switchover.md](../design/20260802-city-instance-switchover.md)）。
  **进行中**：批 0/1 已完成，批 2 代码完成待收尾，批 3–5 未开工。
  这一篇同样在完成前就开写——横跨 5 个批次、多次提交，等全部做完再写会丢掉过程信息。
- [20260802-snapshot-collection-deployment-launch.md](20260802-snapshot-collection-deployment-launch.md)
  —— 快照采集上线（对应
  [design/2026-08-snapshot-collection-deployment.md](../design/2026-08-snapshot-collection-deployment.md)）。
  **已完成 2026-08-02。** 动作大多在 git 之外（凭证、systemd、外部监控），
  一步做错就是一天不可再生的历史，因此执行清单先行、执行时逐条填结果。

> **"上线后写"仍是默认，但两篇都提前开了篇**，理由不同：快照采集是不可逆
> 且大多在 git 之外，城市实例切换是横跨 5 个批次的长跨度变更。共同点是
> **等做完再写会丢掉过程信息**。短的、一次做完的变更仍按上线后写。

> ⚠️ 命名不一致：快照那篇带 `20260802-` 前缀，与下面「命名」一节的规则相悖。
> 保留原名不改——launch 记录一旦写就不改名，与 ADR 同理。新写的按规则来。

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
launch/<topic>-launch.md
```

`<topic>` 与对应 design doc 的 `<topic>` **逐字一致**，不带日期前缀——
一次变更只上线一次，日期写在正文里。回滚后重新上线，追加到同一篇。

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
