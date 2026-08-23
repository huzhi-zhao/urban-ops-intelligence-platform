# 跨层对账与 Gold 认证（第三批）上线记录

> **Date**: 2026-08-22（开篇日） ·
> **Design**: [../design/20260822-cross-layer-reconciliation-and-certification.md](../design/20260822-cross-layer-reconciliation-and-certification.md) ·
> **ADR**: [0012](../adr/0012-data-quality-audit.md) §6 第三批
> **Result**: 阶段 A–D **已跑通生产**（87 条全绿 · 落盘 · 计分卡 · **`certified` 已写出**）。余 E 部署 · F 注入 · G 收口

**本篇是 ADR 0012 的最后一批**。第一批（Bronze 校验 B/C 进 `dag_audit_bronze`，
`a5304cb`）与第二批（`dq_audit_log` + 81 条单层检查 + `dag_dq_audit`）都已在
生产跑着，本篇**只增加一层**，不重构任何已跑通的东西。

**提前开篇的理由**：与第二批同——没有一步不可逆（审计只读，新表删了重建即可），
但 §0 那三条会在写第一行代码之前就把人绊倒。

---

## 0. 开工前必须知道的三件事

### 0.1 🔴 现有执行器不支持本批要做的两件事，先扩能力再写规则

`scripts/dq/run_audit.py` 的 `sql` 检查只替换三个占位符，**没有分年切块、
连不上对象存储**。照现有能力硬写对账规则，会写出：

- 一条读 Bronze 却只能连 Trino 的规则 → **写不出来**；
- 一条 4,878 分区全表扫的 roll-up → **could-not-run**，而 could-not-run 是
  唯一会 raise 的情况，于是 DAG 变红、被静音。

所以顺序是**先加两个 check type（`bronze_manifest_sum` / `chunked_sql`），
再写规则**，不能反。详见 design §2.0。

### 0.2 🔴 三个「显而易见的等式」里有两个是假的

细化时核 DML 核出来的，写代码前必须先认下来（design §0.2 / §2.3）：

| 关系 | 真相 |
|---|---|
| Bronze == Silver | ✅ 成立，锚点 **12,474,313**，拒绝行 0 |
| Silver 带标签计数 == F8 之和 | 🔴 **假**。F8 的 DML 最后是 `INNER JOIN dim_admin_label`，维表里没有的标签**被静默丢掉**，所以只能是 `>=` |
| Silver 事件窗口计数 == F1 之和 | ⚠️ 条件成立，Silver 侧必须套**同样三个过滤**（22 个排班分区 / 事件窗口 / 6 个冬季类别） |

🔴 **把不该相等的关系写成等式，后果不是算错，是规则天天红 → 被人手动改宽 →
彻底失去意义。** 比不做还糟。

🟢 而 F8 那条丢掉的部分**本身就是一条有价值的 DQ 发现**：上游冒出一个没人
见过的 ward 名，F8 会安静地少统计一批工单，现在这套里没有任何东西会说话。
所以拆成两条规则，第二条（未知标签数 == 0）才是真正要的那条。

### 0.3 🔴 `certified` 是会被人引用的结论，必须有第三个状态

打标之后看板与下游会拿它当「这批数据可信」的凭证。两条硬约束：

1. **`certified` 只由 `error` 级全绿产生**，`warn` 不参与；
2. **必须有 `unknown`**：`suspect` = 查了有问题，`unknown` = 没能查。
   合并这两个，审计自己坏掉的那天就会给出一个看起来像结论的结论。

由此推出一条容易写反的编排细节：`certify` 任务的 `trigger_rule` 必须是
**`all_done` 而不是 `all_success`** —— `run_dq_audit` 在 could-not-run 时会
raise，而那**正是最需要写一行 `unknown` 的时候**。

---

## 1. 前置检查

- [ ] `.env` 里 Trino 是**容器视角**（`TRINO_HOST=trino` / `8080`）。
      宿主机跑命令临时加前缀 `TRINO_HOST=localhost TRINO_PORT=8090`，
      **不要改 `.env`**（O17 的成因就是这个）。
