# 跨层对账与 Gold 认证（第三批）

> **Date**: 2026-08-22 ·
> **ADR**: [0012](../adr/0012-data-quality-audit.md) §6 第三批 ·
> **前置**: [第二批设计](20260822-out-of-pipeline-dq-audit.md) ·
> [第二批上线记录](../launch/20260822-out-of-pipeline-dq-audit-launch.md)
> **Status**: 细化中，一行代码未写

ADR 0012 §6 的**第三批，也是最后一批**：跨层对账（design §3.4）+ 计分卡
（§3.5）+ `certified` / `suspect` 打标（O3）。第一批（Bronze 校验 B/C）与
第二批（`dq_audit_log` + 81 条单层检查）都已在生产跑着，本篇**只增加一层**。

**本篇不重开的口径**（第二批已定案，照做）：

- **管道外一律不用等值行数门禁**，用绝对下界 + 环比（第二批 §4.2）。
- **finding 不 fail 任务**，只有「检查跑不起来」才 raise。
- **审计不改数据**。修复回填走 CLI，在人的手里。
- **改一条规则的语义时同时改 `rule_id`**，否则趋势把两个问题接成一根线。
- **`uoip_meta` 与 Gold 隔离**：`build_gold.TABLES` / `dq_baseline` /
  `dq_assertions` 三处遍历到的必须仍是 **17 张表**。

---

## 0. 三个会在写代码前就把人绊倒的地方

### 0.1 🔴 「对账」天然是全表扫，而全表扫在这套栈上是不成立的

O13 实测：`silver_service_request` **12,477,414 行 / 4,878 个日分区**，
七个真实列的全表扫**直接 read timeout**。而对账的字面形式恰恰是
「Silver 全量 vs Gold 聚合」——**照字面写必然写出一条永远 could-not-run 的规则**，
然后它会 raise（因为跑不起来才是唯一失败），把 DAG 变红，然后被静音。

处置有三条，缺一不可：

1. **Bronze 侧不扫对象,读 manifest。** 每个 manifest 的 `record_count`
   描述的就是那份 NDJSON 的行数（`ingestion/loaders/s3_loader.py:288`）。
   Bronze→Silver 守恒因此是**几千次 HEAD/GET 小 JSON**，不是读 12 M 行。
2. **Silver/Gold 侧只用 `COUNT(*)` 与分组计数**，Parquet footer 能答，不碰真实列。
3. **真需要按真实列聚合的（F1/F8 的 roll-up）一律按 R2 分年切**，
   与建表 DML **同一套切法**，且 `cadence` 定为 `weekly` 或 `manual`，不进日频。

🔴 **分年切之后不能把每年的百分比再平均**（R3）：分子分母各自求和，
比值最后算一次。这条在对账里比在建表里更容易犯，因为「一致率」听起来天然是百分比。

### 0.2 🔴 三个「显而易见的等式」里有两个是假的

对账最大的风险不是算错，是**把一个本来就不该相等的关系写成等式**，
然后规则天天红、被人手动改宽、最后失去意义。已知的三个：

| 关系 | 成不成立 | 为什么 |
|---|---|---|
| Bronze 行数 == Silver 行数 | ✅ 成立（有锚点） | 全量首次落地实测 **12,474,313 = 12,474,313**，拒绝行 0 |
| Silver 冬季子集 == F8 行数之和 | 🔴 **不成立** | F8 的行是 `(日, 标签)`，**一条工单带 ward 和 neighbourhood 两个标签就产生两行**；且 77.22% 的行没有任何标签，根本进不了 F8 |
| Silver 事件窗口内计数 == F1 计数之和 | ⚠️ **条件成立** | F1 只覆盖 **22 个有排班的分区 × 6 个冬季类别**，Silver 那边必须套同样三个过滤才可比。少套一个就差一大截 |

🔴 **对账的正确形式是「同一个过滤条件下的两个数」**，不是「两张表的总量」。
每条对账规则必须把它的过滤条件写进 SQL 并在 `note` 里复述一遍——
否则读日志的人无法判断 `−3.2%` 是数据坏了还是口径不同。

### 0.3 🔴 `certified` 是一个**会被人引用的结论**，比任何一条规则都危险

打标之后，看板与下游会拿它当「这批数据可信」的凭证。因此两条硬约束：

1. **`certified` 只由 `error` 级检查全绿产生**，`warn` 不参与。否则
   「有 warn 但仍 certified」和「全绿 certified」在下游看起来一模一样。
