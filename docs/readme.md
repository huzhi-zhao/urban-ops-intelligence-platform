# NYC-UOIP 文档索引

> **新接手 / 长时间没碰？先读 [09-ProjectManagement/handover-2026-07.md](09-ProjectManagement/handover-2026-07.md)。**

## 本仓库实际存在的文档

| 路径 | 内容 |
|---|---|
| `00-requirements/Project-Requirement-Overview.md` | 需求总览 |
| `00-requirements/business-objectives.md` | 业务目标（含 HTML 版） |
| `00-requirements/domain-knowledge/week2-Airflow.md` | Airflow 领域知识笔记 |
| `00-requirements/domain-knowledge/week3-explore_raw_data.md` | BigQuery 外部表探查 Bronze 的 SQL |
| `00-requirements/prompts/` | 各周使用的 prompt 记录 |
| `01-architecture/Project-Structure-Overview.md` | 系统架构 + 分层设计 + 原始 6 阶段路线图 |
| `01-architecture/decisions/week1-Terraform.md` | ADR：Terraform / GCP 基础设施 |
| `01-architecture/decisions/week2-Airflow.md` | ADR：Airflow 选型 |
| `01-architecture/decisions/week2-incremental_pipeline.md` | ADR：增量管道设计 |
| `01-architecture/decisions/week3-Build_Silver_Layer.md` | ADR：Silver 清洗规则方法论 |
| `01-architecture/decisions/week3-Silver-Execution-Architecture.md` | ADR：**Silver 执行架构（最重要的一篇）** |
| `01-architecture/steps/` | GCP demo / 自建集群的分步操作记录 |
| `03-datasources/source-registery.md` | 数据源注册表 |
| `03-datasources/backfill-comands.md` | 回填命令照抄清单 |
| `09-ProjectManagement/handover-2026-07.md` | **接手交接文档（当前状态）** |
| `09-ProjectManagement/project-management.md` | 原始 12 周排期计划（≠ 已完成状态） |
| `09-ProjectManagement/week1..week3/` | 每周复盘、踩坑记录、成果截图 |
| `images/` | 架构图 SVG / 排期 PDF |

仓库根目录另有 `CLAUDE.md`、`AGENTS.md`（AI 与人共用的强制约定）和
`.claude/rules/backfill.md`（回填层架构 + DAG 清单）。

---

## 参考：现代数据工程文档目录规范（目标形态，尚未完全落地）

下面是设计文档结构时参考的通用模板，**不是本仓库当前的真实结构**（真实结构见上表）。

![modern_data_eng_docs_structure.svg](./images/modern_project_structure.svg)


