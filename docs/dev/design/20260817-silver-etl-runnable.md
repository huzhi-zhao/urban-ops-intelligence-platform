# Silver 全链路跑通（L1）

> **Status**: Draft · **Date**: 2026-08-17
>
> **上游需求**: [20260817-etl-implementation.md](20260817-etl-implementation.md)
> —— 25 张表填满的总计划（E0–E6）。本篇是它的**第一次上线**：E2 + 调度 + 全量回填。
> **同一需求的另两次上线**: [L2 Gold 维表与事实表](20260817-gold-dimensional-build.md) ·
> [L3 评分链与 M1](20260817-scoring-chain-and-m1.md)
> **前置上线记录**: [20260814 篇](../launch/20260814-table-creation-deployment-launch.md)（建表 + C6/C7 定案）·
> [20260817 篇](../launch/20260817-etl-implementation-launch.md)（E0/E1 实测，命中率 99.996%）
> **相关 ADR**: [0004](../adr/0004-silver-cleansing-methodology.md)（清洗方法论）·
> [0005](../adr/0005-silver-execution-architecture.md)（Silver 执行架构）·
> [0009](../adr/0009-plow-zone-as-the-unit-of-analysis.md)（作业分区是分析单元）
>
> **不新增也不修改任何 schema。** contract 自 2026-08-13 冻结。

---

## 1. 问题

上一次上线（2026-08-14）把 25 张表建进 Trino 并做了烟测,**明确不含 ETL**。
紧接着的 E0/E1 实测（2026-08-17）跑通了四个小 Silver job。现在的位置是:

| 层 | 状态 |
|---|---|
| Bronze | ✅ 全量落地。311 共 **18.4 M 行 / ~16 GB**,2008-06-17 起 |
| Silver 小表 | ✅ 有真实数据:`plow_shift` 418 · `parking_ban` 49 · `plow_zone_boundary` 82 · `snow_clearing_address` 25 · `weather_archive` + `snowfall_event` |
| **Silver 大表** | 🔴 `silver_service_request` **零行,无生产者** |
| Gold | 🔴 17 张表零行（L2/L3） |
| 调度 | 🔴 Silver 侧两个 DAG 未建。失败告警**代码已在**（`ba43372`,`DEFAULT_ARGS` 一处覆盖全部 DAG）,欠一次端到端验证 |

`silver_service_request` 是关键路径的起点:下游 `fact_service_request_zone_event`
（13,068 行训练面板）是 M1 的唯一输入,而它只能从这张表来。

**为什么 L1 只做 Silver。** 上线粒度应当等于**回滚粒度**:全量回填 4,876 天 / 16 GB
要跑数小时,跑完就是既成事实,出错只能重跑;Gold 是 `INSERT OVERWRITE PARTITION`,
分钟级可反复重建。两种性质的东西放进一次上线,上线记录里「能不能回滚」就没有统一答案。
被否决的合并方案见 §4。

### 1.1 剖析结论（design README 附表的五问）

字段与填充率**全部取自 `contracts/api-contracts/winnipeg-311.yaml` 的实测值**:

| 问题 | 结论 | 决定了什么 |
|---|---|---|
| 主键?是否唯一? | `(case_id, interaction_id)`。`case_id` 单独**不唯一**（1.87% 重复）,行粒度是 interaction 不是 case | 断言键;**不 dedup**（§3.4） |
| 时间字段格式 / 时区? | `open_date` / `closed_date` 都是 Socrata **floating timestamp**（无 offset、无 `Z`,本地 `America/Winnipeg` 墙上时间） | `localize_naive_to_utc` + 分区键取**本地日** |
| 高 NULL 率字段? | `geometry` / `ward` / `neighbourhood` 全表填充率 **0.209**（冬季子集 0.801）;`closed_date` 0.965 | 告警**分母 = `has_geo` 子集**;`closed_ts_utc` 允许 NULL |
| 低基数字段? | `channel_type` 15 · `reason` 20 · `case_status` 3 · `subject` 3;`type` **3,563**（高基数,真正的分析维度） | Gold 聚合维度;`type` 走 L2 的 `dim_service_type` |
| 与其他源怎么关联? | 空间经 `plow_zone`（点在多边形内）;时间经 `open_date_local` 对 `silver_snowfall_event` | Gold 三键事实表 |

