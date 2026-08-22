# 管道外数据质量审计（第二批）上线记录

> **Date**: 2026-08-22（开篇日） ·
> **Design**: [../design/20260822-out-of-pipeline-dq-audit.md](../design/20260822-out-of-pipeline-dq-audit.md) ·
> **ADR**: [0012](../adr/0012-data-quality-audit.md)
> **Result**: 第二批阶段 A–E 完成、生产已跑通，余提 PR

**本篇覆盖的是 ADR 0012 §6 的第二批**：`dq_audit_log` + 统计性/结构性检查 +
独立 DAG。第一批（Bronze 校验 B/C 进 `dag_audit_bronze`）已于 `a5304cb` 完成，
不在本篇的执行清单里，只作为前置事实。第三批（跨层对账 + 计分卡 +
`certified`/`suspect` 打标实现）另开一篇。

**为什么提前开篇**：本次没有任何一步不可逆——审计只读，产出是日志表，
删了重建即可。提前开篇的理由和 L2/L3 那两篇不同，是 §0 那四条：
**其中三条会在写第一行代码之前就把人绊倒**，写在这里比事后记进偏差表有用。

---

## 0. 四个最容易翻车的地方

### 0.1 🔴 `sql/ddl/` 是被 glob 的，多放一个文件就会炸两处

`tests/unit/test_contract_ddl_schema_consistency.py:137` 是
`DDL_DIR.glob("*.sql")`，`scripts/ddl/apply_ddl.py` 也遍历同一个目录。
把 `dq_audit_log.sql` 直接放进 `sql/ddl/` 会同时撞上两堵墙：

1. 三方一致性单测会给它找 contract，**找不到就红**——而审计日志表按定义
   没有 contract（它不是数据产品，是审计自己的账本）。
2. `ddl_parser._LAYER_RE` 只认 `silver|gold` 两个词，`meta/` 路径会让
   `Ddl.layer` **抛异常**，而不是返回一个新层。

处置（细化时已定，执行时照做）：**审计表的 DDL 放 `sql/meta/`，不放 `sql/ddl/`。**
glob 不递归，两处都自然看不见它。`apply_ddl` 要么加一个 `--dir` 参数，要么
审计表自带一个更小的建表入口——**倾向后者**，别为一张表去改建表器的形状。

### 0.2 🔴 `uoip_meta` 的隔离要真的验，不能靠"应该看不见"

design §6 的 **V5** 就是为这条写的。`build_gold.TABLES` / `dq_baseline.py` /
`dq_assertions.py` 三处都在遍历 Gold 的每一张表，隔离生效的判据是
**这三处跑完看到的仍然是 17 张表**，不是「我们放进了别的 schema」。

### 0.3 🔴 新 DAG 默认 paused，而 `dags trigger` 照样返回成功

L2 §4.12 的老坑，原样适用：run 落到 `queued` 后永远不动，scheduler 日志一个字
都没有。判据只能用

```
airflow dags details dag_dq_audit -o yaml | grep is_paused
```

**`airflow dags unpause` 打印的是改之前的状态**，不能拿它当证据。
另外改了 compose 的卷要 `make stack-recreate-airflow`，`restart` 不重挂卷。

### 0.4 🟡 "任务不红"这件事本身要被验证，而不是被声称

ADR 0012 §1 规定 2 的全部价值在于：发现不让任务变红，只有「检查跑不起来」
才 raise。这两条的边界很容易在实现时糊掉（一个没接住的异常就把它变成阻断式）。
**V3 是唯一能证明它的判据**：造一条真违规，看任务是绿的、Discord 是响的。

---

## 1. 前置检查

- [ ] `.env` 里 Trino 是**容器视角**（`TRINO_HOST=trino` / `8080`）。
      宿主机跑命令临时加前缀 `TRINO_HOST=localhost TRINO_PORT=8090`，
      **不要改 `.env`**（O17 的成因就是这个）。
- [ ] 17 张 Gold 表仍有数据：`make gold-dq` 跑通、零行表 0、全空列 0。
- [ ] `make gold-assert` 仍是 **185 条 / 0 violations**（本轮要复用它）。
- [ ] `dag_audit_bronze` 的 `audit_integrity` 任务近一次是绿的（第一批的产出）。
- [ ] 分支 `feat/out-of-pipeline-dq-audit`，从 main 起。

