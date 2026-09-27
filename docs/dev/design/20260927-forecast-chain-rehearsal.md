# 前瞻链路的合成演练（H2-R12）

> **Status**: Draft · **Date**: 2026-09-27
>
> **需求**：[伞篇](../requirements/20260921-h2-model-and-scoring-improvements.md) **R12**
> （判据：**11 月前跑通**），它是 **R11** 的前置。
> 本篇只写怎么落地；为什么要做只留结论，见伞篇 D 组。
>
> **范围**：把「预报 → 日粒度 → 事件 → M1 推断 → 输出」整条链路建出来，
> 并用构造的预报跑通。**不含** R11 的评估（要等真实冬季预报），
> **不含** R3 的模型行为检查与 R13 的区间（两者都是模型线，另行设计）。
>
> ⚠️ 不改任何契约、DDL、Gold 表、门禁或 DQ 规则。

---

## 0. 动工前的三个实测发现

这三条都是读代码、查生产时发现的，**每一条都改变了 R12 的形状**。

### 0.1 🔴 真实预报从来没有被采集过

伞篇 R11 与 CLAUDE.md 都写着 `weather_forecast` 自 2026-08-02 起按 `snapshot` 每日采集。
**这件事不成立。** 2026-09-27 经 `ssh oci-4c24g` 只读核对：

| 前缀 | 结果 |
|---|---|
| `bronze/raw/SRC-Open-Meteo/` | 只有 `weather_archive/` |
| `bronze/raw/SRC-Open-Meteo/weather_forecast/` | **0 个对象** |
| `silver/weather_forecast/` | **0 个对象** |
| `bronze/raw/SRC-WPG-SNOW/` | 正常（对照：存储节点的采集器本身在跑） |

成因有两层，**任意一层单独存在都足以让它采不到**：

1. **部署层**：存储节点的 systemd 单元只写了 `--source SRC-WPG-SNOW`
   （[snapshot-collection.md](../../guide/snapshot-collection.md) 的
   `ExecStart`），IAM 策略也只放行 `bronze/raw/SRC-WPG-SNOW/*`。
2. **代码层**：即使加上 `--source SRC-Open-Meteo` 也会失败——
   [`ingestion/snapshot/fetch.py`](../../../ingestion/snapshot/fetch.py) 的
   `fetch_snapshot_records` 对 `api_type != socrata` 直接抛 `ValueError`；
   而且一次预报只有几百行（按当时的配置是 10 天 × 24 小时 = 240 行），
   低于采集器默认的 `min_records = 1000` 小样本保护，会被当成异常拒绝落盘。

**为什么八周没人发现**：`dag_audit_bronze` 对 snapshot 缺口**只写日志不失败**，
而且也不发通知（`gap_report` 只在 `fill_failures` 非空时才 raise，snapshot 缺口
永远进不了 `fill_failures`）。「finding 不 fail 任务」这条原则本身没错，
错在缺口没有第二条通知渠道，所以只能靠人读日志。这件事**不在本篇范围内**，另开一项处理。

**对 R11 的后果**：预报是覆盖式的，**已经过去的 8 周永远拿不回来**。
好在这 8 周是夏秋，不影响冬季评估。**但 11 月第一场雪之前必须开始采集**，
否则 R11 要再等一年。所以本篇把采集修复排成**第 0 批，优先级最高**。

### 0.2 🔴 Silver 的预报表回答不了「当时知道什么」

[`dedupe_by_freshness`](../../../spark/transforms/weather_forecast.py) 对同一小时
**只保留最新一次采集的值**。对「给 Silver 一张干净的逐小时表」来说这是对的，
但对前瞻评估来说是致命的：

- 事件过去之后，最新一次采集里的那几个小时来自 `past_days: 3`，
  已经是分析场、接近实况，**不是预报**。
- R11 明确要求「用当时真正可得的预报，不能拿事后值冒充」。
  从 Silver 读，就是在不知不觉中作弊。

**处置**：链路**直接读 Bronze 的单个 `ingest_date=` 分区**，一次只读一个发布日（vintage）。
这不是绕开 Silver，而是 Silver 的语义和这个问题不匹配。一次 240 行，
用 pandas 就够，不需要 Spark。Silver 表**不改**。

### 0.3 🟡 M1 的产物够用，不需要重训

[`train_m1.py`](../../../scripts/models/train_m1.py) 在 `metrics.json` 里存了全部
`coefficients`。log link 下推断就是 `exp(Xβ + log(unit_size))`，
不需要 statsmodels，也不需要重训。

