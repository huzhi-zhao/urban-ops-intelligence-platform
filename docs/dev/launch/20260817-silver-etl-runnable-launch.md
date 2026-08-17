# Silver 全链路跑通（L1）上线记录

> **Date**: 2026-08-17（开篇） · **Result**: 待填
> **Design**: [../design/20260817-silver-etl-runnable.md](../design/20260817-silver-etl-runnable.md)
> **前一次上线**: [20260817-etl-implementation-launch.md](20260817-etl-implementation-launch.md)（E0/E1 四个小 job 实测）
> **同一需求的后续上线**: L2（[design](../design/20260817-gold-dimensional-build.md)）· L3（[design](../design/20260817-scoring-chain-and-m1.md)）
>
> **为什么提前开篇**：本次上线含一次 **4,876 天 / 16 GB 的全量回填**，
> 跑完就是既成事实、出错只能重跑数小时。执行清单必须先写下来照着敲，
> 且门禁数字要在跑的当时逐条填，事后回忆不算判据。同 20260802 快照采集篇与
> 20260814 建表篇的理由。

**本篇管什么**：`silver_service_request` 从零到全量的实际过程 —— 步骤、
门禁的真实输出、踩了什么坑。**不管什么**：代码怎么写的（在 PR 里）、
Gold 怎么建（L2 篇）。

**当前状态（2026-08-17）**：阶段 A（代码）已完成，阶段 B 起未开工。
一行生产数据都还没有。

---

## 0. 一页纸：这次要做的事

| 阶段 | 做什么 | 耗时量级 | 能不能退 |
|---|---|---|---|
| A | 代码 + 单测 | 已完成 | ✅ 丢分支即可 |
| B | 部署到计算节点 | 15 min | ✅ 切回旧 commit + 重启 |
| C | **单季**（2024-11-01 → 2025-04-01）+ 幂等 + 告警端到端 | 1–2 h | ✅ 删 5 个月的日分区 |
| D | 收 E0 遗留：重建 `snowfall_event` 后清复数旧前缀 | 30 min | ⚠️ D4 删前缀之后不可退 |
| E | **全量回填** 19 个窗口 | 数小时～1 天 | ❌ 只能重跑 |
| F | 增量 DAG 开、观察 3 天、PR | 3 天 | ✅ 暂停 DAG |

🔴 **两个不可跨越的门**：C 阶段门禁（§3.1）不全过**不进 E**；
D2 未确认新前缀有数据**不删**旧前缀。

---

## 1. 前置检查（开跑前逐条填）

| # | 检查 | 命令 / SQL | 期望 | 结果 |
|---|---|---|---|---|
| P1 | 边界表有真实数据（空表会让 `assign_zone` 直接抛，这是设计不是故障） | `SELECT COUNT(*) FROM hive.uoip_silver.silver_plow_zone_boundary` | **82** | ✅ 已验证 |
| P2 | 分区标签数 | `SELECT COUNT(DISTINCT plow_zone) FROM hive.uoip_silver.silver_plow_zone_boundary` | **25** | ✅ 已验证 |
| P3 | Trino / Hive Metastore 可连（平台级外部服务，`make stack-up` 之后仍可能连不上） | `make ddl-smoke` 只读部分 | 25 张表在 | ✅ 已验证 |
| P4 | Bronze 311 的实际日期范围与月前缀数（**全量门禁的分母，不能用日历天数**） | 见下方 P4 命令 | ~220 个月前缀 | ✅ 2026-08-17：**161 个月前缀，4,877 个日文件**，`2008-11-01` → `2026-08-16`。H1 分区数上限记为 **4,877**（右开区间口径，不是 4,876） |
| P5 | 代码门禁 | `make lint && make test-unit-offline` | 见下 | ✅ 2026-08-17：lint 干净，**789 passed, 2 skipped** |
| P6 | `dag_audit_bronze` 已暂停（本次只读 Bronze，不抢 Socrata token，但抢同节点内存） | Airflow UI | paused | ✅ 已暂停 |
| P7 | 计算节点当前可用内存（基线 7 GB，与 Trino / Metastore / Superset 共用） | `free -g` | ≥ 6 GB 空闲 | ✅ 2026-08-17：`total 23 / used 14 / free 1 / buff-cache 7 / available 8`。与设计基线一致；used 主要是 Trino/HMS 等平台级服务，不归本项目控 |
| P8 | 目标前缀当前是空的（确认这是首次写入，不是在别人的数据上叠加） | 见下方 P8 命令 | 无对象 | ✅ 2026-08-17：发现一条 0 字节目录占位对象 `silver/service_request/`（非数据，MinIO 客户端遗留），已删除；删除后前缀真空 |

⚠️ **本节命令已从 `aws` CLI 改为 boto3**：计算节点（`oracle-super-node-4c24g`）上
`apt-get install awscli` 无候选包（该发行版把它挪到了未启用的 `universe`/需要
`snap`），装它本身不值得。仓库已经依赖 `boto3` 和 `python-dotenv`
（`make install` 装的那批），直接用它们更省事，也不用再额外映射一套
`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` 环境变量。