- [ ] 第二批仍在跑：`make dq-audit` = **81 条 / 0 error**。
- [ ] 17 张 Gold 表仍有数据；`make gold-assert` = 185 条 / 0 violations。
- [ ] `dq_audit_log` 里已有**至少两天**的记录（计分卡的趋势要两个点才有意义）。
- [ ] 分支从 `feat/out-of-pipeline-dq-audit` 起，或从 main 起新分支
      `feat/cross-layer-reconciliation`。

---

## 2. 执行清单

一批一个阶段，**阶段之间停下等确认**。

| 阶段 | 内容 | 可否回滚 | 判据 |
|---|---|---|---|
| **A** | 执行器扩能力：两个新 check type + 加载期禁令 + 单测 | 是（纯新增） | `make lint` + `make test-unit-offline` 全绿，且**已有 81 条检查行为不变** |
| **B** | `sql/meta/gold_certification.sql` + 建表 | 是（DROP + 清 prefix） | 表建出来；**V5 隔离重验**（三处遍历仍是 17 张表） |
| **C** | **6** 条对账规则进 `config/dq/rules.yaml` | 是 | 宿主机 `make dq-audit --cadence weekly` 跑通，could-not-run **0** |
| **D** | `scorecard.py` + `certify.py` + 两个 make target | 是（只读 + 追加） | 宿主机跑通，写出一行 `certified` |
| **E** | `dag_dq_audit` 串两个下游任务 + 部署 + 容器内跑一趟 | 是 | 三个任务全绿，`run_id` 三处一致 |
| **F** | **W6 两次故障注入**（`suspect` 与 `unknown` 各一次） | 是 | 两种状态都真的出现过，且**任务都不红** |
| **G** | 收口：填 §3、更新 CLAUDE.md、PR | — | — |

🔴 **A 与 C 的顺序不能反**（§0.1）。
🔴 **B 之后必须重验 V5**：第二批验过一次是对 `dq_audit_log` 验的，
**新增一张表就要重验一次**——那三处遍历漏掉任何一处，审计表会被当业务表 purge。

---

## 3. 验收判据的实际结果（待填）

| # | 判据 | 期望 | 实测 |
|---|---|---|---|
| **W1** | **六**条对账规则跑通 | could-not-run **0** | ✅ 宿主机 dry-run，**87 条检查 / 0 error / 0 warn / 0 could-not-run** |
| **W2** | Bronze→Silver 守恒 | 差值 0（除最近 `late_arrival_days` 天） | ✅ **差值 0**，0.8 秒 |
| **W3** | F1 / F8 roll-up 的口径差异被解释掉 | 与 design §2.2/§2.3 的过滤条件一致，差值在容差内 | ✅ F1 = **0**；F8 ward / neighbourhood 各 **6**，成因见 §4.1 |
| **W4** | 连跑两趟结论逐条相同 | 除 `run_id`/时间戳外全同 | ✅ **87 条逐条相同**，含六条 cross_layer（0 / 0 / **6** / **6** / 0 / 0）。耗时也几乎一致（162 / 220 / 223 / 417 秒）——成本是分片数的固定开销，不是首跑的一次性代价 |
| **W5** | 正常一天写出 `certified` | `gold_certification` 一行 | ✅ `run dq-20260823T011552-4d9a94` · **87 checks / 0 error / 0 warn / 0 could-not-run** |
| **W6** | 故障注入两次 → `suspect` / `unknown` | 两种状态都出现，**任务都不红** | ⏳ |
| **W7** | `uoip_meta` 隔离仍成立 | 三处遍历仍是 **17** 张表 | ✅ 单测 `test_the_certification_table_is_invisible_to_the_three_gold_iterations` |
| **W8** | `make lint` + `make test-unit-offline` + `make test-dags` | 全绿 | ✅ lint 干净 · unit **1042 passed / 7 skipped** · dags **33 passed** |

