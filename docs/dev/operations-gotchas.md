# 操作坑

跨篇复现的操作坑。收在这里的判据只有一条：**它在两次以上、互不相关的上线里
各绊了一次**——只在某一次运行里出现过的，属于那篇 `launch/`，不进来。

常青文档，原地改写。每条都注明**第一次实测的出处**，原始现场在那篇 `launch/` 里，
本篇不复述那次运行的经过，只留可执行的判据。

> 事件类文档（`launch/` · `adr/` · `postmortem/`）里重复的同一个坑**不删**：
> 每篇要能独立还原那一次运行。本篇收敛的是常青侧的转述。

---

## Airflow

### `airflow dags unpause` 打印的是改之前的状态

所以**不能用它的回显判断是否生效**。唯一判据：

```bash
docker exec uoip-airflow-scheduler-1 \
  airflow dags details <dag_id> -o yaml | grep is_paused
```

第一次实测 [L2 launch §4.12](launch/20260819-gold-dimensional-build-launch.md)，
在 [DQ 审计那轮](launch/20260822-out-of-pipeline-dq-audit-launch.md) 原样复现第二次。

### paused 的 DAG 照样接受 `dags trigger`，然后永远不动

`dags trigger` 返回成功，run 落到 `queued` 就停住，**scheduler 日志一个字都没有**。
而 `airflow dags test` 是前台解析执行的、不看 paused 标记——两条路表现完全相反，
所以"手动能跑、调度不跑"不是调度坏了。

- **改完 DAG 文件要重新确认 paused 状态**：实测有 DAG 在 `git pull` 改了文件之后
  从 unpaused 变回 paused，没人手动 pause 过。
- **新建的 DAG 默认 paused。**
- 排查"DAG 不跑"先用 `dag_smoke_alert` 划范围：它 1 秒被调度、6 秒失败，
  一步分开"整套不调度"和"只有这个 DAG"。

出处：[L2 launch §4.10 与 §4.12](launch/20260819-gold-dimensional-build-launch.md)。

### `dags trigger` 返回时任务还在排队

trigger 到落第一行日志约 **30 秒**。紧跟着 grep 日志**必然只看到上一趟**，
据此判断成败会判反。等 run 的 `end_date` 非空再读。

出处：[DQ 审计 launch §4.4](launch/20260822-out-of-pipeline-dq-audit-launch.md)。

### Airflow 3 改了排查命令的形状

- `dags list-runs -d <dag>` 的 `-d` 已删，dag_id 改位置参数。
- structlog 日志走 **stdout**，嵌套取 run_id 的 `$(... 2>/dev/null | awk ...)`
  一定抓错（实测抓到 `[info`）。

出处：[跨层对账 launch](launch/20260822-cross-layer-reconciliation-and-certification-launch.md)。

### 改了 compose 的卷要 `make stack-recreate-airflow`

`make stack-restart-airflow` 走的是 restart，**不重挂卷**。挂载改了却只 restart，
表现为文件在宿主机上明明存在、容器里就是没有。

出处：[L2 launch §4.10](launch/20260819-gold-dimensional-build-launch.md)。

---

## 环境与连接

### `.env` 只存容器视角，宿主机跑命令临时加前缀

```bash
TRINO_HOST=localhost TRINO_PORT=8090 <command>
```

`.env` 里的 `trino:8080` 是给 Airflow 容器的。把 `.env` 改成宿主机视角，
会打断容器里**所有** Trino 调用，且要等 `retries=3 × 5min` 耗尽约 16 分钟才告警。

出处：[L2 launch §4.13（O17）](launch/20260819-gold-dimensional-build-launch.md)。

### `--conf` 里的 `$VAR` 在宿主 shell 就展开了

`$S3_ENDPOINT_URL` 展开成空串，s3a 回退到 `s3.amazonaws.com`，报出来的是 **AWS 的
403**——看起来像密钥错，实际是 endpoint 没传进去。

出处：[E0/E1 launch §2](launch/20260817-etl-implementation-launch.md)。

### 一个 extras 一个环境，用 `UV_PROJECT_ENVIRONMENT`

```bash
UV_PROJECT_ENVIRONMENT=.venv-ml uv run --extra ml <command>
```

`uv run --python .venv-ml` 是**错的**：`--python` 只换解释器，不换包集合。

`make test-dags` 必须走独立的 `.venv-airflow`，原因更硬：Spark provider 依赖
`pyspark-client`，那是个独立发行包却往同一个 `pyspark/` 目录写文件，把钉死的
3.5.1 覆盖成 4.2.0，**uv 不报冲突、lock 也仍写 3.5.1**，之后 Spark 单测炸在
pyspark 内部、看不出关联。

出处：[L2 launch §4.11](launch/20260819-gold-dimensional-build-launch.md) ·
[L3 launch](launch/20260820-scoring-chain-and-m1-launch.md)。

---

## 对象存储与 Spark

### 写完 Silver 要 `sync_partition_metadata`

没同步时 **Trino 侧读出来是 0 行，而且不报错**——目录看起来是满的。

出处：[L1 launch 阶段 H1](launch/20260817-silver-etl-runnable-launch.md)。

### commit 阶段会静默几十分钟，那不是卡死

对象存储没有原生 rename，`FileOutputCommitter` 对每个对象都是一次 copy+delete
往返。38,688 个对象实测**日志静默 90 分钟以上且不报错**，两次被误判为 OOM 中断。
**成本随分区数线性增长**，分区多的 job 开跑前设
`mapreduce.fileoutputcommitter.algorithm.version=2`。

判活三件套：

```bash
ps -o pid,stat,etime,time,pcpu -C java   # TIME 涨不涨
docker stats                             # NET I/O 走不走
# 隔 60s 数两次对象数，变不变
```

出处：[commit stall 复盘](postmortem/silver-commit-stall-incident.md) ·
[L1 launch §2 阶段 D2](launch/20260817-silver-etl-runnable-launch.md)。

---

## Shell 脚本

### `PYTHON` 可能是多词命令

计算节点上是 `uv run python`，所以 `"${PYTHON:-python3}"` 当一个词引用会
`command not found`。拆开：

```bash
read -ra PY <<< "${PYTHON:-python3}"
"${PY[@]}" -m scripts.backfill.main ...
```

已经咬过两次，**两次真实错误都在下一层才浮现**（传下去一个空串，报的是
`--end ''`，没有一个字提到 `PYTHON`）。更好的做法是根本不 shell out——
日期加一天用纯 bash 就能算。

出处：[`.claude/rules/backfill.md`](../../.claude/rules/backfill.md)。