一条不在表里但同样是硬事实的:`reason` 是**受理部门**不是投诉原因,
按它 group by 回答的是「谁处理的」,不是 BO-1..BO-6 问的问题。Silver 原样保留。

---

## 2. 范围与边界

### 2.1 本次上线包含

1. `spark/jobs/etl_service_request.py` —— 唯一的新 job。
2. `dags/dag_silver_service_request.py`（增量）+ `dags/dag_backfill_silver_service_request.py`（手动）。
3. `scripts/backfill/plan_silver_service_request.sh` —— 全量回填的切片计划。
4. **DAG 失败告警通路的端到端验证** —— 见 §3.6。代码 `ba43372` 已落地并对
   全部 DAG 生效,本次只欠触发一次 `dag_smoke_alert` 确认 Discord 真收到。
5. **单季（2024-11 → 2025-04）+ 全量回填实跑**。
6. 收掉 E0 遗留:清 `silver/snowfall_events/`（复数）旧前缀
   （[20260817 launch §4.1](../launch/20260817-etl-implementation-launch.md)）。

### 2.2 明确不包含

| 不做 | 去处 |
|---|---|
| 任何 Gold 表、`sql/dml/`、`config/seeds/` 种子 | L2 |
| 评分链 `sql/intelligence/`、M1 训练 | L3 |
| Gold 侧调度入口（17 张表目前没有任何自动化触发方式,是 L2 的一块未设计工作） | L2 |
| DQ 基线与持续监控阈值 | E6（需要真实数据分布,只能后验） |
| 改 schema / contract / DDL | 冻结,走变更流程 |
| 写 Bronze | Bronze 已全量落地;`snapshot` 分区漏采不可恢复 |
| Iceberg、Superset 看板、Grafana | 不占关键路径 |

### 2.3 数据质量检测在本篇的位置

三类东西时点不同,**只有第三类能等**:

| | 落在哪 | 本篇 |
|---|---|---|
| ① 结构性断言（PK 唯一、行数、取值域、非空） | **job 代码里**,跑不过就抛 | §3.4、§3.6 |
| ② 上线门禁（这次上线算不算成功） | **launch doc 逐条贴真实输出** | §5 |
| ③ 持续监控阈值（漂移多少算异常） | E6 的 DQ 基线,要真实分布 | 不做 |

一条**必须现在定、拖到 E6 就废了**的口径:**告警分母 = `has_geo` 子集,不是行数**。
上游 79% 的行没有坐标,全表口径的阈值会永远报警然后被静音。已经用
`zone_assignment.spatial_hit_rate()` 把分子分母固定成一个函数返回,
就是为了不让任何 job 自己算比值。同类的还有 `geo_match_status` 三值不塌成 NULL。
这类口径定错了之后再改,**历史数据已经按错的口径落盘了**,所以它跟 schema 一样属于前置。

---

## 3. 方案

一个 job + 两个 DAG + 一个 plan 脚本 + 告警通路 + 一个单测模块。
**不新增 transform 模块**:`zone_assignment` / `timestamp_normalizer` /
`reference_table` 的断言（`enforce_schema` / `assert_unique` / `split_by_validity`）
E0/E1 已建齐并在真实数据上验过,复用。

### 3.0 E2 的真实未知量

E0/E1 实测把一个风险划掉了:`zone_assignment` 在真实的 82 个多边形（含 8 个
`make_valid` 修复过的）上跑 237,867 个地址点,命中 237,858 —— **99.996%**,未匹配只有 9 条。
几何修复逻辑在真实数据上是可靠的。

所以 E2 剩下的未知量**不是空间归属正确性,而是 1,840 万行规模下的执行时间与内存表现**。
本篇的设计重点因此全部压在:量级、切片、失败模式。

### 3.1 job 骨架与七步失败模式

`spark/jobs/etl_service_request.py`,`--bucket --start --end`（`[start, end)`）,
签名与 `etl_weather_archive.py` 对齐。

它是本仓库**第一个同时具备**「日期窗口 + Python UDF + 大数据量」三者的 job ——
已有的 6 个里 `etl_weather_archive` 有窗口无 UDF,`etl_snow_clearing_address` 有 UDF 无窗口。
两个骨架都不能直接照抄到这个量级。