2. **没跑过 = 不是 `certified`**，必须是第三个状态而不是默认值。审计当天
   没跑（容器挂了、Trino 不通）与审计跑完全绿，是两件完全不同的事，
   而「没有 `suspect` 行」很容易被读成「没问题」。

---

## 1. 交付物

| # | 产出 | 说明 |
|---|---|---|
| **1** | `sql/meta/gold_certification.sql` | 打标状态表。**追加型**，与 `dq_audit_log` 同 schema、同纪律（O3 已定载体） |
| **2** | `config/dq/rules.yaml` 新增 `dimension: cross_layer` 的规则 | 走已有的 `sql` 检查类型，**不新增 check type** |
| **3** | `scripts/dq/scorecard.py` | 从 `dq_audit_log` 汇总六维通过率与趋势；`make dq-scorecard` |
| **4** | `scripts/dq/certify.py` | 读当天审计结果 → 写 `gold_certification`；`make dq-certify` |
| **5** | `dags/dag_dq_audit.py` 加两个下游任务 | `scorecard` → `certify`，**不新建 DAG** |
| **6** | 单测 | 与第二批同规格 |

**不做的**（划出去，避免范围蔓延）：

- Superset 看板的「数据待核实」提示。**打标只落表**，看板改动是 Superset 侧
  的事，第二批设计 §「Superset 侧改动」已把它划在实现之外。
- 根因分析自动化。§3.5 的闭环里「谁修、修什么」由人判断，审计只提供证据。

---

## 2. 跨层对账的具体规则

### 2.0 先看执行器现在能做什么，不能做什么

`scripts/dq/run_audit.py` 的 `sql` 检查类型只替换三个占位符
（`{{ silver }}` / `{window_start}` / `{window_end}`），**没有分年切块，
也不认 Bronze**。Gold 不需要占位符——执行器连接时注入的默认 schema 就是
`uoip_gold`，裸表名直接解析到 Gold（R6 讲的正是反过来那一半）。

所以本批要**新增两个 check type**，而不是把逻辑硬塞进 `sql`：

| 新 check type | 为什么现有的不够 |
|---|---|
| `bronze_manifest_sum` | Bronze 的行数在 manifest JSON 里，走 boto3 不走 Trino。`sql` 类型连不上对象存储 |
| `chunked_sql` | roll-up 必须按 R2 分年切，而 `sql` 只发一条语句。硬用 `sql` 就是一条 4,878 分区的全表扫，**结果是 could-not-run 而不是慢** |

`chunked_sql` 的形状与 R2 的文件头注一一对应，两个字段都必填：

```yaml
check:
  type: chunked_sql
  chunk_by: calendar_year      # 目前只支持这一种
  chunk_range: [2008, 2027]    # [start, end)，与 DML 同一套
  combine: additive            # 分子分母各自求和；见下面的 🔴
  sql: |
    SELECT COUNT(*) AS numerator, ... AS denominator
    FROM ...
    WHERE s.open_date_local >= DATE '{chunk_start}'
      AND s.open_date_local < DATE '{chunk_end}'
```

🔴 **`combine` 只允许 `additive`。** R3 已经定过：比值不可分块合并。
执行器**把每块的两列各自累加，最后算一次比值**——加载期就拒绝
`combine: ratio` 之类的值，不给人写错的机会。

🔴 **`chunk_start` / `chunk_end` 是执行器生成的,不是规则里写死的日期**
（R5：Gold SQL 里不出现硬编码日期字符串）。

### 2.1 `XLAYER-BRONZE-SILVER-CONSERVATION`（日频，便宜）

**问的是**：滚动窗口内，Bronze 收了多少行，Silver 就该有多少行。

```yaml
check:
  type: bronze_manifest_sum
  source_id: SRC-WPG-311
  dataset: service_requests
  silver_table: silver_service_request
  silver_date_column: open_date_local
comparator: "=="
expected: 0            # 差值
```

- **Bronze 侧**：枚举窗口内每天的 `manifest_YYYY-MM-DD.json`，累加
  `record_count`。几十次读小 JSON，**不读一行数据**。
- **Silver 侧**：`COUNT(*)` 按 `open_date_local` 过滤 —— Parquet footer 能答。
- **observed** = Bronze 之和 − Silver 计数；**rows_checked** = Bronze 之和
  （分母,否则「差 0」和「两边都是 0」长得一样，这是第二批 §4.2 的教训）。