### 3.0 🟢 计分卡的分母按 `error` 级算，别读成「少跑了规则」

首次跑出来的四维通过率是 `business 2/2` · `cross_layer 3/3` ·
`statistical 37/37` · `structural 17/17`，而 cross_layer 明明有 **6** 条规则。
**不是少跑了三条** —— 通过率只统计 `error` 级：

| 维度 | error | warn |
|---|---|---|
| cross_layer | CONSERVATION · F8-UNKNOWN-LABEL · F5-F6-F7 | F1-ROLLUP · F8-ROLLUP-WARD · F8-ROLLUP-NEIGHBOURHOOD |
| business | SPATIAL-HIT-RATE · CLOSED-AFTER-OPEN | F1-NONZERO-CELLS |

这正是 §0.3 第 1 条要的形状：**warn 不参与认证判定，但在明细与 `warn_count`
里看得见**。否则「有 warn 但仍 certified」和「全绿 certified」在下游一模一样。

### 3.1 六条对账规则的实测耗时（W-O1 定案依据）

| 规则 | 实测 | observed | cadence |
|---|---|---|---|
| `XLAYER-BRONZE-SILVER-CONSERVATION` | **0.8s** | 0 | daily |
| `XLAYER-GOLD-INTERNAL-F5-F6-F7` | **0.3s** | 0 | daily |
| `XLAYER-SILVER-GOLD-F1-ROLLUP` | 168.3s | 0 | weekly |
| `XLAYER-SILVER-GOLD-F8-ROLLUP-WARD` | 227.4s | 6 | weekly |
| `XLAYER-SILVER-GOLD-F8-ROLLUP-NEIGHBOURHOOD` | 228.5s | 6 | weekly |
| `XLAYER-SILVER-GOLD-F8-UNKNOWN-LABEL` | **426.4s** | 0 | weekly |

**W-O1 定案：维持 `weekly`，不降 `manual`。** 判据按**单条**量——最贵的一条
426 秒 = 7.1 分钟，没到 10 分钟的门槛；四条合计 17.5 分钟是周跑的总账，
而**日频只多了 1.1 秒**（两条 daily 规则之和）。

🔴 **真要削，该削的是那两条 F8-ROLLUP，不是 UNKNOWN-LABEL。** 前两条量的是
同一个迟到现象（§4.1），互为冗余；后者是这批里唯一能说出「上游冒出一个没人
见过的 ward 名、F8 安静少统计一批工单」的东西，降到 `manual` 等于从此没人跑。

---

## 4. 与设计的偏差

### 4.1 🟢 F8 的两条 roll-up 各 = 6，而 UNKNOWN-LABEL = 0 —— `>=` 的实证

首跑就把「为什么不能写等值」演示了一遍。没有标签被 `dim_admin_label` 丢掉
（UNKNOWN-LABEL = 0），所以这 6 **不是丢行**，是 **F8 是快照而 Silver 还在长**：
上次 Gold 构建之后又到了 6 条冬季工单。两条规则都恰好是 6，说明是同 6 条工单
同时带 ward 和 neighbourhood——这也顺带证明了「按标签出现次数计数」这个改法
（§4.3）在数对东西。

🔴 **按 design 字面写成等值，这条规则第一天就是红的**，然后会被手动改宽，
最后失去意义——正是 §0.2 描述的失效路径。

而 **F1 roll-up = 0（精确相等）**，因为 F1 的事件全是历史事件、窗口早已关闭，
没有迟到工单可进。F1 与 F8 的这个差别本身就是那个 6 的解释。

### 4.2 F8 roll-up 拆成两条，规则总数是 **6 不是 5**

design §2.3 的 🔴「ward 与 neighbourhood 分开对，不合并成总数」与 §8 清单的
「+5 条规则」直接打架。按 🔴 走，拆成
`XLAYER-SILVER-GOLD-F8-ROLLUP-{WARD,NEIGHBOURHOOD}`。合并之后「扇出」和
「丢标签」就分不开了——而 §4.1 的 6 正好落在扇出那一侧。

