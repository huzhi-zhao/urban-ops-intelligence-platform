# 开发文档

面向开发者的需求、设计与决策记录。中文书写，文件名用 English kebab-case。
对外操作手册在 [../guide/](../guide/)，两者不混放。

## 当前状态

- [handover-2026-07.md](handover-2026-07.md) —— **先读这篇**。写于 2026-07-24，
  记录真实进度、四个容易记错的架构事实、已知技术债。

## 需求

- [requirements/project-overview.md](requirements/project-overview.md) —— 项目定位、城市无关性、MVP 范围
- [requirements/business-objectives.md](requirements/business-objectives.md) —— BO-1 ~ BO-5

## 架构

- [architecture/platform-architecture.md](architecture/platform-architecture.md) —— 分层设计意图
- [architecture/roadmap.md](architecture/roadmap.md) —— 部署阶段与功能阶段
- [adr/](adr/README.md) —— 架构决策记录（0001 ~ 0005）

## 笔记

一次性的领域知识与踩坑记录。不做长期维护承诺，但不删。

- [notes/airflow-concepts.md](notes/airflow-concepts.md) —— Airflow 概念速通（Java 视角）
- [notes/bronze-data-exploration.md](notes/bronze-data-exploration.md) —— 用外部表探查 Bronze 的 SQL
- [notes/bigquery-external-table-pitfalls.md](notes/bigquery-external-table-pitfalls.md) —— 建外部表连踩的 6 个错
- [notes/winnipeg-data-sources.md](notes/winnipeg-data-sources.md) —— 第二城市（Winnipeg）数据源调研
