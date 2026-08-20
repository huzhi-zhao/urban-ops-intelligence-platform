# ADR 0002 — 用 Airflow 编排增量摄取，回填留在 CLI

> **Status**: Accepted · **Date**: 2026-06 · **Revised**: 2026-08-20
>
> 决策本身没变（调度层用 Airflow，DAG 只做触发与参数渲染）。2026-08-20 重写了
> 正文：原文按 Cloud Composer + `make terraform-apply` 描述部署，而四个云组件已于
> 2026-07-30 全部放弃、`infra/terraform/` 整目录已删（[ADR 0006](0006-storage-compute-query-stack.md)）。
> 这是把过时的操作描述换成实际形态，不是改决策，故不另开 ADR。

---

## 决策

1. **调度层是 Airflow**，自建 Docker（`infra/docker/`），跑在**计算节点**。
2. **DAG 只做触发与参数渲染。** 切片、取数、写对象存储的逻辑一律在
   `ingestion/`、`spark/jobs/`、`scripts/` 里，DAG 里不出现 API 调用与业务分支。
3. **历史回填不进 Airflow，走 CLI 脚本。** Airflow 负责的是「每天这一格」，
   回填负责的是「十年这一片」，两者的失败语义、重试语义和资源占用都不一样。
4. **一个 DAG Run = 一个时间窗口。** Airflow 不做切片——Bronze 由
   `scripts/backfill/bulk.py` 切，Silver 由 Spark job 的 `[start, end)` 窗口切。

---

## 为什么回填不进 Airflow

| | 增量摄取（Airflow） | 历史回填（CLI） |
|---|---|---|
| 触发 | cron，`catchup=True` 补漏 | 人手动跑一次，跑完就不再跑 |
| 窗口 | 一天（311 带 7 天回溯） | 十年，按雪季/日历年切成几十个窗口 |
| 时长 | 分钟级 | 小时级到数小时（Silver 全量 3 小时 03 分） |
| 失败处理 | `retries=3` + Discord 告警 | 断点续跑：`var/backfill/<plan>.state` 记录已完成窗口 |
| 幂等单位 | 当天分区 | 整个窗口 |

把回填塞进 Airflow 需要在 DAG 里复制一套 checkpoint 与窗口列表，而
`scripts/backfill/_plan_lib.sh` 已经提供了 checkpoint / 告警 / watchdog，
且它是层无关的（`WINDOW_RUNNER` 钩子让 Silver 复用同一份库）。

例外：**Bronze 与 Silver 各留了一个手动 backfill DAG**，用于补几天到几个月的
中等窗口——它们 `schedule=None`、Params 驱动，本质是「带 UI 的一次性入口」，
不是回填的主路径。`dag_backfill_silver_service_request` 明确**拒绝超过 400 天的
窗口**，把「这该走 plan 脚本」的判断落在参数校验里而不是三小时后的 OOM 里。

---

## 分工总表

### Airflow 做的（计算节点，`infra/docker/`，10 个 DAG）

| DAG | schedule | 说明 |
|---|---|---|
| `dag_ingest_weather_archive` | `0 6 * * *` | Bronze 增量，`catchup=True` |
| `dag_ingest_service_requests` | `0 5 * * *` | Bronze 增量，7 天回溯，`catchup=True` |
| `dag_audit_bronze` | `0 8 * * *` | Bronze 审计/自愈；审计对象由 registry 的 `partition_strategy` 派生 |
| `dag_silver_weather_archive` | `0 7 * * *` | Silver 增量，7 天滑动回溯 |
| `dag_silver_service_request` | `0 7 * * *` | Silver 增量，7 天滑动回溯，`max_active_runs=1` |
| `dag_backfill_weather_archive` | `None` | 手动，单次最多 365 天 |
| `dag_backfill_silver_weather_archive` | `None` | 手动，兼重建 BO-3 降雪事件表 |
| `dag_backfill_silver_service_request` | `None` | 手动，>400 天直接拒绝 |
| `dag_gold_build` | `None` | 手动。Gold 是整表重建，天天重建一张没变的表没有意义 |
| `dag_smoke_alert` | `None` | 故意失败，不写数据，用来验告警链路真的通 |