---

## 2. 执行清单

| 阶段 | 内容 | 可否回滚 |
|---|---|---|
| **A** | `config/dq/rules.yaml` + 规则解析 + 单测 | 是（纯新增） |
| **B** | `sql/meta/dq_audit_log.sql` + `uoip_meta` 建 schema 建表 | 是（DROP + 清 prefix） |
| **C** | `scripts/dq/run_audit.py` + `make dq-audit`，宿主机手动跑一趟 | 是（只读 + 追加日志） |
| **D** | `dags/dag_dq_audit.py` + 部署 + unpause + 容器内跑一趟 | 是 |
| **E** | V3 故障注入（smoke prefix 造违规）+ 收 Discord | 是 |
| **F** | 收口：填 §3 门禁表、更新 CLAUDE.md 状态、提 PR | — |

**代码部分（A · B · C · D 的可离线写完的一切）已于 2026-08-22 完成**，
一行生产数据都还没跑——「代码写完」不等于「跑通了」。已落地的文件：

| 文件 | 是什么 |
|---|---|
| `config/dq/rules.yaml` + `config/dq/README.md` | **33 条规则**，每条都有实测锚点（`note` 必填） |
| `scripts/dq/rules.py` | 规则解析 + comparator 语义 + 三条加载期禁令 |
| `sql/meta/dq_audit_log.sql` | 追加型日志表，**不在 `sql/ddl/`**（§0.1） |
| `scripts/dq/audit_store.py` | 建 `uoip_meta` + 追加 + 取上一次观测值 |
| `scripts/dq/run_audit.py` + `make dq-audit` | 执行器；`--cadence` / `--full-sweep` / `--dry-run` |
| `dags/dag_dq_audit.py` | `30 8 * * *`，`catchup=False`，finding 不 fail |
| 4 份单测（73 项） | `test_dq_rules` 31 · `test_dq_audit_store` 17 · `test_dq_run_audit` 26；DAG 侧 `test_dag_dq_audit` 已进 `make test-dags` 与 CI 的 `dags` job |

门禁：`make lint` 干净 · `make test-unit-offline` **1,005 passed / 7 skipped** ·
`make test-dags` **27 passed**。
🟢 `make test-dags` 当场抓到一条只有装了 airflow 才会暴露的错：
`dag.schedule_interval` 在 Airflow 3 已删，判据得用 `dag.schedule`——
正是 O15 建这个 job 的理由，第一次用就兑现了一次。

阶段 A/B 的顺序不能反：规则清单定下 `dq_audit_log` 要存哪些字段，
先建表就会漏列，而这张表是追加型、改列比 Gold 麻烦。

---

## 3. 验收判据的实际结果

| # | 判据 | 期望 | 实测 |
|---|---|---|---|
| **V1** | `make dq-audit` 跑通，全部规则落进 `dq_audit_log` | 误报 0 | ✅ **81 条检查 / 0 error / 0 warn / 0 无法执行**，`appended 81 row(s)`。33 条规则展开成 81 条检查（`table: "*"` 的三条按 17 张表展开） |
| **V2** | 连跑两趟结论逐条相同 | 除 `run_id`/`checked_at`/耗时外全同 | ✅ 连跑两趟逐条相同，第二趟「上次」列全部填上，`pct_change` / `pct_point_change` 首次真正生效 |
| **V3** | 造一条违规 → 规则 FAIL、**任务不红**、Discord 实收 | 三样都要 | ✅ **三样全部成立**，见 §3.2 |
| **V4** | `make lint` + `make test-unit-offline` + `make test-dags` | 全绿 | ✅ `make lint` 干净 · `make test-unit-offline` **1,013 passed / 7 skipped** · `make test-dags` **27 passed** |
| **V5** | `uoip_meta` 隔离生效 | 三处遍历仍是 **17** 张表 | ✅ 三趟真实运行里 `build_gold.TABLES` / `dq_baseline` / `dq_assertions` 展开的都是 17 张表（审计输出逐张列出，无 `dq_audit_log`）；单测 `test_the_audit_table_is_invisible_to_the_three_gold_iterations` 同时钉住 |

**阶段 A–E 已完成，余 F（收口提 PR）。**