### 4.3 🔴 F8 roll-up 两侧都按「标签出现次数」计数，不是工单行数

design §2.3 写「Silver 带标签计数 >= F8 之和」。若 Silver 侧按**工单行数**数，
一条工单带两个标签在 F8 产生两行，**方向会反过来**，`>=` 恒假。改成按标签
出现次数（每条规则只看自己那一个 `*_raw` 列），`>=` 才是恒定的，且差值恰好
就是 `INNER JOIN dim_admin_label` 丢掉的那部分——规则想量的正是它。

### 4.4 F1 roll-up 用方向约束 `>= 0`，不是 `pct_change` 容差

design §2.2 要的是「不要等值」，理由是 Open-Meteo 回修会重切事件边界。但两侧
都读**当前** `dim_snowfall_event`，被重切掉的旧事件 id 在 F1 侧自动落选——
**漂移已经被抵消掉了**。真正会让两个数不等的是迟到工单（F1 是快照、Silver
一直在长），方向恒为 Silver ≥ F1。而 `pct_change` 需要一个没人量过的容差数字，
规则文件的规矩是不收这种值。首跑实测 F1 = 0，与该推理一致。

### 4.5 §2.4 里 F7 的版本列叫 `model_version`，不叫 `forecast_version`

design 写的 `forecast_version` 是 **F6 构建时的入参名**（`FORECAST_VERSION=`），
不是 `fact_recommendation` 的列名。被既有单测
`test_a_sql_rule_only_names_columns_its_table_actually_has` 当场抓出来——
那条测试是第二批留下的，这次直接兑现了价值。

---

## 5. 上线发布计划

### 5.1 发布窗口与顺序

**本批没有不可逆步骤，但有一步会改已在跑的 DAG**（阶段 E），所以窗口按它定。

| 步骤 | 何时 | 影响面 |
|---|---|---|
| A–D（代码 + 新表 + 宿主机验证） | 任意时间 | **零影响**：`dag_dq_audit` 不受影响，新规则只在手动跑时执行 |
| E（改 DAG + 重启 Airflow） | **避开 `30 8` 那趟日频运行** | 重启期间若正好在跑，那趟会中断——重跑即可，审计只读无副作用 |
| F（故障注入） | 紧跟 E，同一个操作窗口 | 会发两条 ❌ Discord，**事先知会看告警的人** |
| G（收口 + PR） | 之后 | — |

🔴 **阶段 E 部署要用 `make stack-recreate-airflow` 还是 `restart`，取决于
有没有动 compose 的卷。** 本批**预计不动卷**（新脚本在已挂载的 `scripts/` 下），
所以 `make stack-restart-airflow` 够用；但真动了卷，`restart` **不重挂**，
这坑在本项目已经踩过两次。

### 5.2 部署后的验证顺序（照做，别跳）

```bash
# 1. 确认 DAG 解析没坏 —— 改了 DAG 之后第一件事，不是触发
docker exec uoip-airflow-scheduler-1 airflow dags list-import-errors

# 2. 确认没有变回 paused（改文件之后会变，本项目已复现两次）
docker exec uoip-airflow-scheduler-1 airflow dags details dag_dq_audit -o yaml | grep is_paused

# 3. 触发，然后等——不要立刻 grep 日志
docker exec uoip-airflow-scheduler-1 airflow dags trigger dag_dq_audit
sleep 90   # 三个任务，比第二批的单任务久

# 4. 三个任务的状态
docker exec uoip-airflow-scheduler-1 airflow dags list-runs dag_dq_audit -o plain | head -2
```

🔴 **第 3 步的 `sleep` 不是保守，是判据**：`dags trigger` 返回时 task 还在
`queued`，紧跟着 grep 只会看到上一趟的日志。第二批就差点据此误判一趟成功的
运行是废的（第二批 launch §4.4）。真正的判据是 run 的 `end_date` 非空。