| # | 步骤 | 不加防护会怎样 |
|---|---|---|
| 1 | 读 Bronze,**显式传 `SERVICE_REQUEST_RAW_SCHEMA`** | 无 schema 的 `read.json` 静默推断:`interaction_id` 是「看起来是数字的字符串」,某些月份会被推成 long,跨月 union 时类型冲突或精度损失。AGENTS.md 禁止项 |
| 2 | 按**月前缀**读,`pathGlobFilter` 只取 `data_*.ndjson.gz`,读后按 `[start, end)` 裁剪 | §3.2 —— 已付过学费的决定 |
| 3 | `open_ts_utc` = `localize_naive_to_utc(open_date, "America/Winnipeg")`;`open_date_local` = **本地日** | 按 UTC 日分区会把本地傍晚 18:00 后的工单挪到次日,每日聚合系统性偏移 |
| 4 | 空间归属:broadcast 82 个多边形,`assign_zone` 输出三值 | §3.3 |
| 5 | `type` / `channel_raw` / `ward_raw` / `neighbourhood_raw` **原样** | 在 Silver 做 casefold 或渠道归一化＝业务语义前移,破 Silver 五条原则 |
| 6 | PK **断言**,不 `dropDuplicates` | §3.4 |
| 7 | `repartition(N, "open_date_local")` → `partitionBy` → `partitionOverwriteMode=dynamic` + `mode("overwrite")`;拒绝行进 `silver/_rejects/service_request/` | 🔴 `mode("overwrite")` 不配 dynamic 会**删掉整表**再写这一个窗口 —— 十年数据被一个增量窗口清空,且不报错 |

C7 定案:`N = clamp(窗口天数, 1, 64)`,**每个日分区恰好 1 个文件**。

### 3.2 为什么按月前缀读而不是逐日枚举

Spark 在读之前对交给 reader 的每个路径调 `exists()`
（`DataSource.checkAndGlobPathIfNecessary`）。逐日枚举 4,876 天 ＝ 开跑前 ~4,876 次 HEAD。
两个后果:s3a 把 403 归类为**不可重试**,这一串里任何一次瞬时故障都会整 job 失败
（2026-08-16 的 Cloudflare HEAD-rewrite 事故就是这么暴露的）;且 Bronze 里**真缺**一天
会让整个窗口读不了 —— 2008–2016 只有冬季有数据,逐日枚举在这一段必然每次都炸。

按月读同一窗口约 220 个路径,两个失败模式一起消失。
`etl_weather_archive._bronze_month_prefixes()` 已是这个实现,抽公共还是各留一份见 §6 O2。

### 3.3 空间归属:先过滤再 UDF

`assign_zone` 对 lon/lat 皆 NULL 的行返回 `no_geo`,逻辑上无需前置过滤。但 **79%**
的行落在这一支,仍要付 UDF 的 JVM↔Python 序列化成本。做法:

- `add_point_coordinates` 之后按坐标是否为 NULL **split 成两支**;
- 有坐标的一支走 `assign_zone`;无坐标的一支直接常量填三值,再 union;
- 两支的取值必须引用 `zone_assignment.MATCH_STATUS_*` 常量,**不得在 job 里写字符串字面量**
  —— 否则哪天 transform 改了拼写,这一支静默变成第四个取值。

⚠️ 这个优化**不得改 transform**:`assign_zone` 的 `no_geo` 分支要留着,
`etl_snow_clearing_address` 走的是不分流的路径,而 BO-4 的命中率只有两边同一实现才可比。
命中率一律经 `spatial_hit_rate()` 算,job 不自己算比值。

### 3.4 PK 断言而不是去重

`assert_unique(df, ("case_id", "interaction_id"), ...)`,重复即**失败**。

7 天回溯窗口天然重复拉取同一批天 —— 这由 C6 的「整天分区覆写」语义解决,不靠去重。
`dropDuplicates` 会在上游真出问题时**静静少一批行**,而 PK 破坏正是上游变更的信号,必须告警。

断言的**范围**是本篇要定的一件事:全量段一次断言 18.4 M 行的 distinct count 很贵。
定为**按窗口断言** —— 每个 job run 只对自己窗口内的行断言唯一。跨窗口的全表唯一性
留给 E6 的 DQ 基线一条 SQL,不进 job 热路径。判据:Bronze 的 `daily` 分区键就是
`open_date`,同一个 interaction 不会横跨两天的文件。这是省成本,不是放松。

### 3.5 调度与切片