### 3.0 阶段 D 实测（2026-08-22）

`make stack-restart-airflow`（本轮**没动 compose 的卷**，restart 足够）→ unpause →
触发。任务 **success**，22.6 秒：

```
dag_dq_audit | run_dq_audit | success | 22:41:41 → 22:42:04
✅ DQ audit (daily): 81 checks, 0 error · 0 warn · 0 could not run
appended 81 row(s) to uoip_meta.dq_audit_log
```

🟢 日志里有**两趟**：22:41:27 是 scheduler 自己调度的，22:42:04 是手动触发的——
**容器视角连 Trino（`.env` 的 `trino:8080`）这条路与宿主机那条各自验过**，
两条不是同一条路，O17 的成因就是把它们混为一谈。

### 3.1 规则清单实测（2026-08-22）

**81 条检查全绿，整趟约 60 秒**（结构性那 17 条占大头，每张表 1–3 秒；
统计性的都是 0.1–0.3 秒，因为一张表只扫一次 profile 供多条规则共用）。

逐条数字与 L3-c 基线**全部对得上**，摘要：

| 维度 | 条数 | 实测 |
|---|---|---|
| 结构性（185 条四件套按表复跑） | 17 | 全部 0 violations |
| 行数下界 | 17 | 7 / 15 / 6 / 3,516 / 25 / 252 / 99 / 19 / 548 / 418 / 49 / 418 / 13,068 / 141,377 / 2,596 / 1,298 / 748 —— 与 L3-c 逐张相同 |
| 行数环比 | 17 | 首趟无基线一律判过；第二趟起全部 0% 变化 |
| 零行表 / 全空列 | 2 | **各 0** |
| 七列已知空值率 | 7 | 93.46 / 94.20 / 68.00 / 10.53 / 61.22 / 10.53 / 71.19 —— 与基线逐位相同 |
| 其余列空值率 | 17 | 全部 0 |
| Silver freshness | 1 | **1 天**（O17 说的「最新一两天偏薄」是稳态，阈值 ≤2 合适） |
| 业务性 | 3 | F1 **908** · 空间命中率 **100%（分母 7,666）** · 时序违规 **0** |

🟢 **F1 = 908，与 L2 阶段 D 的门禁逐位相同**，下界 ≥880 的余量是 28。
🟢 **空间命中率 100% 是真的，不是分母太小**：14 天窗口里 7,666 行带坐标，
全部落进某个分区。全量基线 99.8988% 的那 135 个 `outside every zone` 都是历史行。

---

### 3.2 V3 故障注入实测（2026-08-22）

把 `GOLD-ROWS-MIN-dim_plow_zone` 的下界从 `22` 改成不可能满足的 `99999`，
**不动任何数据**，宿主机与 Airflow 各跑一趟：

| 判据 | 实测 |
|---|---|
| 规则 FAIL | `❌ DQ audit (daily): 81 checks, 1 error`，明细 `observed 25.0, expected >= 99999`。**81 条里只错这一条**，注入没有连带污染 |
| 任务不红 | 宿主机 CLI `exit=0`；DAG run `manual__2026-08-22T22:51:59` **state = success**，日志同时是 `1 error` |
| Discord 实收 | 收到，`❌` 在 `content` 最前面 |

🟢 **「finding 不 fail 任务」在 DAG 路径上得到证明，不只是 CLI 上。**
ADR 0012 规定 2 到此有了可执行的证据——两条路的失败语义本来可能不同
（一个没接住的异常就会把它变成阻断式），而这里同一趟运行里
`1 error` 与 `success` 是并存的。

还原后复跑一趟回到 `✅ 81 checks, 0 error`，`git status` 干净。

---

## 4. 与设计的偏差