### 5.3 W6 故障注入的具体做法

**两次注入，分两趟做，不要合并**——合并了就分不清哪个状态是哪条路径产生的。

**注入 1 → `suspect`**（造一条 `error` 级 finding）：

```bash
# 把一条行数下界改成不可能满足的值，不动任何数据
sed -i 's/^    expected: 22$/    expected: 99999/' config/dq/rules.yaml
docker exec uoip-airflow-scheduler-1 airflow dags trigger dag_dq_audit
# 等 run 的 end_date 非空，再还原（🔴 还原窗口是竞态的，见第二批 §4.4）
```

期望：`run_dq_audit` 有 1 条 error → **任务 success** → `gold_certification`
新增一行 `status='suspect'` → Discord 收到 ❌。

**注入 2 → `unknown`**（让一条检查跑不起来）：

```bash
# 把一条 sql 规则指向一张不存在的表 —— 这是"问不出答案"，不是"答案不好"
```

期望：`run_dq_audit` **raise（任务红）** → 而 `certify` 因为
`trigger_rule="all_done"` **仍然执行** → `gold_certification` 新增一行
`status='unknown'`。

🔴 **注入 2 是本批唯一会让任务变红的场景，而这是对的**：检查跑不起来 =
审计坏了，必须有人管。它与「finding 不 fail 任务」不矛盾，是同一条规则的另一半。

### 5.4 回滚

| 阶段 | 怎么退 |
|---|---|
| A / C / D | `git revert`，不涉及任何已落盘数据 |
| B | `DROP TABLE gold_certification` + 清 `meta/gold_certification/` prefix |
| E | 回退 `dag_dq_audit.py` 到单任务版本 + 重启 Airflow。**`dq_audit_log` 里已写的行不用清**，追加型表的历史行本来就是历史 |

🔴 **`dq_audit_log` 永远不重建**（R4 不适用）。回滚回滚的是代码，不是账本。

### 5.5 上线后观察

| 盯什么 | 多久 | 超过什么就动手 |
|---|---|---|
| `gold_certification` 的 `status` 分布 | 两周 | 出现 `unknown` 就立刻查——那是审计自己坏了 |
| 对账规则的 `warn` 频率 | 两周 | 某条天天 warn → 回到 design §0.2 问「是不是把不该相等的写成了等式」，**不是删掉它** |
| roll-up 的耗时 | 首次 + 一周 | 超过 10 分钟就把 `cadence` 从 `weekly` 降到 `manual`（W-O1） |
| 计分卡的 Discord 噪音 | 两周 | 天天发全绿汇总没人看 → 改成只在有 finding 或状态变化时发（W-O3） |

---

## 6. 交接

现状一句话：**阶段 A–D 的代码全部写完、门禁全绿，宿主机 dry-run 87 条全通过；
还没有落过一行盘，DAG 也没部署。**

接手从这里继续，顺序不要跳：

1. ✅ 已做：落盘 · `make dq-scorecard` · `make dq-certify`（`certified`）。
2. ✅ 已做：W4 连跑两趟，87 条逐条相同。
3. **阶段 E**：部署 DAG，按 §5.2 走。🔴 **「无 import error」不是判据** ——
   scheduler 可能还在用重启前解析的那份，判据是 `airflow tasks list dag_dq_audit`
   **列出三个任务**。重启后要**重新确认 paused 状态**。触发后等 90 秒再看日志。
4. **阶段 F**：两次故障注入，按 §5.3，**分两趟做**。
5. **阶段 G**：填 §3 剩下的 ⏳、更新 CLAUDE.md、提 PR。

🟡 **计分卡的趋势现在恒为空**：`dq_audit_log` 里还没有本批规则的历史点，
连续失败天数在跑满一周之前恒为 0 或 1。**别把「全绿」读成「趋势检查生效了」**
（design §3 的 🟡，第二批 §6.2 已记过一次）。
