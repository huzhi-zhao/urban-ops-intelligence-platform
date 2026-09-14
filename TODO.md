# TODO — 仓库内待办

> **这份名单只收仓库内的事**：代码、管道、SQL、契约、CI、PR。
> 会议演讲、对外摘要、合作关系、写作计划这些**不在这里**——
> 它们在 ToucanShelf `UOIP/TODO`（见 `CLAUDE.md`「文档边界」一节）。
> 两份名单故意不交叉：交叉了就两边都不可信。
>
> 状态的权威仍是 `CLAUDE.md`「Implementation status」。本文件只是把散在那
> 几千字里的**未完成项**提出来排个序，不复述已完成的部分。
>
> 最后整理：2026-09-14

---

## 🔴 未开的 PR

管道已经闭合、17 张 Gold 表全部有生产数据，但**代码还在分支上**。
每多待一天，rebase 成本和「本地跑通 ≠ 别人能跑通」的风险就多一分。

- [ ] `feat/l3-scoring-chain` —— L3 全部（a/b/c），已推齐，PR 未开
- [ ] 管道外 DQ 审计（阶段 A–E 已完成、生产跑通）—— 余提 PR
- [ ] 跨层对账与 Gold 认证（阶段 A–F 已完成、八条判据全过）—— 余提 PR
- [ ] L2 阶段 E5 提 PR
- [ ] **E6 另开 PR**：O8 的两个 Bronze 探针进 `dag_audit_bronze`

---

## 🟠 已知会咬人的缺陷

都不是「跑不起来」，而是**跑得起来但问错了问题 / 迟早误导人**。

- [ ] 五张事实表 DDL 头注的 `-- relationships:` 仍写 `= 916`，实测是 **908**。
      那行是不执行的 prose，但**与冻结的契约同源，要改走变更流程**。
      在那之前以 L2 launch §4.9 为准
- [ ] `contracts/api-contracts/open-meteo.yaml` 仍写着批 2 已废弃的 dataset 名
      `nyc_weather_forecast` —— 该源已拆成 archive + forecast 两份，契约需跟着拆
- [ ] `contracts/source-registry.md` **不存在**，而 `AGENTS.md` 在引用它。
      要么建，要么把引用删掉
- [ ] `ingestion/schemas/`（Pydantic raw-API models）**从未创建**。
      原始形状校验目前只在 `ingestion/config/source_config.py` 里
- [ ] 死人开关未注册：`AIRFLOW_WATCHDOG_URL` 为空时 `ping_watchdog` 静默跳过。
      这是设计不是 bug，但**没注册就等于没有这条防线**

---

## 🟡 运维与噪音

- [x] ~~`tests/integration/`（12 项）从未在本地跑过 `make test-integration`~~
      —— **作为判据取消**（[ADR 0013](docs/dev/adr/0013-h2-scope-from-handover-to-a-single-honest-answer.md)）。
      「集成测试在真实 MinIO 上绿」原是 H2「能跑起来」的验收项，该判据已作废。
      套件留在仓库里可跑，但没人再欠它一次绿
- [ ] 日志噪音：`scripts/` 挂在 `plugins/` 下被 Airflow 逐个 import，
      每次任务刷 15 行无关 ERROR（批 3 遗留，回填跑完后处理）
- [x] ~~**Grafana 是唯一尚未部署的栈组件**~~ —— **取消**
      （[ADR 0013](docs/dev/adr/0013-h2-scope-from-handover-to-a-single-honest-answer.md)）。
      它属于被取消的运维面：没有接手者的服务不需要监控面板

---

## 🟢 范围内但未开工

这些**不是做不出来**，是主动排在 H1（2026-09-19）之后的。
不要把留白读成技术障碍。

- [ ] 城市实例切换**批 4–5**：边界能力泛化 → 语义配置化 + Silver
- [ ] 指标可用性探针**任务 6、7** —— 2026-08-09 决定延后（BO-5 是 P1、
      BO-8 依赖 M1 才能真测）。两者都没跑出任何反对证据
- [x] ~~Iceberg 迁移（ADR 0006 §5）~~ —— **取消**
      （[ADR 0013](docs/dev/adr/0013-h2-scope-from-handover-to-a-single-honest-answer.md)）。
      收益真实，但服务的是一个要长期被别人运维的系统；判据变了，它就没有消费者

---

## 🔵 H2 · 分区顺位页（ADR 0013）

H2 的判据已于 2026-09-14 改写为「读者选一个 plow zone，拿到两个不超出证据的
答案」。代码已进仓，**差的只是真实数字**。

- [ ] 在计算节点上取数并出页：
      `make eda-export ONLY=FIG-BO2-06,FIG-BO2-07` → `make zone-lookup`。
      两份 SQL 至今**未对生产 Gold 跑过**，页面只在合成 payload 上验证过
- [ ] 取到数之后回填台账 `docs/dev/requirements/bo-conclusions-and-figures.md`
      §2.2 里 FIG-BO2-06 / 07 两行的状态（现为「未取数」）
- [ ] 🔴 **页面上的保留条件与答案同等重要**，改页面时不得删减：
      粒度是分区不是街道 · 只覆盖住宅街 · 3 个分区无排班（占 6.02% 地址）·
      它回答不了「现在清到哪了」（那是市政官方工具的活，BO §0.1）

---

## 🔴 一条会连带改口径的改动

- [ ] **BO-3 必须在单日阈值之外再加一条滚动累积判据。**
      4 次无降雪犁雪的真因是**阈下累积**（21 日累计保留对照组 76%，
      单日峰值只剩 26%，差 2.92 倍），换阈值救不了。
      🔴 该改动会连带改变 **N、ward × 事件面板、BO-8 回测次数**——
      不是一处 SQL，动之前先把受影响的门禁数清楚