```bash
# P4 —— Bronze 有哪些月，最早/最晚是哪天。全量门禁的分区数要对这个数，
# 不能对日历天数：2008–2016 只有冬季有数据。
uv run python -c "
import os, boto3
from dotenv import load_dotenv
load_dotenv()
s3 = boto3.client('s3', endpoint_url=os.environ['S3_ENDPOINT_URL'],
                  aws_access_key_id=os.environ['S3_ACCESS_KEY_ID'],
                  aws_secret_access_key=os.environ['S3_SECRET_ACCESS_KEY'])
p = s3.get_paginator('list_objects_v2')
months = set(); files = []
for page in p.paginate(Bucket=os.environ['S3_BUCKET_NAME'], Prefix='bronze/raw/SRC-WPG-311/service_requests/'):
    for o in page.get('Contents', []):
        k = o['Key']
        if k.endswith('.ndjson.gz'):
            files.append(k); months.add(k.split('/')[-2])
files.sort()
print('months:', len(months), '| files:', len(files))
print('first :', files[0] if files else None)
print('last  :', files[-1] if files else None)
"
```

```bash
# P8 —— 目标前缀应当不存在或为空（Size > 0 的才算数据；零字节的
# 目录占位对象不算，但发现了要顺手记一下来源可疑就先别删）
uv run python -c "
import os, boto3
from dotenv import load_dotenv
load_dotenv()
s3 = boto3.client('s3', endpoint_url=os.environ['S3_ENDPOINT_URL'],
                  aws_access_key_id=os.environ['S3_ACCESS_KEY_ID'],
                  aws_secret_access_key=os.environ['S3_SECRET_ACCESS_KEY'])
n = 0
for page in s3.get_paginator('list_objects_v2').paginate(
        Bucket=os.environ['S3_BUCKET_NAME'], Prefix='silver/service_request/'):
    for o in page.get('Contents', []):
        n += 1
        if n <= 10:
            print(o['Key'], o['Size'])
print('total objects:', n)
"
```

> ⚠️ **P5 的 2 个 skip 里有一个是 `test_dag_imports`（本地没装 airflow）。**
> 也就是说两个新 DAG 的 import **没有被任何自动化验证过**，只做了 `py_compile`。
> 这一条挪到 B6 在真实 Airflow 上验证——不要因为 P5 是绿的就跳过它。

> 🔴 **前置阶段核对时发现一个真实缺口，记在这里免得阶段 C 才撞上**：
> `scripts/backfill/plan_silver_service_request.sh` 的 spark-submit 调用
> （§2 阶段 C/E）**没有 `--executor-memory` / `--driver-memory`**，
> `dags/_spark_common.py` 的 `SPARK_CONF` 也不含内存配置——两处都确认过，
> 不是漏看。走的是 Spark standalone 默认值（`executor-memory=1g`）。
> P7 量出来这台节点 available 只有 8 GB、其余被 Trino/HMS 常驻占用，
> 1 GB executor 在 18.4 M 行全表 + `zone_assignment` 空间 UDF 的 shuffle 上
> 很可能就是 E0 那次「`--emit-events` 疑似 OOM 未确认」的同类故障。
> **阶段 C 单季试跑之前必须先补上**，建议 `--executor-memory 4g
> --driver-memory 1g`（8 GB available 里给 Trino/HMS 留余量），
> 单季 C1/C4 顺便验证这个数值够不够。

---

## 2. 执行清单

### 阶段 A · 代码落地（无生产影响，可随时回退）

- [x] A1 `spark/jobs/etl_service_request.py`
- [x] A2 `tests/unit/test_etl_service_request.py`（离线 20 项，覆盖 design §3.7 五项 + 幂等 + 空边界表）
- [x] A3 `dags/dag_silver_service_request.py` + `dags/dag_backfill_silver_service_request.py`
- [x] A4 告警：**无需开发**。`dags/_alerts.py` 已在（`ba43372`），挂在
      `_dag_common.DEFAULT_ARGS` 上一处覆盖全部 DAG。本条只核对两个新 DAG
      **没有**显式写 `on_failure_callback` 把它覆盖掉：

      grep -rn "on_failure_callback" dags/ | grep -v _dag_common.py

      → 2026-08-17 无输出 ✅
- [x] A5 `scripts/backfill/plan_silver_service_request.sh`（+ `_plan_lib.sh` 的
      `WINDOW_RUNNER` 钩子：Bronze 默认走 CLI，Silver 走 spark-submit，
      checkpoint / 告警 / watchdog 一份不复制）
- [x] A6 `make lint` 绿；`make test-unit-offline` = 789 passed, 2 skipped。
      ⚠️ `test_dag_imports` 在本地与 CI 都 **skip**（没装 airflow）→ 见 B6

> ⏪ **回滚点**：A 阶段之内可以直接丢弃分支。写入对象存储的第一条命令
> （阶段 C）之后，`silver/service_request/` 下就有数据了。

### 阶段 B · 部署到计算节点