这也让链路**对模型族无关**：R1 若换成负二项，均值的算法不变（同为 log link），
链路不用改。R13 的区间将来按副本接进来，同样不影响链路形状。

### 0.4 🔴 存储节点拉新代码会让 SNOW 采集一起挂掉（批 0 顺带修复）

在只装 `requirements-snapshot.txt` 的干净环境里验证批 0 时发现：
**`--source SRC-WPG-SNOW` 同样起不来**，报 `ModuleNotFoundError: dotenv`。
`88e9388` 让 `scripts/_env.py` 在顶层导入 `python-dotenv`，而该依赖文件故意不装它。

SNOW 采集至今正常（2026-08-02 → 09-26 共 56 个分区，无缺口），说明存储节点的
代码早于那个提交，或者它的 venv 碰巧装了 dotenv。无论哪种，**为批 0 在存储节点
拉新代码，都可能让一个每丢一天就永久丢失的源停掉**。已改为缺包时跳过：
systemd 经 `EnvironmentFile` 提供环境，存储节点本来就用不到 `.env`。
两个源在干净环境里的 dry-run 均已通过。

---

## 1. 链路总览

```
[存储节点] collect_snapshot --source SRC-Open-Meteo          ← 批 0
      ↓  bronze/raw/SRC-Open-Meteo/weather_forecast/ingest_date=D/data.ndjson.gz
[计算节点] outlook（纯 Python）                                ← 批 A
   1. 读 vintage D 的 Bronze（只读这一个发布日）
   2. 逐小时 → 本地日（降雪求和、最低温取最小）
   3. 前接实况：从 silver_weather_archive 取 D 之前的 N 天，供滚动累积判据用
   4. 用与生产相同的规则切事件（v1-3cm-or-10d10cm），只保留起点 ≥ D 的
   5. 组装 M1 特征（严重度用冻结的 99 事件边界、滞后特征用 F1 实际值）
   6. 用指定 model_version 的系数推断 22 个分区
   7. 写产物 + 外推标记
      ↓  gold/_outlook_runs/issue_date=D/{model_version}/
[演练] 构造的 payload 写到 smoke 前缀，跑同一条链路                ← 批 B
```

---

## 2. 约束

- **契约冻结**：不新建 Gold 表。输出是**产物**，写在
  `gold/_outlook_runs/`，与 F5 的 `gold/_forecast_runs/` 同样避开 Gold 表前缀，
  `build_gold` 的 purge 碰不到它。
- **产物只追加、不覆盖**：一个发布日的产物就是「那天我们说了什么」的唯一记录，
  将来 R11 用它来打分。同一 `issue_date × model_version` 已存在时**拒绝写**，
  这条规则和 snapshot 的 Bronze 相同。
- **城市无关护栏**：链路代码用角色名（单元 / 事件 / 单元规模）。时区、事件规则
  参数、列名映射一律放在 `config/`，沿用 `config/models/m1.yaml` 的做法。
- **显式版本**：`--model-version` 必填，不做自动选择。理由与 F6 相同
  （launch §4.6）：字典序会选中故意训坏的 `nomonth`。
- **合成数据绝不进生产 Bronze 路径**：往 `ingest_date=` 下写假预报等于伪造采集历史，
  而且无法事后分辨。演练只写 smoke 前缀，入口在前缀为空时拒绝运行。

---

## 3. 各步的口径

### 3.1 逐小时 → 日

| 日粒度字段 | 来源 | 口径 |
|---|---|---|
| `snowfall_sum_cm` | 逐小时 `snowfall` | 按本地日求和 |
| `temperature_2m_min_c` | 逐小时 `temperature_2m` | 按本地日取最小 |

- 本地日按 `config/sources/open_meteo.yaml` 的 `timezone` 取。API 返回的本来就是
  本地墙钟，所以这里**不做** UTC 转换，理由同 `normalize_archive_dates`。
- 只保留**完整的日**（24 个小时齐全）。预报窗口的最后一天常常不完整，
  把半天的降雪当成一天会系统性低估。
- 🟡 **已知的口径差，R12 解决不了**：训练用的是存档（再分析），推断用的是预报模式输出，
  两者的降雪量存在系统偏差。这正是 R11 要量的东西，R12 只把它写进产物说明。

### 3.2 事件切分