- `dag_silver_service_request.py` —— `0 7 * * *`,7 天滑动回溯,`catchup=True`,
  起点 `INGEST_START_DATE`。DAG 只做调度,**窗口切片由 job 的 `[start, end)` 承担**
  （既有设计:1 DAG Run = 1 窗口）。
- `dag_backfill_silver_service_request.py` —— 手动、`schedule=None`、Params 驱动任意窗口。
- `plan_silver_service_request.sh` —— **照抄 Bronze 侧 `plan_wpg_311_backfill.sh` 的窗口划分**
  （8 个雪季 + 全量段按日历年切）,`source _plan_lib.sh` 复用 checkpoint / 告警 / watchdog。
  两侧窗口一致不是省事:Bronze 缺哪段 Silver 就在同一个窗口失败,对账只看一个窗口名。
  **切片粒度就是重跑粒度**;`${PYTHON:-python3}` 必须遵守（PEP 394）。

两个 DAG 都带 UDF,需要 `etl_plow_zone_boundary.py` 头注与 `_spark_common.py` 里记的
三个额外 `--conf`。**没有 UDF 的 job 不需要它们** —— E0/E1 实测踩过这个区别。

### 3.6 失败告警通路 —— ✅ 代码已在，L1 只剩验证

> **2026-08-17 更正**:本节初稿写的「DAG 侧什么都没有」在写下时就已过时。
> `ba43372` 已实现 [20260816 篇](20260816-failure-alerting-and-followups.md) 的批 1:

现状（已核对代码）:

- `dags/_alerts.py` —— `alert_on_failure`（Discord,`BACKFILL_ALERT_WEBHOOK_URL`
  回落 `SNAPSHOT_ALERT_WEBHOOK_URL`）+ `ping_watchdog`（`AIRFLOW_WATCHDOG_URL`,无回落）。
- 挂载点是 `_dag_common.DEFAULT_ARGS`,**一处生效、覆盖全部 DAG**,
  包括本篇两个还没写的 —— 所以本篇的两个新 DAG **不需要显式写
  `on_failure_callback`**,照常用 `DEFAULT_ARGS` 即可,显式写反而会覆盖掉它。
- `dags/dag_smoke_alert.py` —— 故意失败的手动 DAG,`retries=0`,不写任何数据。

因此 L1 在这一块的工作**不是实现,是执行一次端到端验证**:触发 `dag_smoke_alert`,
确认 Discord 真收到、内容含 `dag_id` / `task_id` / `run_id` / 日志链接
（launch 阶段 C5）。这条不能靠「看起来配好了」代替 —— 上一次静默 12 天,
恰恰是因为约定写了而能力不存在。**不引入新的告警系统。**

仍然欠的一条:死人开关**尚无 healthchecks.io check 注册**,`AIRFLOW_WATCHDOG_URL`
未设时 `ping_watchdog` 静默跳过（这是设计,见 `_alerts.py` 头注）。
注册与「停 scheduler 验证报警」不在 L1 关键路径上,记进 §6 O6。

### 3.7 单测边界

`tests/unit/test_etl_service_request.py`,离线（`make test-unit-offline` 必须能跑）:

- 时区:本地 `2024-01-15T23:30:00.000` → `open_date_local = 2024-01-15`（**不是 16**）;DST 边界各一例。
- 三值分流:有坐标命中 / 有坐标不命中 / 无坐标,断言 `plow_zone` 仅在 `matched` 时非 NULL。
- PK 断言会抛（构造一对重复行）。
- 窗口裁剪:月前缀读进来的邻月行被 `[start, end)` 剔掉。
- `N = clamp(days, 1, 64)` 的三个边界（1 / 30 / 400 天）。

不测的:真实 MinIO 读写、真实 82 个多边形、行数量级 —— 那是 §5 的生产门禁。

---

## 4. 被否决的选项