E0/E1 实测踩过的四个坑，这里直接照做，不重新发现：

- [x] B1 `git fetch && git checkout <branch>` —— **不要 `git pull`**。
      节点是部署目录，`pull` 会尝试合并并因分叉失败，git 提示的 `pull.rebase` 是错的方向。
      ✅ 2026-08-17：首次核对时发现节点还停在 `main`@`ba43372`（PR #15，
      不含 L1），两个新 DAG 文件在 `dags/` 下压根不存在——不是解析失败，
      是代码没合过去。合并/推送后重新 `git fetch && checkout` 解决。
- [x] B2 `docker ps` 现查容器名 —— 是 `uoip-airflow-scheduler-1`，不是 `airflow-scheduler`。
      ⚠️ 2026-08-17 现场确认：这套栈是 Airflow 3.x，DAG 文件解析已经从
      scheduler 拆到独立的 `uoip-airflow-dag-processor-1`
      （`airflow-dag-processor` 服务，`command: airflow dag-processor`，
      `infra/docker/docker-compose.yml:140`）——**scheduler 不再自己解析 DAG
      文件**。B6 要查 import error，问的容器得是这个，不是 scheduler。
- [x] B3 重启 Airflow 容器（`make stack-restart-airflow`）—— `AIRFLOW_SERVICES`
      已经包含 `airflow-dag-processor`（`Makefile:126`），这条本身没问题。
      但"LocalExecutor 从内存里的 scheduler fork"这条旧理由**只对 scheduler
      的任务调度成立，对 DAG 文件解析已经不成立**——B2 查出来 dag-processor
      是单独进程，真正因为不重启而吃到旧代码的是它，不是 scheduler 的
      fork 逻辑。理由过时不代表操作错，仍按 `make stack-restart-airflow`
      走（它两个都重启了）。
- [ ] B4 `spark-submit` 整条命令用 `sh -c '...'` 交给**容器 shell** 展开。
      🔴 宿主 shell 没 source `.env`，`$S3_ENDPOINT_URL` 会展开成空串，
      s3a 回退到 `s3.amazonaws.com`，报出来的是 AWS 的 `InvalidAccessKeyId` / 403，
      看起来像密钥错 —— E0/E1 在这上面花的时间最多。
      ⚠️ C1 的命令块已补上 `--executor-memory 4g --driver-memory 1g`
      （前置检查阶段发现这两个参数此前一直缺失，走的是 1g 默认值）。
- [ ] B5 `--jars` 必带 `hadoop-aws:3.3.4` + `aws-java-sdk-bundle:1.12.262`。
      本 job **带 Python UDF**，所以还需要那三条 `--conf`（无 UDF 的 job 不需要）。
- [x] B6 **两个新 DAG 在真实 Airflow 里能 import**（P5 的 skip 在这里补上）：

      docker exec uoip-airflow-dag-processor-1 airflow dags list-import-errors
      docker exec uoip-airflow-scheduler-1 airflow dags list | grep service_request

      → 2026-08-17：`No data found`（无 import error）；`dags list` 列出
      `dag_backfill_silver_service_request` 与 `dag_silver_service_request`，
      `paused` 均为 `True`（符合 B7 要求）。

      期望：无 import error；列出 `dag_silver_service_request` 与
      `dag_backfill_silver_service_request`。
      ⚠️ 第一条**必须**对 `dag-processor` 容器跑——它是实际解析 DAG 文件、
      产生 import error 的进程（B2）。`dags list` 读的是元数据库里已解析
      好的结果，对哪个容器跑都一样，留在 scheduler 上没问题。
- [ ] B7 增量 DAG 先**保持 paused**。它的 catchup 从 `INGEST_START_DATE`
      (2026-08-02) 起，现在放开会和阶段 C/E 抢同一批分区与同一个 7 GB。
      F1 才开。

### 阶段 C · 单季（2024-11-01 → 2025-04-01）

这一季是**唯一一次「便宜的错误」机会**：152 天、分钟到小时级，
错了删掉重来不心疼。全量段没有这个性质。

- [x] C1 跑单季窗口，记录墙钟耗时与峰值内存（更早一次会话跑的，G1–G6/G10/G11 已在 §3.1 填好）：

```bash
# 在计算节点上。sh -c 让 $S3_ENDPOINT_URL 在容器里展开（B4）。
docker exec uoip-airflow-scheduler-1 sh -c '
spark-submit \
  --master spark://spark-master:7077 \
  --executor-memory 4g \
  --driver-memory 1g \
  --jars https://repo1.maven.org/maven2/org/apache/hadoop/hadoop-aws/3.3.4/hadoop-aws-3.3.4.jar,https://repo1.maven.org/maven2/com/amazonaws/aws-java-sdk-bundle/1.12.262/aws-java-sdk-bundle-1.12.262.jar \
  --conf spark.hadoop.fs.s3a.impl=org.apache.hadoop.fs.s3a.S3AFileSystem \
  --conf spark.hadoop.fs.s3a.endpoint=$S3_ENDPOINT_URL \
  --conf spark.hadoop.fs.s3a.path.style.access=true \
  --conf spark.hadoop.fs.s3a.connection.ssl.enabled=false \
  --conf spark.hadoop.fs.s3a.signing-algorithm=AWSS3V4SignerType \
  --conf spark.pyspark.python=/usr/local/bin/python3.11 \
  --conf spark.pyspark.driver.python=python3 \
  --conf spark.executorEnv.PYTHONPATH=/opt/airflow/plugins \
  /opt/airflow/plugins/spark/jobs/etl_service_request.py \
  --bucket $S3_BUCKET_NAME --start 2024-11-01 --end 2025-04-01
'
```

