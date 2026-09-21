# ADR 0003 — Bronze 增量管道设计

> **Status**: Accepted · **Date**: 2026-06 · **Revised**: 2026-08-20
>
> 决策没变：增量 DAG 与回填复用同一套 `bulk.py` 函数，区别只在「窗口从哪来」——
> 回填从 Params 拿，增量从 `data_interval_start` 推算；一致性靠 `retries` +
> `catchup` + 每日 audit 自愈三层兜底。原文按 NYC 四源（311/NYPD/Open-Meteo/DCP）
> + GCP 执行者（Dataproc/BigQuery）描述，二者均已作废——NYC 实例已退役
> （CLAUDE.md「城市无关护栏」§3），GCP 组件已于 2026-07-30 全部放弃
> （[ADR 0006](0006-storage-compute-query-stack.md)）。这次重写换的是这两处，
> 三层兜底的决策本身不变，故不另开 ADR。

---

## 决策

增量摄取 DAG 与手动 backfill DAG 跑**同一套** `scripts/backfill/bulk.py` 函数，
不是两套平行实现。区别只有一处：

| | backfill DAG / CLI | ingest DAG（增量） |
|---|---|---|
| 触发方式 | 手动，Params 传 start/end | 定时自动 |
| 日期来源 | UI / CLI 输入 | `data_interval_start` 推算窗口 |
| 复用的代码 | `bulk.py` | 完全相同的 `bulk.py` |
| 幂等性 | ✅ | ✅（重跑同一 Run 结果一样） |

好处是 `bulk.py` 的窗口切片、重试、幂等写入只有一份实现要维护和测试；
坏处（也是接受的代价）见 [ADR 0002](0002-airflow-orchestration.md) 的「后果」一节——
两个入口意味着一件事能从两条路触发。

---

## 现状：三层各自的增量摄取

原文只讲 Bronze。当前 Bronze **和** Silver 都有 `catchup=True` 的每日调度 DAG，
Gold 没有——它是手动整表重建（[ADR 0002](0002-airflow-orchestration.md)、
`.claude/rules/gold-sql.md` R4），天天重建一张没变的表没有意义。

也不是原设计的四源四 DAG：NYC 四个源（`SRC-NYC-311` / `SRC-NYPD` /
`SRC-Open-Meteo` 的 NYC 用法 / `SRC-DCP`）随城市实例退役全部删除；
Winnipeg 只给**活跃、非 static**的源建 DAG（DAG 数量纪律，见
`.claude/rules/backfill.md`）。

**Bronze（2 个，`0 5/6 * * *`，`catchup=True`）**

| DAG | schedule | 窗口 |
|---|---|---|
| `dag_ingest_weather_archive.py` | `0 6 * * *` | 昨天（Open-Meteo archive 数据集） |
| `dag_ingest_service_requests.py` | `0 5 * * *` | 7 天回溯（`LOOKBACK_DAYS=7`），311 行会晚到晚改 |

**Silver（2 个，`0 7 * * *`，`catchup=True`）** —— 跟在 Bronze 后一小时跑，
窗口与对应 Bronze DAG 的回溯天数一致，这样晚到的 Bronze 行下一次 Silver 增量
就能追上，不用等下一次全量重建：

| DAG | schedule | 窗口 |
|---|---|---|
| `dag_silver_weather_archive.py` | `0 7 * * *` | 7 天滑动回溯 |
| `dag_silver_service_request.py` | `0 7 * * *` | 7 天滑动回溯（`LOOKBACK_DAYS=7`，镜像 Bronze 侧），`max_active_runs=1`（Spark 集群只有 7 GB） |

**Gold（0 个增量，1 个手动）** —— `dag_gold_build.py`，`schedule=None`。
14 张表整表重建（`DROP` → 清 prefix → `CREATE` → `INSERT`），人手触发，不跟着
日历跑。触发时机与参数设计见 L2 launch 文档，本 ADR 不重复。

### 静态数据：三张表，没有自动更新

三张 Winnipeg static 参照表——`SRC-WPG-PLOW-SHIFT`（作业排班）·
`SRC-WPG-PARKING-BAN`（禁停）· `SRC-WPG-PLOW-ZONE`（作业分区边界）——
**Bronze 和 Silver 都没有 DAG**，任何一层都不会自动刷新：

- Bronze：`scripts/backfill/backfill_wpg_plow_shifts.py` /
  `backfill_wpg_parking_bans.py` / `backfill_wpg_plow_zones.py`，
  `--source SRC-WPG-*`，全表拉取覆写 `data_static.ndjson.gz`。
- Silver：`spark/jobs/etl_plow_shift.py` / `etl_parking_ban.py` /
  `etl_snow_clearing_address.py`，E1 写完后手动跑过一次（2026-08-17，见
  CLAUDE.md「各层进度」表），**之后没有再跑过**，也没有计划外的触发条件。