下划线开头的三个是共用模块不是 DAG：`_dag_common`（`DEFAULT_ARGS` /
`backfill_params` / `get_bucket`）· `_spark_common`（S3A jar 与 Spark conf）·
`_alerts`（Discord 告警 + 死人开关）。另有 `_trino_common` 供 Gold 使用。

告警挂在 `DEFAULT_ARGS` 上，一处生效覆盖全部 DAG——见下面「失败告警」一节。

### 脚本回填做的（CLI，计算节点上手动执行）

```
scripts/backfill/plan_*.sh          窗口列表（雪季 + 日历年切分），共用 _plan_lib.sh
scripts/backfill/main.py            自动发现 backfill_*.py，--source/--start/--end/--bucket
scripts/backfill/bulk.py            窗口切片 + 线程池
ingestion/backfill/facade.py        单文档的原子 pull+write
```

三层架构与按 `partition_strategy` 的分发表见 `.claude/rules/backfill.md`。

三张 static 参照表（`SRC-WPG-PLOW-SHIFT` / `SRC-WPG-PARKING-BAN` /
`SRC-WPG-PLOW-ZONE`）**既没有 ingest DAG 也没有 Silver DAG**：全表覆写，
没有调度可言，上游变了手动跑一次。

**DAG 数量纪律**：回填留 CLI；只给活跃源建 ingest DAG；static 参照表不建。
照搬「每源一个 backfill DAG + 一个 ingest DAG」会得到十几个没人看的 DAG。

### 既不在 Airflow 也不是回填的（存储节点）

**BO-7 快照采集**（`ingestion/snapshot/` + `scripts/collect_snapshot.py`）跑在
**存储节点**，由系统 cron 触发，不进 Airflow。理由是它服务于覆盖式更新的上游——
**漏采一天即永久缺失**，所以它必须独立于计算节点的可用性，自带告警与外部死人开关，
而不是排在 Airflow 的队列里等一个 paused 标记放行。运维手册见
`docs/guide/snapshot-collection.md`。

对应地，`dag_audit_bronze` 对 snapshot 源**只核对不回填**：查
`ingest_date=` 的 manifest 在不在，不在就报警。"补"只会把今天的数据写进
昨天的分区，伪造历史。

`spark/jobs/etl_weather_forecast.py` 同理没有 DAG：它的 Bronze 输入在 Airflow
之外采集，产出在 M1 之前无人消费。

---

## 失败告警

CLAUDE.md 从一开始就要求每个 DAG 带 `on_failure_callback`，但**该能力此前根本不存在**，
约定是空转的——`dag_backfill_silver_weather_archive` 连续失败 12 天没人知道。
现已实现（`ba43372`），引用旧文档时注意时点。

**两条通道，因为有两种失败，其中只有一种能从进程内部报出来：**

| 失败 | 谁报 | 实现 |
|---|---|---|
| 任务跑了并且失败了 | 进程自己 | `alert_on_failure` → Discord webhook |
| 任务**根本没跑** | 外部看门狗 | `ping_watchdog` 成功时签到，签到没来就告警 |

第二种是 scheduler 挂了 / DAG 解析不了 / 容器没了——**没有任何进程存在去执行
callback**，所以它只能由外部发现。这是同一种沉默的两半，缺一半就还是会出现
「失败了 12 天没人知道」。

### 怎么配置

`.env`（容器视角），两个变量：

```bash
# 失败告警的投递地址（Discord webhook）。留空则回落到 SNAPSHOT_ALERT_WEBHOOK_URL
BACKFILL_ALERT_WEBHOOK_URL=

# 死人开关（healthchecks.io 或等价物）的签到地址。**没有回落**
AIRFLOW_WATCHDOG_URL=
```

告警通道**故意与回填脚本共用**：webhook 只是个投递地址，UOIP 的所有失败进同一个
Discord 频道没有值得为之再加一个环境变量的坏处。

看门狗**故意不回落**到 `SNAPSHOT_WATCHDOG_URL`：在快照采集根本没跑的那天替它签到，
会压掉那个 check 唯一存在的理由。

两个变量都是可选的，且**两条通道都永远不抛异常**——告警失败不能把原始错误替换成
它自己的错误。区别在于：webhook 没配会**警告一次**（失败没被报出去是问题），
watchdog 没配则**静默跳过**（看门狗还没注册，这是设计不是 bug；否则它会每次成功
都刷一条永远为真的警告，真警告就是这么被忽略的）。