| 设计怎么写的 | 实际怎么做的 | 为什么改 |
|---|---|---|
| §5.3「七列已知空值率 → 环比 ±5 个百分点」 | 新增 **`pct_point_change`** comparator，与 `pct_change` 并存 | 设计的 comparator 清单里只有 `pct_change`（相对百分比）。在 93.46% 的基线上「±5%」是 ±4.7 个百分点，「±5 个百分点」才是设计要的那把尺子。两条都留着，因为行数用相对、空值率用绝对 |
| §5.3「空间命中率 ≥ 99.5%」，锚点写的是全量口径 99.8988% | 实现为 **14 天滚动窗口**上的命中率 | §4.3 自己禁止对 Silver 全表扫（O13 实测 read timeout），两条口径在设计里没有对上。窗口值与全量基线可比但不相等，规则的 `note` 里写明了这件事。要全量口径得走 `--cadence manual` 并另开一条规则 |
| §5.4 只提到 `scripts/dq/run_audit.py` | 建表拆到 `scripts/dq/audit_store.py`，**不改 `apply_ddl`** | §0.1 的两堵墙。`apply_ddl` 的 `LAYERS` 元组在别处表示「数据层」，为一张非数据产品的表去改它的形状不划算 |
| — | `dq_audit_log` 多了三列：`cadence` · `previous_observed` · `error_text` | `previous_observed` 让趋势不用自连接就能读；`error_text` 承载「检查跑不起来」这个与 FAIL 不同的状态（ADR 0012 §1 规定 2 的可执行形式）；`cadence` 区分日频与全量扫，否则周频那趟的数字会被当成日频趋势的一个点 |

🔴 **首跑当场抓到两个缺陷，两条都是「规则跑得动但问错了问题」**（§4.1 / §4.2）。

🔴 **一条在写代码时才浮现、值得记住的口径**：`pct_change` 规则在**第一次运行时
一律判过**。没有上一次观测值就没有可比的东西，而让一张新建的 `dq_audit_log`
第一天就全红，等于教所有人忽略它。单测
`test_pct_change_passes_on_the_first_run` 钉死这条。

---

### 4.1 🔴 F1 规则数了 1,436，而门禁量的是 908 —— 绿着的规则失效了

首跑 81 条全绿，其中一条问的是另一个问题。规则漏了 `is_scheduling_era`
（数成全部 99 个事件而非排班期的 59 个），且用 `request_count > 0` **逐行**
而非 `SUM(request_count) > 0` **逐格**。

后果不是数字难看，是**这条规则不再能失败**：下界 880 照 908 定，观测的却是
1,436——**排班期那半塌到 0，它照样绿**。design §3.5 说「规则过严是三个候选里
最可能的一个」，这次是反过来的那一面：**规则过松，且过松比过严更难发现**，
因为它不产生任何噪音。

修法是让规则 SQL 成为门禁 SQL 的**逐字拷贝**，并加单测比对两个字符串
（`test_the_f1_rule_measures_the_same_thing_as_the_in_pipeline_gate`）。
一个数字有两个消费者时，「各写各的 SQL」迟早漂移，而漂移的那一天两边都是绿的。

🔴 **由此带出一条趋势读法**：修好之后 F1 在日志里从 **1,436 掉到 908**（−36.8%），
**变的是规则不是数据**。规则 id 没变，趋势就把两个不同的问题接在了一根线上。
**改动一条规则的语义时应当同时改它的 `rule_id`**——`config/dq/README.md` 已写明
「重命名会开启一条新趋势」，那正是这种时候要的行为，不是要避免的副作用。

### 4.2 🟡 百分比规则必须自带分母

首跑的空间命中率报了个光秃秃的 **100**。`sql` 类检查当时不记 `rows_checked`，
于是「命中率完美」和「窗口里只有三行带坐标」在日志里长得一模一样——
**一个读不出来的数字，等于没测**。

现在 `sql` 检查可以返回第二列作为分母，命中率规则带上了 has_geo 计数，
实测 **7,666**，100% 由此可信。单测
`test_a_rate_rule_selects_its_own_denominator` 钉死「带 `100.0 *` 的规则必须
多选一列」。

### 4.3 🟡 两条环境事实（第三次遇到同一个坑）

- 🔴 **`airflow dags unpause` 的回显仍然是改之前的状态**：这次打印
  `is_paused | True`，而紧接着的 `dags details` 是 `is_paused: 'False'`。
  L2 §4.12 记过一次，**原样复现**。判据只能用 `dags details ... | grep is_paused`。
- **容器名带 compose project 前缀**：是 `uoip-airflow-scheduler-1`，
  不是 `airflow-scheduler`。
- **`airflow dags list-runs` 在 Airflow 3 换了参数形状**：`-d <id> --limit N`
  不再被接受，dag_id 改成位置参数。查任务状态用
  `airflow tasks states-for-dag-run <dag_id> <run_id> -o table` 更稳。