- 🔴 **末尾 `late_arrival_days` 天排除在外**：上游 311 发布滞后约一天（O17），
  当天两边都还在长，把它算进来就是造一个天天响的告警。
- 🔴 **缺 manifest 与 `record_count = 0` 是两回事**：前者 raise
  （could-not-run，审计问不出答案），后者是一个合法观测值。
- **锚点**：全量首次落地 Bronze = Silver = **12,474,313**，拒绝行 0。

### 2.2 `XLAYER-SILVER-GOLD-F1-ROLLUP`（周频）

**问的是**：F1 面板里的 `request_count` 加起来，等不等于 Silver 在**同样三个
过滤**下的工单数。

三个过滤缺一不可，且必须与 `sql/dml/fact_service_request_zone_event.sql`
**逐字同一套**（第二批已经证明过：规则 SQL 与门禁 SQL 逐字拷贝、单测比对字符串，
是唯一能防住「规则跑得动但问错问题」的办法）：

1. `plow_zone` 属于 22 个 `has_plow_schedule = true` 的分区
2. 工单日期落在 `dim_snowfall_event` 的 `[start_date, end_date]` 内
3. `type` 属于 6 个有 `winter_category` 的冬季类别

- **切法**：按事件的日历年分年，含 DML 里那个 **45 天溢出窗口**
  （事件可能跨年）。
- **comparator**：`pct_change` 容差，**不是等值**。Open-Meteo 回修历史存档会
  重切事件边界（第二批 §4.1 记的漂移机制），等值必然过期然后被静音。
- **锚点**：F1 = 13,068 行 / 非零格 908（排班期）。

### 2.3 🔴 `XLAYER-SILVER-GOLD-F8-ROLLUP`（周频）—— 这条不是等式

细化时核 DML 发现的:`fact_winter_request_daily_by_label` 最后一步是
**`INNER JOIN dim_admin_label`**（`sql/dml/…:75`）。**维表里没有的标签被静默丢掉。**

所以「Silver 带标签的行数 == F8 之和」是**假的**，正确形式是两条规则：

| 规则 | 形式 | 意思 |
|---|---|---|
| `XLAYER-…-F8-ROLLUP` | Silver 带标签计数 **>=** F8 之和 | 方向恒定；反过来就是 F8 凭空多了行 |
| `XLAYER-…-F8-UNKNOWN-LABEL` | 落不进维表的标签数 **== 0** | **丢掉的那部分本身就是一条 DQ 发现** |

第二条才是真正有价值的那条:上游冒出一个没人见过的 ward 名,F8 会**安静地少统计一批工单**,现在这套里没有任何东西会说话。

- 两条都按 `open_date_local` 分年，与 F8 的 DML 同一套切法。
- 🔴 **ward 与 neighbourhood 分开对，不合并成总数**：一条工单同时带两个标签
  会产生两行，合并之后「扇出」和「重复」就分不开了。
- **锚点**：F8 = 141,377 行；冬季子集 256,077 行 / 2.05%；无标签 77.22%。

### 2.4 `XLAYER-GOLD-INTERNAL-F5-F6-F7`（日频，便宜）

评分链三张表的行数关系,只用 `COUNT(*)`，走现有的 `sql` 类型即可：

- F6 = 1,298 = 374 scored + 924 partial_no_rank
- F7 = 374 × 版本数（实测 748）
- F5 = 1,298 × 版本数（实测 2,596）

🔴 **写成动态关系而不是常数**，`COUNT(DISTINCT model_version)` 参与运算。
L3 已经证明过：没有它，「purge 吃掉一个版本」和「本来只训过一个」长得一样。

---

## 3. 计分卡

`scripts/dq/scorecard.py` 从 `dq_audit_log` 读，**不重新跑任何检查**。

按 `dimension` 分六维（`structural` / `statistical` / `business` /
`cross_layer`——实际是四类，六维是 §3 的说法，落表是四个值）汇总：

| 输出 | 定义 |
|---|---|
| 通过率 | 该维 `error` 级检查里 pass 的比例 |
| 趋势 | 与上一次同 `cadence` 的运行比 |
| 连续失败天数 | 同一 `rule_id` 连红几天——**这是「慢性劣化」唯一能被看见的形式** |