### 怎么用

**失败告警是自动的，不用做任何事。** 挂载点是 `_dag_common.DEFAULT_ARGS`，
**一处生效、覆盖全部 DAG，包括还没写的**。

> 🔴 新建 DAG **不要**显式写 `on_failure_callback`——写了就是把它覆盖掉，
> 而且悄无声息。

**看门狗签到要显式调一次**，因为「成功」的定义是每个 DAG 自己的事。当前只有两个
Bronze 增量 DAG 调了，形如：

```python
from _alerts import ping_watchdog
...
ping_watchdog("dag_ingest_service_requests")   # 放在成功路径的末尾
```

**验证链路真的通**，用 `dag_smoke_alert`：`schedule=None`、`retries=0`、
故意失败、不写任何数据。1 秒被调度、6 秒失败。

```bash
docker compose exec airflow-scheduler airflow dags trigger dag_smoke_alert
```

它还有第二个用处：排查「DAG 不跑」时先跑它，一步就分开了「整套不调度」和
「只有这个 DAG 不调度」。

✅ 已端到端验证（2026-08-20）：`dag_smoke_alert` 实收 Discord，且
`dag_gold_build` 两次真实失败的 `TypeError` 也都发出来了。

设计文档：`docs/dev/design/20260816-failure-alerting-and-followups.md`。

---

## 部署形态

计算节点，两条命令：

```bash
make stack-up                  # Airflow + Spark
make stack-restart-airflow     # git pull 之后必须做，LocalExecutor 从内存里的 scheduler fork
make stack-recreate-airflow    # 改了 compose 的卷用这个，restart 不重挂卷
```

⚠️ 不要裸跑 `docker compose -f infra/docker/...`：`${VAR}` 会对着不存在的
`infra/docker/.env` 解析，每个 `${VAR:?}` 都会中止。make target 包了 `--env-file .env`。

⚠️ `.env` 只能存**容器视角**的地址（`trino:8080`、`minio:9000`）。宿主机跑命令
临时加前缀（`TRINO_HOST=localhost TRINO_PORT=8090 ...`）。曾经把 `.env` 改成
宿主机视角，结果容器里所有 Trino 调用被打断三天（O17）。

⚠️ 新 DAG 默认 **paused**，而 `airflow dags trigger` 对 paused 的 DAG **照样返回成功**，
run 落到 `queued` 后永远不动且 scheduler 日志一个字都没有。改完 DAG 文件要重新确认
paused 状态，判据只能用 `airflow dags details <id> -o yaml | grep is_paused`——
`airflow dags unpause` 打印的是改之前的状态。

Hive Metastore / Trino / Superset **不在本仓库的 compose 栈里**，它们是计算节点上的
平台级共享服务（ADR 0006 §9）。

---

## 后果

- ✅ DAG 文件薄，业务逻辑可单测（`tests/unit/`，不需要 airflow 就能跑）。
- ✅ 回填可断点续跑、可在 tmux 里跑几小时，不占 Airflow 的 worker slot。
- 🔴 代价是**有两个入口**：一件事在 Airflow 里跑一遍、在 CLI 里也能跑一遍。
  靠「plan 脚本发的窗口与 Bronze 侧逐个对齐」缓解——Silver 失败时报出的窗口
  与 Bronze 缺口报出的是同一个。
- 🔴 `make lint` + 全套单测全绿**不能证明 DAG 能在 Airflow 3 里跑起来**。
  实测一次撞了四个必然失败的缺陷（`days_ago` 被删、`sql/` 没挂进容器、
  `from dags._dag_common`、`get_bucket()` 漏传 `params`）。现由
  `tests/unit/test_dag_deployment_contract.py` + CI 的 `dags` job
  （独立环境 `.venv-airflow`，钉死 `apache-airflow==3.2.2`）兜住。

---

## 参考

- 三层回填架构与分发表：`.claude/rules/backfill.md`
- Silver 执行架构：[ADR 0005](0005-execution-architecture.md)
- 存储/计算/查询栈（含云组件放弃的决策）：[ADR 0006](0006-storage-compute-query-stack.md)