- 规则与生产完全相同：`v1-3cm-or-10d10cm`（单日 ≥ 3 cm，或 10 日滚动累积 ≥ 10 cm，
  `gap_days = 1`）。2026-09-27 实测 `dim_snowfall_event` 99 行全为此版本。
  🟡 版本串里**不含** `gap_days`：它取自 `etl_weather_archive` 的默认值 1，
  L1 launch 记录的生产参数与探针默认值也都是 1。配置里必须显式写出，不能靠版本串推断。
- **必须用 pandas 重写一份**，因为链路不跑 Spark。重写版有一条单测：同一份输入
  分别喂给 Spark 版 `segment_snowfall_events` 和 pandas 版，事件逐行一致。
  没有这条，两份实现迟早会漂移，而且漂移不会报错。
- 滚动累积要看**过去 10 天**，所以发布日之前的 9 天从 `silver_weather_archive` 取实况，
  接在预报前面。只保留**起点 ≥ 发布日**的事件，起点在过去的事件已经不是「前瞻」。
- 事件 id 用单独的命名空间 `OUTLOOK-{issue_date}-{start_date}`，
  避免和将来真实发生的 `SNOW-{start_date}` 撞名。
- 事件结束日落在预报窗口最后一天时标 `truncated_at_horizon = true`：
  雪可能还没下完，`total_snowfall_cm` 和 `duration_days` 是下界。

### 3.3 特征

| 特征 | 取法 | 外推标记 |
|---|---|---|
| `total_snowfall_cm` 等四个事件量 | 3.2 的事件聚合 | 超出 99 事件的训练范围时标记 |
| `accum_flag` | 同 Spark 版定义（峰值 < 单日阈值） | — |
| `severity_score` | **用冻结的边界**：取 `dim_snowfall_event` 99 事件的 min/max 归一化，不把新事件算进边界 | 结果落在 [0, 1] 之外时标记 |
| `season_index` | 新雪季的序号 = 训练里的最大值 + 1 | **每一次真实前瞻都会触发**，见下 |
| `month` | 事件起始日 | — |
| `prev_target` / `expanding_mean` | 该分区在 F1 中**最近的真实事件**的实际工单数 | — |
| `unit_size` | `dim_plow_zone.address_count` | — |

- 🔴 **`severity_score` 的边界不能现算**：dim 的 DML 按全部入选事件做 min-max，
  新事件一进来边界就变，同一场历史雪的严重度也跟着变。推断时必须用训练时的边界。
- 🟡 `season_index` 在新雪季**必然超出训练范围**。F5 的留出季也是这种情况
  （2025–26 同样没进训练），所以它不是新风险，但每次都要标出来。
  这一条和极端降雪的外推一样，都是 **R3 要检查的对象**。R12 只负责标记，不负责判断能不能信。
- 🟡 **滞后特征会过期**：冬季里真实事件发生后，要等 Gold 重建，F1 才会把它算进去。
  在那之前，`prev_target` 仍停留在上一场。R12 在产物里写明 `lag_asof`（滞后特征
  取自哪一场事件），是否要自动重建留给 R11 决定。

### 3.4 产物

`gold/_outlook_runs/issue_date=D/{model_version}/`：

- `outlook.csv`：每行一个 (事件, 单元)，列为 `outlook_event_id` · `unit_id` ·
  `predicted_count` · `predicted_per_1k` · 各项外推标记 · `truncated_at_horizon`。
  🔴 **不出区间**：R13 交付前，区间一律为空，**不用 Poisson 解析区间顶替**
  （伞篇 R13：过度离散下它系统性偏窄）。
- `run.json`：`issue_date` · `model_version` · 模型产物的 `panel_fingerprint` ·
  Bronze vintage 的 manifest 校验和 · 事件规则版本 · 严重度边界 · `lag_asof` ·
  链路代码的 git sha。

**没有事件时也要写产物**，内容为「0 个事件」。只要链路跑过，就必须留下记录。
「这天没预报出雪」和「这天链路没跑」长得不能一样。

---

## 4. 分批

每批一个 PR，批间停下等确认。

### 批 0 · 开始采集真实预报（最急，与其余各批无依赖）

**代码**
1. `fetch_snapshot_records` 增加 `open_meteo` 分支，复用
   `OpenMeteoFetcher` 的预报路径（按 YAML 的 `past_days` / `forecast_days` 取）。
2. 小样本保护的阈值改为按数据集配置：`config/sources/open_meteo.yaml` 的
   `weather_forecast` 加 `snapshot_min_records: 200`，缺省时仍为 1000。
   配在非 snapshot 数据集上直接报错，否则它看起来有保护、实际上没人读。
   不用 CLI 参数的原因：阈值属于数据集本身，写在 systemd 单元里会和配置分家。