- Gold：吃 Silver 输出，同样只在手动 `dag_gold_build` 触发时重建，不单独刷新。

这不是遗漏，是设计：`static` 策略本来就意味着「上游几乎不变、变了也感知不到」，
建 DAG 等于每天问一遍一个几乎总是没有答案的问题。代价是**这三张表可能是陈旧
的而没有任何机制提醒你**——上游改了排班或边界，Bronze 不会自动发现，Gold 也不
会自动过期。上游更新后重新拉取是操作者的责任：手动跑对应的 `backfill_*.py`
→ 对应的 Silver job → `dag_gold_build`。

`SRC-Open-Meteo` 的 `weather_forecast` 数据集是例外中的例外——它是 `snapshot`
策略，**在存储节点**由 `ingestion/snapshot/` 按系统 cron 自动采集，不进
Airflow，也不是这里说的「没有自动更新」——它有自动更新，只是触发者不是
Airflow。机制见 [ADR 0002](0002-airflow-orchestration.md#既不在-airflow-也不是回填的存储节点)。

`_dag_common.py` 提供的是 `get_yesterday()` / `get_bucket()` 等通用工具，
不是原文列的按源命名的日期函数——工具函数按角色命名，不按调用它的城市命名
（城市无关护栏 §2）。

---

## 数据一致性三层兜底

```text
ingest DAG 失败
    ↓
retries=3 自动重试（覆盖网络抖动）
    ↓ 仍失败
catchup=True 下次 scheduler 起来自动补跑
    ↓ 仍有缺口
dag_audit_bronze 每天 08:00 扫描 manifest
    发现缺口 → 直接调 bulk.py 补填
    补填失败 → task 标红 + Discord 告警
```

三层落地情况，和原文「立刻做 / 本周做 / 后续做」的规划表相比：

| 层 | 状态 |
|---|---|
| `retries=3` + `max_active_runs=1` | ✅ `DEFAULT_ARGS`，全 DAG 生效 |
| 失败告警 | ✅ 不是原文设想的 `sla` 参数——实现为 `dags/_alerts.py` 的
  `alert_on_failure`（Discord）+ `ping_watchdog`（死人开关），机制见
  [ADR 0002](0002-airflow-orchestration.md#失败告警) |
| `dag_audit_bronze` | ✅ 已建成，见下 |

`dag_audit_bronze`（`0 8 * * *`，`catchup=False`）比原设计多两件事：

1. **审计对象从 registry 派生**，不是硬编码的 `(source_id, dataset)` 表——
   新增源自动被覆盖，不用改这个 DAG。
2. **区分 existence 与 content 两种检查。** manifest 存在不代表数据没问题——
   2026-08 的分页事故（`docs/dev/postmortem/bronze-socrata-pagination-incident.md`）
   里 34 个损坏分片全部 manifest 健康。所以第二个 task 额外核对 PK 唯一性和
   行数，发现问题只报不补（Bronze 不可变，输出是给 CLI 用的重拉清单）。
3. **`snapshot` 策略的源只核对、从不回填。** 它的上游覆盖式更新、不留历史，
   "补"只会把今天的数据写进昨天的分区，伪造历史——这条例外原文完全没有，
   是 BO-7 快照上线时才加的规则。

---

## 各层真实执行者（当前栈，取代原文的 Phase 1 GCP 表）

原表把 Bronze/Silver/Gold 的执行者分别对到 Airflow Worker / Dataproc /
BigQuery，是 GCP 阶段的设计；现状是三层都在**自建栈**上，且 Bronze/Silver 与
Gold 的触发方式不同（Gold 是手动整表重建，不是每日调度）：

| 层 | 任务类型 | 真实执行者 | 触发 |
|---|---|---|---|
| Bronze | Python HTTP 调用 API | **Airflow Worker**（计算节点容器内） | `PythonOperator`，定时 |
| Silver | PySpark 清洗转换 | **Spark Standalone**（`spark-master`/`spark-worker`，计算节点） | `SparkSubmitOperator` 系，定时 + 手动 backfill |
| Gold | Trino SQL（整表重建，见 `.claude/rules/gold-sql.md` R4） | **Trino**（计算节点，平台级共享服务） | `dag_gold_build.py`，手动 |

BO-7 快照（`weather_forecast` 等 snapshot 策略源）不走这张表——它在**存储节点**
由系统 cron 触发，独立于 Airflow 的可用性，理由见 ADR 0002「既不在 Airflow 也
不是回填的」一节。

---

## 参考

- 触发/回填分工总表、失败告警配置：[ADR 0002](0002-airflow-orchestration.md)
- Silver 执行架构：[ADR 0005](0005-execution-architecture.md)
- 存储/计算/查询栈：[ADR 0006](0006-storage-compute-query-stack.md)
- 分页事故复盘：`docs/dev/postmortem/bronze-socrata-pagination-incident.md`