- [ ] C2 §3.1 的单季门禁**逐条跑、逐条填** —— G1/G2/G3/G4/G5/G6/G9/G10/G11 已填，
      **G7 半完成、G8/G12 未完成**，见下方三条与 §3.1 表
- [x]（半完成）C3 **同一窗口原样再跑一次**，核幂等：
      - ✅ 重跑本身成功（`app-20260817210149-0075`，`spark-submit` 正常结束无异常）
      - ✅ 行数一致：重跑后 `SELECT COUNT(*) ... WHERE open_date_local >= 2024-11-01 AND < 2025-04-01` = **249,369**，与重跑前完全一致
      - ✅ `_rejects` 一致：重跑后 `silver/_rejects/service_request/window=2024-11-01_2025-04-01/` 仍是 **0** 个对象
      - ❌ **窗口外分区未被触碰**这一条没有真正验证成功——本次会话第一次生成的
        `partitions_before.txt` / `partitions_after.txt` diff（空输出）是在**真正重跑之前**做的对比，
        对比的是同一个状态，不能算数；真正的 `spark-submit` 重跑执行之后，**没有重新生成
        `after` 快照并 diff**，这一步被后续排查 `dag_smoke_alert` 卡滞的问题打断，漏掉了。
        补法见执行清单 C3 命令块下方备注。
- [x] C4 实测一个日分区的对象大小，与 0.3–0.5 MB 的估计对账（伞篇 O5）——
      **对不上**：实测 min 0.049 / max 0.310 / avg 0.151 MB，比估计低了 2–3 倍，
      记为偏差不是失败，原因待查（见 §3.1 G9 行）
- [ ] C5 触发 `dag_smoke_alert`（现成的手动 DAG，故意失败、`retries=0`、不写数据），
      确认 Discord 收到，内容含 `dag_id` / `task_id` / `run_id` / 日志链接。
      🔴 这是本次唯一一次告警**端到端**验证 —— 上次静默 12 天正是因为
      「约定写了、看起来配好了」。单测覆盖 payload，覆盖不了容器里的 webhook 还有没有效
      **未完成**：本次触发的 run（`manual__2026-08-17T20:55:44`）卡在 `queued`
      超过 8 分钟不动，排查发现是计算节点上一个独立的基础设施缺陷（见下方 🔴），
      不是 `dag_smoke_alert` 本身的问题，但这次没能把它验完。

