# 管道外数据质量审计（第二批）上线记录

> **Date**: 2026-08-22（开篇日） ·
> **Design**: [../design/20260822-out-of-pipeline-dq-audit.md](../design/20260822-out-of-pipeline-dq-audit.md) ·
> **ADR**: [0012](../adr/0012-data-quality-audit.md)
> **Result**: 进行中 —— 第一批已完成，第二批未开工

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
| **E** | V3 故障注入（smoke prefix 造违规）+ 收 Discord | 是 |
| **F** | 收口：填 §3 门禁表、更新 CLAUDE.md 状态、提 PR | — |

阶段 A/B 的顺序不能反：规则清单定下 `dq_audit_log` 要存哪些字段，
先建表就会漏列，而这张表是追加型、改列比 Gold 麻烦。

---

## 3. 验收判据的实际结果（留空待填）

| # | 判据 | 期望 | 实测 |
|---|---|---|---|
| **V1** | `make dq-audit` 跑通，全部规则落进 `dq_audit_log` | 误报 0 | |
| **V2** | 连跑两趟结论逐条相同 | 除 `run_id`/`checked_at`/耗时外全同 | |
| **V3** | 造一条违规 → 规则 FAIL、**任务不红**、Discord 实收 | 三样都要 | |
| **V4** | `make lint` + `make test-unit-offline` + `make test-dags` | 全绿 | |
| **V5** | `uoip_meta` 隔离生效 | 三处遍历仍是 **17** 张表 | |

### 3.1 规则清单实测（留空待填）

一条规则一行：规则 id / 维度 / 期望 / 实测 / 通过 / 耗时。
**第一次跑出来的这张表就是后续趋势的第 0 个点**，与 L3-c 的基线对不上的
每一条都要在 §4 给出解释——对不上不一定是数据坏了，也可能是规则写错了
（design §3.5：规则过严是三个候选里最可能的一个）。

---

## 4. 与设计的偏差（留空待填）

| 设计怎么写的 | 实际怎么做的 | 为什么改 |
|---|---|---|
| §5.3「七列已知空值率 → 环比 ±5 个百分点」 | 新增 **`pct_point_change`** comparator，与 `pct_change` 并存 | 设计的 comparator 清单里只有 `pct_change`（相对百分比）。在 93.46% 的基线上「±5%」是 ±4.7 个百分点，「±5 个百分点」才是设计要的那把尺子。两条都留着，因为行数用相对、空值率用绝对 |
| §5.3「空间命中率 ≥ 99.5%」，锚点写的是全量口径 99.8988% | 实现为 **14 天滚动窗口**上的命中率 | §4.3 自己禁止对 Silver 全表扫（O13 实测 read timeout），两条口径在设计里没有对上。窗口值与全量基线可比但不相等，规则的 `note` 里写明了这件事。要全量口径得走 `--cadence manual` 并另开一条规则 |
| §5.4 只提到 `scripts/dq/run_audit.py` | 建表拆到 `scripts/dq/audit_store.py`，**不改 `apply_ddl`** | §0.1 的两堵墙。`apply_ddl` 的 `LAYERS` 元组在别处表示「数据层」，为一张非数据产品的表去改它的形状不划算 |
| — | `dq_audit_log` 多了三列：`cadence` · `previous_observed` · `error_text` | `previous_observed` 让趋势不用自连接就能读；`error_text` 承载「检查跑不起来」这个与 FAIL 不同的状态（ADR 0012 §1 规定 2 的可执行形式）；`cadence` 区分日频与全量扫，否则周频那趟的数字会被当成日频趋势的一个点 |

🔴 **一条在写代码时才浮现、值得记住的口径**：`pct_change` 规则在**第一次运行时
一律判过**。没有上一次观测值就没有可比的东西，而让一张新建的 `dq_audit_log`
第一天就全红，等于教所有人忽略它。单测
`test_pct_change_passes_on_the_first_run` 钉死这条。

---

## 5. 遗留项

- **生产执行未开始**：阶段 B/C/D/E 的实际跑动（建 `uoip_meta`、宿主机跑一趟、
  部署 DAG 并 unpause、V3 故障注入）都要在计算节点上做，代码侧已就绪。
- **V3 的造违规方式待定**：倾向在 smoke prefix 上建一张空表让下界规则 FAIL，
  而不是动生产数据。判据是「规则 FAIL + 任务绿 + Discord 实收」三样同时成立。
- 第三批（跨层对账 + 计分卡 + `certified`/`suspect` 打标）按设计另开一篇。

- **第三批**：跨层对账（Bronze→Silver 守恒 / Silver→Gold roll-up）+ 计分卡 +
  `uoip_meta.gold_certification` 打标实现 + Superset 看板提示。载体已在
  design O3 定案，实现另开一篇。
- **趋势要攒够点才有意义**：`dq_audit_log` 第一天只有一个点，环比规则
  （§4.2 的 ±10%）在第二次运行之前恒过。**这不是缺陷，但别把「全绿」
  读成「趋势检查生效了」**。

---

## 6. 上线后需要观察的

| 盯什么 | 多久 | 超过什么就动手 |
|---|---|---|
| 每日 `warn` 条数 | 两周 | 某条规则天天 `warn` → 回到 design §3.5 问「规则过严还是数据真变了」，**不是删掉它** |
| `error` 条数 | 持续 | 非 0 就人工复核；修复走 CLI，**审计不改数据** |
| 审计任务自身的红 | 持续 | 红 = 检查跑不起来（不是发现问题），优先级高于任何 finding |
| `dq_audit_log` 行数增长 | 一个月 | 日频 × 几十条规则，一年几万行，无需清理；真要清也只能归档不能重建 |

---

## 7. 交接

接手先读 design §4（三条通则）和本篇 §0（四个坑）。
两句话的现状：**第一批已在生产跑着，第二批一行代码没写**，
`dq_audit_log` 与 `uoip_meta` 都还不存在。
