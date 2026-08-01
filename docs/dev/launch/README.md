# Launch Records

一篇 launch 记录**一次变更实际上线的过程与结果**：什么时候上的、
实际做法与 design doc 差在哪、验收判据跑出来是什么、上线后要盯什么。

尚无上线记录。第一篇预计是自建栈迁移
（[design/2026-07-self-hosted-migration.md](../design/2026-07-self-hosted-migration.md)）。

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
