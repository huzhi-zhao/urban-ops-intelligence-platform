---
name: feedback
description: User wants AI to read project docs first, then build .claude memory
metadata:
  type: feedback
---

## 规则
- **每次会话开始时**：先阅读 README.md、CLAUDE.md、AGENTS.md；需要项目细节时按需读
  `docs/dev/`（设计意图、ADR），需要"怎么用/怎么排障"时读 `docs/guide/`
- **写代码前**：先理解项目架构，不要盲目开始
- **长期记忆**：将必要内容提炼到 `.claude/` 目录下，作为不同 AI 模型/会话的共享记忆

## 文档只有两类
写文档前先确认属于哪一类，规则详见 CLAUDE.md 的 Documentation conventions：

| | `docs/guide/` | `docs/dev/` |
|---|---|---|
| 受众 / 语言 | 外部使用者 / **English only** | 开发者 / 中文可 |
| 内容 | 怎么用、怎么排障 | 需求、设计意图、ADR、笔记 |

实现进度只在 CLAUDE.md 的 Implementation status 维护，文档里不重复。
个人的周报、排期、prompt 存档不进本仓库。

## 为什么
用户习惯先用文档整理思路，再开始实现。要求 AI 在动手前先理解项目全貌。

## 如何应用
- 新会话启动时：读取 `.claude/MEMORY.md`，按需加载 `project.md` 和 `directory-structure.md`
- 开始新任务时：先问自己"这个任务需要理解项目的哪些部分？"
- 任何关键项目事实（架构决策、数据源、业务逻辑）都值得沉淀到 `.claude/`