> 🔴 **本次会话发现的独立缺陷（非本篇原有范围，但阻塞了 C5）**：`bigdata-net`
> 是跨项目共享网络，本仓库的 Postgres service 曾经就叫 `postgres`，平台侧
> `platform-postgres` 也把自己注册成同一个别名——Compose 自动加的网络别名关不掉，
> Docker DNS 在两个容器间轮询解析，两边密码不同，表现为间歇性
> `password authentication failed for user "airflow"`（`airflow pools list` 复现过）。
> `dag_smoke_alert` 卡 `queued` 大概率是同一根因：scheduler 心跳正常
> （`airflow jobs check` 显示 alive），但状态转换写库偶尔撞上错误的那个连接。
> **已在本次会话修复代码**：`infra/docker/docker-compose.yml` 把 Postgres service
> 改名为 `uoip-postgres`，连接串与 `depends_on` 同步更新，规范记在
> [infra/docker/README.md](../../../infra/docker/README.md#项目级容器命名规范)。
> **尚未部署验证**——需要在计算节点 `docker compose up -d` 重建容器后，
> 重新触发 `dag_smoke_alert` 确认能正常跑到 `failed` 并收到 Discord 消息，
> 这一步和上面 C3 的 G8 补测应该一起做。

> 🔴 **C2 不全过就不进阶段 E。** design §5.2 的定案，不是建议。
> C 阶段的回滚方式：删 `silver/service_request/open_date_local=2024-11-*` 起
> 五个月的前缀，其余什么都不用动。

### 阶段 D · 收 E0 遗留：清 `snowfall_events`（复数）旧前缀

顺序不能反 —— 反了万一新路径是空的，会把唯一一份数据删掉。

- [ ] D1 确认历史起点（design O4：取 Bronze 实际最早一天，**把日期记进本篇**）：待填
- [ ] D2 全量重跑事件表。**走 `dag_backfill_silver_weather_archive`，不要手工
      `spark-submit`** —— 上一次补测就是手工跑的，两次都在 job 2 阶段异常终止
      （一次误操作、一次疑似 OOM 未确认）。触发配置：

      {"start": "<D1 的日期>", "end": "2026-08-18", "bucket": "uoip",
       "snowfall_threshold_cm": 3.0, "snowfall_gap_days": 1}

      ⚠️ 阈值取 BO-3 定案的 **3.0**，不是 DAG Param 默认的 2.0，也不是 docstring
      示例里的 2.0。**不能分段**：跨窗口的降雪事件会被切成两个。
- [ ] D3 `SELECT COUNT(*) FROM hive.uoip_silver.silver_snowfall_event` → 待填（期望 **99**）
- [ ] D4 D3 对上了，**才**删 `s3a://uoip/silver/snowfall_events/`（复数）。
      ⛔ 只删这一个前缀。`silver/snowfall_event/`（单数）是新的，
      `silver/weather_archive/` 不碰，`bronze/` 更不碰。删之前把 `s3 ls` 的
      输出贴进本篇 §3.3 —— 删除是本次唯一不可逆的动作。

> D 阶段与 C 阶段互不依赖，可以并行，但**不要和 E 并行**（抢内存）。

### 阶段 E · 全量回填

```bash
# 计算节点，repo 根目录。19 个窗口，串行（design O3）。
# 走 docker exec 的话三个变量要一起给——默认值是宿主路径。
tmux new -s silver-backfill
export S3_BUCKET_NAME=uoip
export SPARK_SUBMIT="docker exec uoip-airflow-scheduler-1 spark-submit"
export SPARK_JOB_PATH=/opt/airflow/plugins/spark/jobs/etl_service_request.py
export SPARK_EXECUTOR_PYTHONPATH=/opt/airflow/plugins
DRY_RUN=1 ./scripts/backfill/plan_silver_service_request.sh   # 先看窗口列表
./scripts/backfill/plan_silver_service_request.sh 2>&1 | tee var/silver-backfill.log
```

- [ ] E1 先 `DRY_RUN=1` 过一遍，确认 19 个窗口的边界与 Bronze 侧一致
- [ ] E2 `tmux` / `nohup` 起真跑（串行）
- [ ] E3 中途至少核一次 checkpoint 在推进：
      `wc -l var/backfill/plan_silver_service_request.state`
- [ ] E4 §3.2 的全量门禁逐条跑、逐条填
- [ ] E5 记录 DQ 基线数字（行数 / 各列空值率 / 分区完整性 / 构建耗时）——
      L3 的 E6 只做汇总，**基线在这里产生**，跑完就没有第二次机会拿到「首次全量」的数

> **中断了怎么办**：原样重跑同一条命令。已完成的窗口在 state 文件里被跳过，
> 断在哪个窗口 Discord 消息里写了。删 state 文件 = 强制全量重来。
> **失败了怎么办**：先看是哪一个窗口 —— 单个窗口失败不影响已完成的窗口，
> 修完只重跑那一个。

### 阶段 F · 收口

- [ ] F1 `dag_silver_service_request` 取消暂停，观察连续 3 天（§6）
- [ ] F2 `CHANGELOG.md` 记一条
- [ ] F3 分支 push + PR（E0/E1 那次遗留了未 push 的分支，这次别再落下）
- [ ] F4 回填期间暂停的 `dag_audit_bronze`（P6）恢复

---

## 3. 门禁的实际结果

> 逐条贴真实输出（命令 + 结果），**不写「已验证」**。

### 3.1 单季门禁（2024-11-01 → 2025-04-01）

```sql
-- G1 复合键无重复。job 里已按窗口断言过，这里是落盘后的独立复核
SELECT COUNT(*) - COUNT(DISTINCT (case_id, interaction_id))
FROM hive.uoip_silver.silver_service_request;                      -- 期望 0

-- G2 三值齐全，无第四个取值、无 NULL
SELECT geo_match_status, COUNT(*) FROM hive.uoip_silver.silver_service_request
GROUP BY geo_match_status;              -- 恰好 matched / unmatched / no_geo

-- G3 plow_zone 仅在 matched 时非空
SELECT COUNT(*) FROM hive.uoip_silver.silver_service_request
WHERE (geo_match_status = 'matched') <> (plow_zone IS NOT NULL);   -- 期望 0

-- G4 空间命中率：分母是 has_geo 子集，不是行数
SELECT COUNT_IF(geo_match_status = 'matched') * 1.0 / COUNT_IF(has_geo)
FROM hive.uoip_silver.silver_service_request;                      -- 期望 >= 0.999

-- G5 分区列真的是本地日：本地 18:00 之后的工单不得跑到次日。
-- with_timezone() 是必须的：open_ts_utc 是 TIMESTAMP without tz，直接
-- AT TIME ZONE 会把它当成 Winnipeg 本地时间再转，等于什么都没验。
SELECT COUNT(*) FROM hive.uoip_silver.silver_service_request
WHERE open_date_local
      <> CAST(with_timezone(open_ts_utc, 'UTC') AT TIME ZONE 'America/Winnipeg' AS DATE);
                                                                    -- 期望 0
```

```bash
# G6 每个日分区恰好 1 个文件（C7），且分区数 == 窗口天数
uv run python -c "
import os, boto3
from dotenv import load_dotenv
load_dotenv()
s3 = boto3.client('s3', endpoint_url=os.environ['S3_ENDPOINT_URL'],
                  aws_access_key_id=os.environ['S3_ACCESS_KEY_ID'],
                  aws_secret_access_key=os.environ['S3_SECRET_ACCESS_KEY'])
from collections import Counter
c = Counter()
for page in s3.get_paginator('list_objects_v2').paginate(
        Bucket=os.environ['S3_BUCKET_NAME'], Prefix='silver/service_request/'):
    for o in page.get('Contents', []):
        if o['Size'] > 0:
            c[o['Key'].split('/')[2]] += 1
print('partitions:', len(c))
print('files per partition histogram:', Counter(c.values()))
"
# 第一列全是 1；分区数 == 151（右开区间 [2024-11-01, 2025-04-01) 的实际天数，
# 不是 152 —— 2026-08-17 核实前这里写的 152 是算术错误，见下方 ⚠️ 说明）
```

⚠️ **上面这条 shell 命令已从 `aws` CLI 改成 boto3**（原因见 §1 前置检查的同一条说明：
这台计算节点装不了 `awscli`）。**「分区数 == 窗口天数」的期望值也从 152 订正为
151**——`[2024-11-01, 2025-04-01)` 右开区间的实际天数是 Nov(30)+Dec(31)+
Jan(31)+Feb(28)+Mar(31) = 151，原表述是算错的，不是数据有缺口。用
`sequence(0,150)` 逐日 `LEFT JOIN` 核过，151 天一天不少。

| # | 判据 | 期望 | 实际 |
|---|---|---|---|
| G1 | 复合键重复数 | 0 | ✅ **0** |
| G2 | `geo_match_status` 取值集 | 恰好三值，无 NULL | ✅ **unmatched 34 / no_geo 192,016 / matched 57,319，合计 249,369 == 总行数**（`SELECT COUNT(*)` 单独复核过） |
| G3 | `matched` ⇔ `plow_zone IS NOT NULL` 的反例数 | 0 | ✅ **0** |
| G4 | 空间命中率（分母 = `has_geo`） | ≥ 0.999 | ✅ **57,319 / (57,319+34) = 0.9994** |
| G5 | 分区列 ≠ 本地日的行数 | 0 | ✅ **0** |
| G6 | 每个日分区文件数 / 分区数 | 1 / **151**（订正，见上） | ✅ **151**（`SELECT COUNT(DISTINCT "$partition")` 与逐日 `LEFT JOIN` 均核过，无缺失日） |
| G7 | 幂等：连跑两次后行数与分区清单一致 | 一致 | ⚠️ **半完成**：真正重跑（`app-20260817210149-0075`）后 `SELECT COUNT(*)` 复核为 **249,369**，与重跑前一致；`_rejects` 复核仍是 **0**。**分区清单**（每分区仍恰好 1 个文件、无重复/多余文件）没有在这次重跑后重新核过——重跑前的 151/1 结果（G6）是重跑前的状态，不能替代 |
| G8 | 幂等：窗口外分区未被触碰（对比 C1 前后的 `s3 ls` 时间戳） | 未变 | ❌ **未完成**：本次会话跑过一次 `diff`，结果是空输出，但那次对比发生在**真正重跑之前**（对比的是同一份 `before` 快照两次生成的结果，没有意义）。真正的 `spark-submit` 重跑之后没有重新生成 `after` 快照就去排查 `dag_smoke_alert` 卡滞问题了，这一步被漏掉，需要补跑 |
| G9 | 日分区对象大小 | 对账 0.3–0.5 MB | ⚠️ **对不上**：`min 0.049 MB / max 0.310 MB / avg 0.151 MB`（151 个分区实测），avg 比估计低 2–3 倍。这不是失败性判据（文档只要求"对账"），但偏差幅度较大，原因未查——待查方向：实际字段基数/文本长度是否比设计估算时假设的低 |
| G10 | `_rejects/service_request/window=2024-11-01_2025-04-01/` 行数与原因分布 | 极小量级 | ✅ **0**（`s3a://uoip/silver/_rejects/service_request/window=2024-11-01_2025-04-01/` 读取报 `PATH_NOT_FOUND`——这不是故障，是证据：job 逻辑是 `if rejected_count: 才写 rejects_path`（[etl_service_request.py:303-306](../../../spark/jobs/etl_service_request.py#L303)），路径不存在即这一季零拒绝行） |

> 🔴 **G1–G6 门禁通过之前踩了一个真实坑，记进 §4 偏差表**：`silver_service_request`
> 是全新 Hive 分区外部表，Spark 直接用 s3a 写文件从不经过 Hive Metastore，
> C1 跑完 `SELECT COUNT(*)` 一直是 0（不是空表，是 Metastore 压根不知道这些
> 分区存在）。跑 `CALL hive.system.sync_partition_metadata(schema_name =>
> 'uoip_silver', table_name => 'silver_service_request', mode => 'FULL')`
> 之后才能看到数据。这一步不在原设计/执行清单里，20260814 建表篇的 R4
> 曾预先记过这类故障（"INSERT 成功但 COUNT(*) 读不到"），但没有把
> `sync_partition_metadata` 列进本篇的执行步骤——**下一次窗口（E 阶段全量
> 回填的 19 个窗口，以及 F 阶段增量 DAG 每天新分区）都需要这一步**，
> 否则 Trino/Superset 侧永远看不到新写的分区。这不是本季一次性的手动补救，
> 是遗漏的一个常规步骤，见 §5 遗留项。
| G11 | 单季墙钟耗时 / 峰值内存 | —— | 墙钟 ✅ **173.3s**（Spark master `/json/` 查到 `app-20260817200801-0073 etl_service_request_2024-11-01_2025-04-01`，`duration: 173314 ms`）。峰值内存 ⚠️ **未采集**——Spark standalone 的 master REST API 只报 `--executor-memory`/`--driver-memory` 配置值（4g/1g），不报实际峰值占用，app 结束后 Spark UI 随之关闭也无法事后查询；运行期间无 OOM、无异常退出，间接说明 4g/1g 在单季规模上够用 |
| G12 | `dag_smoke_alert` → Discord 真收到，含四要素 | 收到 | ❌ **未完成，被阻塞**：`manual__2026-08-17T20:55:44` 触发后卡在 `queued` 超过 8 分钟不进 `running`。排查发现是计算节点的独立缺陷——`bigdata-net` 上本仓库的 Postgres service 曾经与平台侧 `platform-postgres` 撞用同一个网络别名 `postgres`，Docker DNS 轮询解析导致间歇性 `password authentication failed for user "airflow"`（`airflow pools list` 复现过），怀疑是 scheduler 状态转换写库偶尔连错库。**代码已在本次会话修复**（`infra/docker/docker-compose.yml` 把该 service 改名 `uoip-postgres`，见 [infra/docker/README.md](../../../infra/docker/README.md#项目级容器命名规范)），但**尚未部署到计算节点、也未重新触发验证**——这是遗留项，不是 `dag_smoke_alert` 或告警链路本身的缺陷 |

### 3.2 全量门禁

```sql
-- H2 行数对账。分母侧的 Bronze 行数来自 manifest 的 record_count 之和
SELECT COUNT(*) FROM hive.uoip_silver.silver_service_request;

-- H4 冬季子集量级（比例判据，不是精确值）
SELECT COUNT(*) FROM hive.uoip_silver.silver_service_request
WHERE MONTH(open_date_local) IN (11, 12, 1, 2, 3);
```

| # | 判据 | 期望 | 实际 |
|---|---|---|---|
| H1 | 分区数 | == Bronze 实际有数据的天数（P4），**不是日历天数** | 待填 |
| H2 | 全表行数 | 与 Bronze 18.4 M 的差额**必须能被 `_rejects` 全部解释** | 待填 |
| H3 | `_rejects` 总行数与原因分布（`missing_type` / `unparseable_open_date` / …） | 极小量级；非 0 就在本篇写清是什么 | 待填 |
| H4 | 冬季子集行数 | 量级对齐 `winter_subset_approx ≈ 275,282` | 待填 |
| H5 | 探针复现：空间命中 | **134,123 / 134,258** | 待填 |
| H6 | 全量墙钟耗时 / 最慢的窗口 | —— | 待填 |

> 🔴 **H5 对不上时信探针** —— 管道里有一步和探针口径不一致，不是探针老了。
> H2 对不上（Silver + rejects < Bronze）就是**丢行**，这是唯一一条
> 「对不上就必须停下查清、不能记成遗留项」的判据。

### 3.3 D 阶段删除前的现场记录

```bash
aws --endpoint-url "$S3_ENDPOINT_URL" s3 ls --recursive \
  "s3://$S3_BUCKET_NAME/silver/snowfall_events/"   # 复数，删除对象
aws --endpoint-url "$S3_ENDPOINT_URL" s3 ls --recursive \
  "s3://$S3_BUCKET_NAME/silver/snowfall_event/"    # 单数，必须先有数据
```

删除前输出：待填 · 删除命令与时刻：待填

---

## 4. 与设计的偏差

| 设计怎么写的 | 实际怎么做的 | 为什么改 |
|---|---|---|
| §3.1 第 7 步：拒绝行进 `silver/_rejects/service_request/` | 实际写成 `.../service_request/window={start}_{end}/` | 最常见的拒绝理由**就是 `open_date` 不可解析**，那种行没有日分区可落；按窗口收口后，重跑同一窗口只覆盖自己的拒绝行，全量门禁 H2/H3 也仍然可以整前缀数 |
| §5 未提命中率的**代码内**下限 | job 在 `has_geo ≥ 1000` 且命中率 < 90% 时**抛** | CLAUDE.md 的升级条件本来就是 90%/geo 子集；只写在 launch 门禁里等于全量跑完才发现。小样本不判，避免 2008–2016 稀疏段误报 |
| 未规定回填 DAG 的窗口上限 | `dag_backfill_silver_service_request` **拒绝 > 400 天** | 全量不该走 DAG（不切片、不 checkpoint）。与其跑一小时再 OOM，不如在 `check_params` 里拒 |
| §3.5 只说 plan 脚本 `source _plan_lib.sh` | 给 `_plan_lib.sh` 加了 `WINDOW_RUNNER` 钩子 | 库里的 `run_window` 原本硬编码 Bronze CLI。checkpoint / 告警 / watchdog 与层无关，加一个钩子好过复制第二份库 |
| （其余）| 待填 | |

> 「没有偏差」也要写一行 —— 那是对设计质量的正面证据。

---

## 5. 遗留项

- 🔴 **新发现（2026-08-17，C2 阶段）：Trino 侧看不到新分区，缺 `sync_partition_metadata`
  步骤**。`silver_service_request` 是 Hive 分区外部表，Spark 用 s3a 直接写文件，
  从不经过 Hive Metastore，所以 C1 跑完 `SELECT COUNT(*)` 一直是 0（详情见 §3.1
  下方的 🔴 说明）。`CALL hive.system.sync_partition_metadata(...)` 手动补一次能看到，
  但这一步**不在 `etl_service_request.py`、两个新 DAG、`_spark_common.py` 里的
  任何地方**——E 阶段 19 个全量窗口、以及 F1 打开后每天的增量分区，都会复现
  同一个"写进去了但查不到"的假象，而且**不报错**。
  → **必须在 F1（增量 DAG 取消暂停）之前解决**，且强烈建议在 E 阶段全量回填
  开始前就解决，否则 19 个窗口跑完还要手动 sync 一次、且中途任何人查 Trino
  验证 checkpoint 进度都会被这个假 0 误导。解法方向：DAG 里加一个
  `TrinoOperator`/`PythonOperator` 任务，在 Spark 写完后调用一次
  `sync_partition_metadata`（增量 DAG 每次跑完都要），`plan_silver_service_request.sh`
  的 `WINDOW_RUNNER` 每个窗口跑完后也要调一次，两处都得补，不是加一处就够。
- **O2（design §6）**：`_bronze_month_prefixes` 现在是两份（weather 一份、
  service_request 一份）。**单季门禁过后、全量之前**再抽到 `spark/transforms/`，
  现在抽会同时动一个已在生产跑的 job。→ 本次上线内处理
- **O6（design §6）**：死人开关未注册，`AIRFLOW_WATCHDOG_URL` 为空时
  `ping_watchdog` 静默跳过（这是设计）。「scheduler 挂了没人知道」那一半没闭合。
  → L1 之后、增量 DAG 长期开着之前
- **批 3「日志噪音」**：`scripts/` 挂在 `plugins/` 下被 Airflow 逐个 import，
  每次任务刷 15 行无关 ERROR。→ 回填跑完再动
- 🔴 **新发现（2026-08-17，C5 阶段）：`bigdata-net` 上 Postgres service 名撞车，
  代码已修、待部署验证**。本仓库 Postgres service 曾经就叫 `postgres`，
  跨项目共享网络上平台侧 `platform-postgres` 也注册了同一个别名，Docker DNS
  轮询解析导致间歇性 `password authentication failed for user "airflow"`——
  `airflow pools list` 复现过，且很可能是这次 `dag_smoke_alert`
  卡 `queued` 8 分钟不动的根因。已把 service 改名为 `uoip-postgres`
  （`infra/docker/docker-compose.yml`），命名规范记进
  [infra/docker/README.md](../../../infra/docker/README.md#项目级容器命名规范)，
  同时该 README 留了一条待办：评估把本栈内部服务迁到不与平台共享的私有网络。
  → **必须在 C5 补测（`dag_smoke_alert` 端到端验证）之前部署**：
  `docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env up -d`
  重建容器，再重跑 C3 的 G8（重跑后 diff 分区快照）与 C5（触发 `dag_smoke_alert`
  确认能正常到 `failed` 并收到 Discord）
- 其余待填。每条附去处（Ticket / L2 / L3 / ADR）。

---

## 6. 上线后需要观察的

- **增量 DAG 连续 3 天**（F1 起）：每天 1 个新日分区、文件数 1、命中率不掉。
  7 天回溯意味着每天会重写最近 7 个分区 —— 这是设计，不是异常。
- **告警通路**：C5 确认过一次人为失败能收到；此后 7 天内若有真实失败，核实是否收到。
- **升级人类的条件**（CLAUDE.md）：某个日分区 0 行；空间命中率在 `has_geo`
  子集上跌破 90%（job 现在会自己抛）；上游 Socrata schema 变化（新增/改名字段）；
  `_rejects` 出现 `missing_type` / `missing_channel_raw`——契约写的是非空，
  真出现就是上游改了。
- **不盯的**：`closed_ts_utc` 的缺失率（3.5%，C8 语义未验证，H1 不阻塞）。