### 4.4 🟡 故障注入自身有两个时序坑，差点误判一趟成功的运行

两条都不是代码缺陷，是**验证手法**的坑，下次做注入类验证照样会踩：

1. **`dags trigger` 返回时 task 还在 `queued`。** 紧跟着 grep 日志必然只看到
   上一趟的行，看起来像「这趟没跑」。实测 trigger 到 task 落第一行日志
   约 30 秒。判据是 `list-runs` 里那条 run 的 `end_date` 非空，
   **不是 `trigger` 的返回**。
2. **还原窗口是竞态的。** §6.1 原来的命令把 `git checkout` 直接跟在 `trigger`
   后面——若 task 起得慢，它读到的就是已还原的规则，注入等于没做。
   本轮侥幸没中（task 22:52:00 起、22:52:33 记日志，早于还原），
   但那是运气不是设计。**先确认 run 结束，再还原。**

---

## 5. 遗留项

- **阶段 F 已收口**：CLAUDE.md 状态段已更新，launch 全篇填完。
  **PR 本轮按用户决定不开**（与 L3 同样处理），分支 `feat/out-of-pipeline-dq-audit`
  已推齐。这不是遗漏，是决定。
- **`SILVER-BIZ-SPATIAL-HIT-RATE` 的窗口口径待观察**：14 天窗口实测 100%
  （分母 7,666），与全量基线 99.8988% 不是同一个数。跑满一周看趋势稳不稳，
  再决定要不要另加一条 `cadence: weekly` 的长窗口规则。
- 第三批（跨层对账 + 计分卡 + `certified`/`suspect` 打标）按设计另开一篇。

---

## 6. 交接：下一次从这里开始

### 6.1 V3 已跑完，只剩提 PR

阶段 A–E 全部完成，生产在跑。**接手唯一要做的是提 PR**（分支
`feat/out-of-pipeline-dq-audit`）。V3 的实测记录在 §3.2，注入手法与它的两个
时序坑在 §4.4——真要复现，照 §4.4 的顺序做，不要照本节原先那段命令。

### 6.2 已经确定的事，不要重开

- **F1 = 908**，与 L2 门禁逐位相同；下界 ≥880 的余量 28。**不要改成等值**（§4.2 通则）。
- **`dq_audit_log` 是追加型**，`make gold-build` 那套四步重建（R4）永远不碰它。
- **改一条规则的语义时同时改它的 `rule_id`**，否则趋势把两个问题接成一根线（§4.1）。
- 三趟真实运行的行数与 L3-c 基线**逐张相同**，Gold 侧没有任何待查项。

- **第三批**：跨层对账（Bronze→Silver 守恒 / Silver→Gold roll-up）+ 计分卡 +
  `uoip_meta.gold_certification` 打标实现 + Superset 看板提示。载体已在
  design O3 定案，实现另开一篇。
- **趋势要攒够点才有意义**：`dq_audit_log` 第一天只有一个点，环比规则
  （§4.2 的 ±10%）在第二次运行之前恒过。**这不是缺陷，但别把「全绿」
  读成「趋势检查生效了」**。

---

## 7. 上线后需要观察的

| 盯什么 | 多久 | 超过什么就动手 |
|---|---|---|
| 每日 `warn` 条数 | 两周 | 某条规则天天 `warn` → 回到 design §3.5 问「规则过严还是数据真变了」，**不是删掉它** |
| `error` 条数 | 持续 | 非 0 就人工复核；修复走 CLI，**审计不改数据** |
| 审计任务自身的红 | 持续 | 红 = 检查跑不起来（不是发现问题），优先级高于任何 finding |
| `dq_audit_log` 行数增长 | 一个月 | 日频 × 几十条规则，一年几万行，无需清理；真要清也只能归档不能重建 |

---

## 8. 现状一句话

**第一批与第二批都在生产跑着**：`dag_audit_bronze` 的 `audit_integrity`（第一批）
+ `dag_dq_audit` 的 81 条检查（第二批，`30 8 * * *`）。`uoip_meta.dq_audit_log`
每天追加 81 行。接手先读 design §4（三条通则）和本篇 §0（四个坑）。