| 选项 | 否决理由 |
|---|---|
| **Silver + Gold 一次上线** | 上线粒度应等于回滚粒度。全量回填跑完是既成事实、只能重跑;Gold 分钟级可反复重建。混在一起,launch 记录里「能不能回滚」没有统一答案。且 Gold 的最大不确定量是 `dim_service_type` 3,563 个取值的**人工过审**,把它压在关键路径上会拖住回填 |
| 逐日枚举 Bronze 路径 | §3.2:4,876 次前置 HEAD,s3a 视 403 不可重试;2008–2016 只有冬季,缺天让整窗口读不了 |
| 不传 schema,靠 `read.json` 推断 | AGENTS.md 禁止项。`interaction_id` 的推断结果随月份数据而变 |
| `dropDuplicates(("case_id","interaction_id"))` | 静静少行;PK 破坏是上游信号,必须报警 |
| 只按 `case_id` 去重 | 行粒度是 interaction,1.87% 重复是**真实的多次交互**,删掉就是删数据 |
| 分区键用 UTC 日 | 本地傍晚工单被挪到次日,每日面板系统性偏移 |
| 改月分区以缓解小文件 | 要改分区列 + 重建表,且让 7 天回溯的增量每天重写整月。C7 已否决 |
| 一次提交十年 | 7 GB 内存 OOM;切片粒度＝重跑粒度,一个大窗口失败等于全部重来 |
| 给 `assign_zone` 加「跳过无坐标」开关 | 两个 job 必须共用同一实现,否则 BO-4 命中率不可比。分流放 job 侧（§3.3） |
| 在 Silver 做 casefold / 渠道归一化 / `type` 解析 | 业务语义一律后移到 Gold。这条原则已扛过一次分析单元变更,不为省一步破例 |
| 把 `reason` 当分析维度 | 它是受理部门。分析 grain 是 `type` |
| 为 L1 单独引入一套 DQ 框架 | ① 已在 job 代码里、② 在 launch 门禁里、③ 需要真实分布只能后验。多一套框架不产生任何现在就能跑的判据 |
| 告警接新系统（Grafana / Alertmanager） | Grafana 未部署,且回填侧的 Discord webhook 已在用。L1 的需求是「跑挂了有人知道」,不是可观测性平台 |

---

## 5. 验收判据

### 5.1 前置门禁（开跑前必须为真,否则结果无效）

```sql
-- 边界表有真实数据。空表会让 assign_zone 直接抛(这是设计,不是故障)
SELECT COUNT(*) FROM hive.uoip_silver.silver_plow_zone_boundary;  -- 82(多边形数)
SELECT COUNT(DISTINCT plow_zone) FROM hive.uoip_silver.silver_plow_zone_boundary;  -- 25
```

```bash
make lint && make test-unit-offline
```

### 5.2 单季门禁（2024-11-01 → 2025-04-01,不通不进全量）

```sql
-- 复合键无重复
SELECT COUNT(*) - COUNT(DISTINCT (case_id, interaction_id))
FROM hive.uoip_silver.silver_service_request;                      -- 0

-- 三值齐全,无第四个取值、无 NULL
SELECT geo_match_status, COUNT(*) FROM hive.uoip_silver.silver_service_request
GROUP BY geo_match_status;          -- 恰好 matched / unmatched / no_geo

-- plow_zone 仅在 matched 时非空
SELECT COUNT(*) FROM hive.uoip_silver.silver_service_request
WHERE (geo_match_status = 'matched') <> (plow_zone IS NOT NULL);   -- 0

-- 空间命中率:分母是 has_geo 子集,不是行数
SELECT COUNT_IF(geo_match_status='matched') * 1.0 / COUNT_IF(has_geo)
FROM hive.uoip_silver.silver_service_request;                      -- >= 0.999
```

```bash
# 每个日分区恰好 1 个文件(C7),分区数 == 窗口天数
aws --endpoint-url "$S3_ENDPOINT_URL" s3 ls --recursive \
  "s3://$S3_BUCKET_NAME/silver/service_request/"
```

- **幂等**:同一窗口连跑两次,分区行数与对象校验和一致,且未触及窗口外的分区。
- **实测日分区对象大小**,与 0.3–0.5 MB 的估计对账,数字进 launch（伞篇 O5）。
- **告警通路**:人为让一次 DAG run 失败,确认 Discord 收到消息。

### 5.3 全量门禁

- 分区数 == Bronze **实际有数据的天数**（2008–2016 只有冬季,不按日历天数）。
- 全表 `COUNT(*)` 与 **Bronze 实测行数**的差额**必须能被
  `silver/_rejects/service_request/` 的行数解释**,对不上就是丢行。
- PK 全表唯一。**必须按年切**（分区裁剪），全表 `GROUP BY` 会打爆 Trino 的
  每节点内存上限。