```text

docs/
│
├── 00-requirements/
│   ├── PRD-{id}-{feature}.md
│   ├── domain-knowledge.md          # 业务状态机、计算模型、枚举值定义
│   ├── metric-glossary.md           # 指标口径（GMV/DAU 等精确定义）
│   └── stakeholder-map.md           # 数据消费方清单（BI/ML/产品）
│
├── 01-architecture/
│   ├── system-architecture.md
│   ├── data-pipeline-flow.md
│   ├── environments.md              # Dev/Stg/Prod 差异、网络策略
│   ├── tech-stack.md                # 工具选型及版本锁定
│   └── decisions/                   # ★ ADR（Architecture Decision Records）
│       ├── ADR-001-why-iceberg.md   # 格式：背景 → 决策 → 否决方案 → 后果
│       └── ADR-002-why-dagster.md
│
├── 02-data-contracts/               # ★ 2025 新增：ODCS v3 (Bitol/Linux Foundation)
│   ├── README.md                    # 说明契约标准及工具链（datacontract-cli）
│   ├── provider-contracts/          # 本团队作为数据提供方对外承诺
│   │   └── orders.datacontract.yaml # ODCS v3 格式：schema + SLA + quality rules
│   ├── consumer-contracts/          # 本团队消费上游的期望（Great Expectations 可生成）
│   │   └── crm-users.datacontract.yaml
│   └── sla-matrix.md                # 数据产品 SLA 汇总（延迟/可用性/质量）
│
├── 03-data-sources/
│   ├── source-registry.md           # 数据源总览（负责人/网络/限速）
│   ├── api-contracts/
│   │   ├── crm-api.yaml             # OpenAPI/Swagger
│   │   └── payment-gateway.md
│   ├── webhooks/
│   │   └── user-behavior-webhook.md
│   ├── cdc-specs/                   # ★ 新增：Change Data Capture 规范
│   │   └── mysql-binlog-config.md   # Debezium/Flink CDC 配置说明
│   └── ingestion-specs.md
│
├── 04-data-models/
│   ├── schema-registry/
│   │   ├── ods_events.yaml
│   │   └── dwd_user_di.sql
│   ├── data-dictionary.md
│   ├── lineage.md
│   ├── data-quality-contracts.md    # ★ 已知脏数据模式、NULL 的业务含义、枚举边界
│   └── impact-matrix.md             # ★ 表 → 下游任务/报表/API 影响矩阵
│
├── 05-testing/                      # ★ 新增独立目录（原散落各处）
│   ├── test-strategy.md             # 单元/集成/契约/E2E 测试分层策略
│   ├── dbt-tests/
│   │   └── schema-test-conventions.md
│   ├── great-expectations/          # 或 soda/
│   │   └── expectation-suites.md
│   ├── sample-data/                 # 测试用脱敏数据集及生成脚本
│   └── ci-validation.md             # PR 门禁规则（数据质量卡点）
│
├── 06-operations/
│   ├── deployment-guide.md          # CI/CD + Airflow/Dagster 调度配置
│   ├── orchestration/
│   │   ├── dag-conventions.md       # DAG 命名、重试策略、SLA 配置
│   │   └── backfill-procedures.md
│   ├── troubleshooting/             # ★ 结构化 Playbook
│   │   ├── _template.md             # 症状 → 原因排序 → 诊断命令 → 恢复 → 预防
│   │   ├── pipeline-lag.md
│   │   └── schema-drift.md
│   ├── monitoring-alerts.md
│   └── incident-postmortems/        # ★ 事故复盘（防止 AI 重踩同类坑）
│       └── 2025-12-data-loss-rca.md
│
├── 07-governance/                   # ★ 新增独立目录（监管压力 + 数据产品化）
│   ├── data-catalog-integration.md  # Datahub/Amundsen/Alation 集成说明
│   ├── access-control.md            # RBAC 策略、列级权限
│   ├── pii-tagging.md               # 字段敏感级别标注（支持自动化扫描）
│   ├── compliance/
│   │   ├── gdpr-ccpa-mapping.md
│   │   └── data-retention-policy.md
│   └── data-products/               # ★ Data Mesh：数据产品定义
│       └── user-profile-product.md  # 遵循 ODPS 规范
│
└── 08-ai-context/                   # ★ AI 专属上下文（现代开发核心差异化目录）
    ├── CLAUDE.md → ../../CLAUDE.md  # symlink，保持单一来源
    ├── rules/                       # ★ 作用域规则（Cursor .mdc 格式）
    │   ├── sql.mdc                  # SQL 规范（禁止 collect()、分区策略等）
    │   ├── python.mdc               # Python pipeline 规范
    │   ├── dag.mdc                  # Airflow/Dagster DAG 编写规范
    │   └── dbt.mdc                  # dbt model 命名与测试规范
    ├── prompt-templates/
    │   ├── pipeline-scaffold.md     # 生成新 pipeline 的标准 prompt
    │   ├── sql-transform.md
    │   └── test-generation.md
    ├── business-logic-faq.md        # 复杂边界条件（防止 AI 幻觉的知识锚点）
    ├── anti-hallucination-guardrails.md  # ★ 禁止模型使用的已废弃函数/表/接口
    └── context-snapshots/           # ★ 阶段性上下文快照（长项目保持 AI 认知连续性）
        └── 2026-Q2-sprint-context.md
```