3. dry-run 只拉 snapshot 数据集（原来会把同源的 daily 存档一起拉），
   并报告真跑能否过保护线、过不了就退出 2。
4. 单测：Open-Meteo 快照请求的是配置的相对窗口；456 行不被拒绝；Socrata 路径行为不变。

**部署（存储节点，写操作，由你执行或授权）**
1. IAM 策略增加 `bronze/raw/SRC-Open-Meteo/weather_forecast/*`。
2. 第二组 systemd 单元 `uoip-snapshot-forecast.{service,timer}`，
   `--source SRC-Open-Meteo`，死人开关单独一条 check（**不共用** SNOW 的，
   理由与 `BACKFILL_WATCHDOG_URL` 不回落相同）。
3. `--dry-run` 一次，再实跑一次，确认 `ingest_date=` 分区与 manifest 出现。
4. `snapshot-collection.md` 补第二个源的步骤。

步骤已写进 [snapshot-collection.md](../../guide/snapshot-collection.md)
「Collect the weather forecast」。

**验收**：连续 3 天每天出现一个 `ingest_date=` 分区，每个约 456 行；
`dag_audit_bronze` 对该数据集报 `AUDIT OK`。

### 批 A · 链路本体

`models/request_forecast/outlook.py`（纯函数，角色名）+
`scripts/models/outlook_m1.py`（入口，唯一允许出现城市列名的地方，同 `train_m1.py`）+
`config/models/outlook.yaml`（事件规则参数、时区引用、列名映射）。

**验收**
- pandas 版切分与 Spark 版逐行一致（单测，含跨窗口、滚动累积、隔一天仍算同一事件的 `gap_days = 1` 边界）；
- 用一个**真实的历史雪季**回放：把 SNOW-20251218 期间的存档当作「完美预报」喂进去，
  推断出的 22 个 `predicted_count` 与 F5 同版本同事件**逐位相同**。
  这是整条链路最有力的一条检查：特征组装错一处，数就对不上。
  🔴 这是**管道正确性**检查，不是预报准确性检查，不能对外表述为「前瞻验证」；
- 产物重复写被拒绝（单测）；
- `make lint` 与 `make test-unit-offline` 全绿。

### 批 B · 合成演练（R12 的判据本身）

`scripts/models/outlook_rehearsal.py`：生成与 Open-Meteo 响应**同形**的 Bronze payload，
写入 `smoke-r12/` 前缀，然后对每个场景调用批 A 的入口。

| 场景 | 构造 | 期望 |
|---|---|---|
| 无雪 | 全零降雪 | 产物写出，0 个事件 |
| 中雪 | 单日 8 cm | 1 个事件，22 行，无外推标记（`season_index` 除外） |
| 大雪 | 三日共 16 cm | 同上，量级接近 SNOW-20251218 |
| 极端 | 单日 40 cm（历史最大总量 29.05 cm） | 出数、不报错，**外推标记亮起** |
| 只靠累积 | 连续 5 天各 2.5 cm | 1 个事件，`accum_flag = true` |
| 到期截断 | 降雪持续到窗口最后一天 | `truncated_at_horizon = true` |
| 半天 | 最后一天只有 12 小时 | 该日被丢弃，不计入事件 |

**判据**：7 个场景在**生产计算节点**上全部跑通，产物齐全，期望逐条满足；
**截止 2026-10-31**。

🔴 **不检查「预测得准不准」**。合成的雪没有发生过，没有工单可对；拿模型自己的
输出当参照，就是模型自己考自己（伞篇 R3 已写明）。「极端」场景只检查链路不崩、
标记亮起，**不对 40 cm 下的数值做任何判断**，那是 R3 的事。

### 批 C · 调度（可选，R11 之前做）

`dag_outlook_request`：每天在存储节点采集之后运行，读当天的 vintage。
前提是批 0 已连续采集。是否要做、是否与 R11 合并，批 B 结束后再定。

---

## 5. 开放项

✅ 已定（2026-09-27）：**`forecast_days = 16`**（Open-Meteo 上限，在首次采集前定下，
每次约 456 行）· **批 0 的部署由你在存储节点执行**，步骤见批 0。

1. **情景输出（低 / 中 / 高）**：伞篇 R11 提到，但 R13 之前没有可信的区间来源。
   本篇只出中值，低 / 高留给 R13。另一个可选来源是同一场雪在相邻几个发布日之间的
   预报修订幅度，但那是 R11 的内容。
2. **滞后特征过期**（3.3）：R11 前决定。