- 空间归属三值齐全（matched / unmatched / no_geo，无第四值、无 NULL），
  且命中率与探针 **99.9%** 同量级。
  ⚠️ **不能在全表上精确复现 134,123 / 134,258** —— 那两个数的分母是
  「排班期 × 冬季 × 带几何」的工单，筛选条件在 Silver 层不存在（需要 L2 的
  `dim_service_type` 与排班表）。精确复现已移交 L2。真对不上时仍然信探针。

> 🔴 **两条判据在 2026-08-18 的首次全量执行中被证伪，已修正**（实测见 launch §3.2）：
>
> 1. **分母不是 18.4 M。** 契约的 `full_table_min` 与 CLAUDE.md 的「18.4 M 行」
>    指**上游整表**，而 Bronze 采集范围有意不是全历史（2016-08-01 起全天，
>    之前只采冬季）。实测 Bronze = Silver = 12,474,313。拿 18.4 M 来对会看到
>    590 万行的假缺口。
> 2. **「冬季子集 ≈ 275,282」这条判据在 L1 无法执行，已移交 L2。**
>    契约里的 winter subset 是按 `type` 关键词匹配的子集（≈1.5% 全表），
>    不是 11–3 月的日历子集；且 `winter_category` 由 Gold 的 `dim_service_type`
>    解析，**Silver 没有这一列**。L1 只记日历口径的规模基线，不作判据。

### 5.4 遗留项收口

```sql
-- 新前缀有数据
SELECT COUNT(*) FROM hive.uoip_silver.silver_snowfall_event;  -- 99(当前定义)
```
然后才删 `s3a://uoip/silver/snowfall_events/`（复数）。⛔ 只删这一个前缀。

---

## 6. 开放项

| # | 未定的事 | 建议 | 时点 |
|---|---|---|---|
| **O1** | 拒绝行的判定范围。目前只想到「`open_date` 不可解析」「PK 字段 NULL」两类;`type` / `channel_type` 契约写 `nullable: false`,真为 NULL 时拒绝还是抛? | 拒绝（进 `_rejects` 并计数）,**但计数 > 0 就在日志里报**。契约说不该有,真有了是上游变更 | 编码时 |
| **O2** | `_bronze_month_prefixes()` 现在是 `etl_weather_archive.py` 的私有函数,E2 要第二份 | 复制一份先跑通,**单季门禁过后**再抽到 `spark/transforms/`（按能力命名）。现在抽会同时动一个已在生产跑的 job | 单季之后、全量之前 |
| **O3** | 全量段的窗口并发度 | 串行。7 GB 内存下并发的收益不确定,而 OOM 浪费的是数小时 | 全量开跑前 |
| **O4** | `etl_weather_archive --emit-events` 全量重跑的**历史起点**(Bronze 实际最早一天,还是 BO-3 的约定窗口) | 取 Bronze 实际最早一天,并把该日期记进 launch —— 事件表的口径不能是「当时随手填的」 | §5.4 执行前 |
| **O5** | `closed_ts_utc` 3.5% 缺失 + C8 语义未验证（case 关闭 ≠ 实际清除） | Silver 原样落,**不据它派生任何 M2 特征**。BO-5 是 P1,H1 不阻塞 | H1 之后 |
| **O6** | 死人开关未注册:`AIRFLOW_WATCHDOG_URL` 空,`ping_watchdog` 静默跳过。「scheduler 挂了没人知道」这一半还没闭合 | 注册一个 healthchecks.io check 并停 scheduler 验证一次。**不占 L1 关键路径**——`on_failure_callback` 已覆盖「跑了但失败」,回填侧另有自己的 watchdog | L1 之后、增量 DAG 长期开着之前 |

---

## 7. 时间盒

| 天 | 做什么 | 出口 |
|---|---|---|
| 8/18 | job + 单测（离线）| `make lint && make test-unit-offline` 绿 |
| 8/19 | 两个 DAG + plan 脚本（告警无需开发,见 §3.6）| DAG import 测试绿 |
| 8/20–8/21 | 单季跑通 + 收 §5.4 遗留 | §5.2 全过 + O5 实测数字 |
| 8/22–8/23 | 缓冲（单季返工在这里吃掉）| — |
| 8/24–8/26 | 全量回填,plan 脚本切片、checkpoint 可续跑 | §5.3 全过 |

关键路径上,**L1 拖一天 L2/L3 整体后移一天**。8/22–8/23 的缓冲是给单季返工的,
不是给「顺手把 O2 抽出来」这类事的。
