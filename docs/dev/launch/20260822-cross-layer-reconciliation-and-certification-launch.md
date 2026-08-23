# 跨层对账与 Gold 认证（第三批）上线记录

> **Date**: 2026-08-22（开篇日） · **Updated**: 2026-08-23 ·
> **Design**: [../design/20260822-cross-layer-reconciliation-and-certification.md](../design/20260822-cross-layer-reconciliation-and-certification.md) ·
> **ADR**: [0012](../adr/0012-data-quality-audit.md) §6 第三批
> **Result**: 阶段 A–D 代码已完成并离线跑绿（`7fe0579`）；阶段 E–G
> （部署到 Airflow、对生产 Trino 跑通、W1–W8 故障注入）**尚未执行**——本会话
> 没有接入生产 MinIO/Trino/Airflow 栈，只能验证到 `make lint` /
> `make test-unit-offline` / `make test-dags` 这一层

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

| 阶段 | 内容 | 可否回滚 | 判据 | 状态 |
|---|---|---|---|---|
| **A** | 执行器扩能力：两个新 check type + 加载期禁令 + 单测 | 是（纯新增） | `make lint` + `make test-unit-offline` 全绿，且**已有 81 条检查行为不变** | ✅ 代码完成，`rules.py` 新增 `bronze_manifest_sum` / `chunked_sql`（`CROSS_LAYER_CHECK_TYPES`），单测随 `test-unit-offline` 一起跑绿 |
| **B** | `sql/meta/gold_certification.sql` + 建表 | 是（DROP + 清 prefix） | 表建出来；**V5 隔离重验**（三处遍历仍是 17 张表） | ✅ DDL 已写（放 `sql/meta/`，同 §0.1 的处置）；⏳ 建表本身与 V5 重验**未跑**——需要生产 Trino |
| **C** | 5 条对账规则进 `config/dq/rules.yaml` | 是 | 宿主机 `make dq-audit --cadence weekly` 跑通，could-not-run **0** | ✅ 6 条规则已落（1 条 `bronze_manifest_sum` + 4 条 `chunked_sql` + 1 条 `sql`，比设计的 5 条多一条）；⏳ 对生产 Trino 的实跑**未做** |
| **D** | `scorecard.py` + `certify.py` + 两个 make target | 是（只读 + 追加） | 宿主机跑通，写出一行 `certified` | ✅ 两个脚本 + `make dq-scorecard` / `make dq-certify` 均已落地，`certify.decide()` 的三态状态机（§0.3）已实现；⏳ 对生产写出一行 `certified`**未做** |
| **E** | `dag_dq_audit` 串两个下游任务 + 部署 + 容器内跑一趟 | 是 | 三个任务全绿，`run_id` 三处一致 | ✅ `dags/dag_dq_audit.py` 已把 `scorecard` / `certify` 接成 `trigger_rule="all_done"` 的下游任务（`make test-dags` 33 项全绿）；⏳ **部署到 Airflow 容器、实际触发一趟未做**——本会话没有接入 compute 节点 |
| **F** | **W6 两次故障注入**（`suspect` 与 `unknown` 各一次） | 是 | 两种状态都真的出现过，且**任务都不红** | ⏳ **未做**，依赖 E 先部署 |
| **G** | 收口：填 §3、更新 CLAUDE.md、PR | — | — | 🚧 本次更新只做到"文档与已有代码对齐"，§3 的 W1–W8 仍标 ⏳ 如实反映未跑；PR 仍按此前决定不开，等 E/F 补上再收口 |

🔴 **A 与 C 的顺序不能反**（§0.1）。
🔴 **B 之后必须重验 V5**：第二批验过一次是对 `dq_audit_log` 验的，
**新增一张表就要重验一次**——那三处遍历漏掉任何一处，审计表会被当业务表 purge。

---

## 3. 验收判据的实际结果（待填）

**2026-08-23 offline 复核**：`make lint` 干净 · `make test-unit-offline`
**1,041 passed / 7 skipped** · `make test-dags` **33 passed**（含
`dag_dq_audit` 三任务的 import + trigger_rule 断言）。这只证明代码在**没有
生产 Trino/MinIO/Airflow** 的前提下是自洽的——W1–W8 需要真实连接，仍是 ⏳。

| # | 判据 | 期望 | 实测 |
|---|---|---|---|
| **W1** | 五条对账规则跑通 | could-not-run **0** | ⏳ |
| **W2** | Bronze→Silver 守恒 | 差值 0（除最近 `late_arrival_days` 天） | ⏳ |
| **W3** | F1 / F8 两条 roll-up 的口径差异被解释掉 | 与 design §2.2/§2.3 的过滤条件一致，差值在容差内 | ⏳ |
| **W4** | 连跑两趟结论逐条相同 | 除 `run_id`/时间戳外全同 | ⏳ |
| **W5** | 正常一天写出 `certified` | `gold_certification` 一行 | ⏳ |
| **W6** | 故障注入两次 → `suspect` / `unknown` | 两种状态都出现，**任务都不红** | ⏳ |
| **W7** | `uoip_meta` 隔离仍成立 | 三处遍历仍是 **17** 张表 | ⏳ |
| **W8** | `make lint` + `make test-unit-offline` + `make test-dags` | 全绿 | ⏳ |

---

## 4. 与设计的偏差

（跑完填。第二批的经验：这一节是最有价值的一节，**首跑抓到的两个缺陷都是
「规则跑得动但问错了问题」**，而它们在 `make lint` + 全套单测全绿的前提下存在。）

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

现状一句话（2026-08-23 更新）：**阶段 A–D 的代码已经写完并提交
（`7fe0579`），offline 门禁（lint / test-unit-offline / test-dags）全绿，
但没有一步在生产 Trino/MinIO/Airflow 上跑过。** 接手直接从 **阶段 E** 开始：
部署 `dag_dq_audit`（含新增的 `scorecard` / `certify` 两个下游任务）到计算
节点、按 §5.2 的顺序验证、再做 §5.3 的两次故障注入（W6）。design §0 的三个坑
和本篇 §0 一样仍然成立，读完再动手。