🔴 **计分卡是读取者，不是第二个真相来源。** 它不落新表，输出到
stdout + Discord。落表的诱惑要抵住：`dq_audit_log` 已经是唯一来源，
再存一份汇总就有了两个可能不一致的数。

🟡 **趋势要攒够点**：`dq_audit_log` 目前只有几趟运行的数据，
连续失败天数在跑满一周之前恒为 0 或 1。**这不是缺陷，但别把「全绿」
读成「趋势检查生效了」**（第二批 §6.2 已记，这里适用第二次）。

---

## 4. `gold_certification`

一次审计运行一行（不是一张表一行——认证的对象是**这一批 Gold 整体**）。

| 列 | 说明 |
|---|---|
| `run_id` | 对应 `dq_audit_log.run_id`，**外键语义**，让人能从结论走回证据 |
| `certified_at` | 时间戳 |
| `status` | `certified` / `suspect` / **`unknown`** |
| `error_count` / `warn_count` | 当次的 finding 数 |
| `checks_total` / `checks_could_not_run` | 分母,以及「有多少条没能问出答案」 |
| `note` | 人可读的一句话 |

**状态机就三条**：

- 所有 `error` 级检查 pass 且 `could_not_run = 0` → **`certified`**
- 有任何 `error` 级 finding → **`suspect`**
- 有任何检查 could-not-run → **`unknown`**（§0.3 第 2 条）

🔴 **`unknown` 不是 `suspect` 的同义词**：`suspect` 是「我们查了，数据有问题」，
`unknown` 是「我们没能查」。合并这两个状态，就会在审计自己坏掉的那天
给出一个看起来像结论的结论。

🔴 **打标不阻塞任何东西**。`certify` 任务写完表就结束，`suspect` 也不 raise
（与 finding 同理）。它改变的是**下游读到什么**，不是管道跑不跑。

---

## 5. DAG 编排

**不新建 DAG。** 在 `dag_dq_audit` 里串三个任务：

```
run_dq_audit  →  scorecard  →  certify
```

理由与第二批把 DQ 与 Bronze 分开是同一条：**这三步共享同一个 `run_id`**，
拆成两个 DAG 就要在表里靠时间戳猜哪次审计对应哪次认证。同一个 DAG 里
`run_id` 走 XCom，是确定的。

- `scorecard` 与 `certify` 的 `trigger_rule` 必须是 **`all_done` 而不是
  `all_success`**：`run_dq_audit` 在「检查跑不起来」时会 raise，而那正是
  需要写一行 `unknown` 的情况。用 `all_success` 会让审计坏掉的那天
  **一行认证记录都没有**，退化成 §0.3 第 2 条要避免的默认值。
- 三个任务都不写 `on_failure_callback`（`DEFAULT_ARGS` 已有）。

---

## 6. 验收判据

| # | 判据 | 期望 |
|---|---|---|
| **W1** | 四条对账规则跑通，全部有确定结论 | could-not-run **0** |
| **W2** | Bronze→Silver 守恒在滚动窗口上成立 | 差值 0（除最近 `late_arrival_days` 天） |
| **W3** | F1 / F8 两条 roll-up 的口径差异被解释掉 | 与 §2.2/§2.3 写的过滤条件一致，差值在容差内 |
| **W4** | 连跑两趟结论逐条相同 | 除 `run_id`/时间戳外全同 |
| **W5** | 正常一天写出 `certified` | `gold_certification` 一行，`status='certified'` |
| **W6** | 🔴 **故障注入两次**：造一条 `error` finding → `suspect`；让一条检查跑不起来 → `unknown` | 两种状态都真的出现过，且**任务都不红** |
| **W7** | `uoip_meta` 隔离仍然成立 | 三处遍历仍是 **17** 张表（新加一张表，这条要重验） |
| **W8** | `make lint` + `make test-unit-offline` + `make test-dags` | 全绿 |

🔴 **W6 是本篇的 V3**：第三批新增的两个状态里，`unknown` 那条路径
**只有故障注入能证明**，正常运行永远走不到。第二批的教训是注入验证自身
有时序坑（launch §4.4），照那节的顺序做。

---

## 7. 待定项

| # | 问题 | 倾向 |
|---|---|---|
| **W-O1** | F1/F8 的 roll-up 分年切之后耗时多少 | 未测。L2 实测两张 19 分片的表各占 400–600 秒，对账只读不写应更快，但**要实测再定 cadence**，超过 10 分钟就从 `weekly` 降到 `manual` |
| **W-O2** | Bronze manifest 读取要不要缓存 | 倾向不缓存。14 天窗口 × 几个源 = 几十个 manifest，便宜；全量扫（`manual`）才是几千个，那时再说 |
| **W-O3** | 计分卡的 Discord 消息要不要每天发 | 倾向**只在有 finding 或状态变化时发**。天天发一条全绿汇总，两周后就没人看了——这与「红着的 DAG 是被静音的 DAG」是同一个失效模式 |
| **W-O4** | `gold_certification` 要不要给 Superset 建视图 | 划在本篇之外（§1「不做的」），但**建表时把列名定成看板直接可用的形状**，免得第四批改列 |

---

## 8. 代码级改动清单（细化到可执行）

一行一处，写代码时照着走。**不新增也不修改任何 Silver/Gold schema** ——
契约自 2026-08-13 起冻结，本批只加 `uoip_meta` 里的一张表。

| 文件 | 改动 | 关键约束 |
|---|---|---|
| `sql/meta/gold_certification.sql` | **新建** | 追加型；`external_location` 用 `meta/gold_certification/`；照 `dq_audit_log` 的头注写清「不是数据产品、不进 `sql/ddl/`」 |
| `scripts/dq/rules.py` | `CHECK_TYPES` += `bronze_manifest_sum` · `chunked_sql` | 加载期校验：`chunked_sql` 必须有 `chunk_by`/`chunk_range`/`combine`/`sql`，且 `combine` 只许 `additive`；`chunk_by` 只许 `calendar_year` |
| 同上 | `dimension: cross_layer` 的加载期禁令 | 对账规则的 `check.type` 只许这三种，写成 `sql` 之外的普通检查直接拒 |
| `scripts/dq/run_audit.py` | 两个新分支 | `bronze_manifest_sum` 走 boto3（复用 `ingestion/loaders/s3_client.py`，**不新写客户端**）；`chunked_sql` 循环发 N 条，两列各自累加 |
| 同上 | `Context` 加 `bucket` / `late_arrival_days` | 前者 boto3 要用，后者 §2.1 的容差 |
| `scripts/dq/scorecard.py` | **新建** + `make dq-scorecard` | 只读 `dq_audit_log`，**不落新表**（§3） |
| `scripts/dq/certify.py` | **新建** + `make dq-certify` | 三态状态机（§4）；写 `gold_certification` |
| `config/dq/rules.yaml` | +5 条规则 | §2.1 · §2.2 · §2.3（两条）· §2.4。每条 `note` 必须复述它的过滤条件 |
| `dags/dag_dq_audit.py` | 串两个下游任务 | `trigger_rule="all_done"`（§5）；`run_id` 走 XCom；**不写 `on_failure_callback`** |
| `tests/unit/test_dq_*.py` | 扩充 | 见下 |
| `Makefile` | `dq-scorecard` · `dq-certify` | 与 `dq-audit` 同形状 |

### 8.1 必须有的单测（每条都对应一个具体的失效模式）

| 测什么 | 防的是 |
|---|---|
| `chunked_sql` 的 `combine` 只接受 `additive` | R3：比值分块平均会返回一个**貌似合理的错数** |
| F1/F8 的规则 SQL 与对应 DML 的过滤子句**逐字相同**（字符串比对） | 第二批 §4.1 那类「规则跑得动但问错问题」——它不产生噪音，所以最难发现 |
| F8 的 roll-up comparator 是 `>=` 而**不是** `==` | §2.3 的 `INNER JOIN dim_admin_label` |
| 三态状态机：could-not-run → `unknown`，不是 `suspect` | §0.3 第 2 条 |
| `certify` 在 `run_dq_audit` raise 之后仍会执行 | `trigger_rule` 写成 `all_success` 就没有 `unknown` 记录了 |
| `uoip_meta` 新表对三处 Gold 遍历不可见 | 第二批 V5，**新增一张表就要重验一次** |
| 每条 `cross_layer` 规则的 `note` 非空且含过滤条件关键词 | §0.2：读日志的人要能分清「数据坏了」和「口径不同」 |

🔴 **有一条测不了、必须靠生产跑**：`bronze_manifest_sum` 读的是真实 MinIO，
单测只能 mock。所以 W2 是**只有阶段 D 能给的判据**——与第二批
「`get_bucket()` 漏传 `params` 单测抓不到」是同一类，别把绿色单测读成跑通。
