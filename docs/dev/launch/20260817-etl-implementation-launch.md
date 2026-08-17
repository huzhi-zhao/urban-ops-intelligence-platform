# E0/E1 Silver ETL 实测记录

> **Date**: 2026-08-17 · **Result**: **Success**
> **上游**：[design/20260817-etl-implementation.md](../design/20260817-etl-implementation.md)（E0–E6 六批执行计划）
> **分支**：`feat/etl-e0-skeleton`（本次未提交代码改动，纯验证）

**本篇管什么**：E0/E1 新写的三个 Silver job（`etl_plow_shift` /
`etl_parking_ban` / `etl_snow_clearing_address`）加一个 E1 复用的边界表 job
（`etl_plow_zone_boundary`）第一次对真实 Bronze 数据跑，行不行、坑在哪、
数字是多少。**不管什么**：代码怎么写的（那是实现，已在 E0/E1 的 commit 里），
以及 E2 怎么做（那是下一篇）。

**为什么在 E2 之前插这一次实测**：`zone_assignment.py` 是 E1/E2 共用的同一份
点归属实现，此前只在本地两个方块加一个蝴蝶结的合成数据上跑过。E2 要拿它去撞
1,840 万行 311、一次回填数小时。先用 23 万个地址点的小表在真实的 82 个多边形
（含 8 个 make_valid 修复过的）上验一遍，发现问题的成本差两个数量级。

---

## 1. 环境

只有一套环境（生产），没有本地 Spark。所有验证只能在计算节点
`oracle-super-node-4c24g` 上做。三个新 job + 一个已有 job：

| job | 状态（跑之前） |
|---|---|
| `etl_plow_shift.py` | 代码就绪，未对真实数据跑过 |
| `etl_parking_ban.py` | 代码就绪，未对真实数据跑过 |
| `etl_snow_clearing_address.py` | 代码就绪，未对真实数据跑过；`zone_assignment` 首次见真实几何 |
| `etl_plow_zone_boundary.py` | 批 4 已生产跑过，本次为 `etl_snow_clearing_address` 提供输入而重跑 |

---

## 2. 实测步骤与踩坑

### 2.1 拉代码：不能 `git pull` 到 main 上

节点当时在干净的 `main`。`git pull origin feat/etl-e0-skeleton` 会尝试把
分支**合进当前分支**，git 因分叉拒绝并提示配置 `pull.rebase`——这条 hint
是错的，节点是部署目录，不该有本地合并提交。正确做法：

```bash
git checkout feat/etl-e0-skeleton   # fetch 已经把分支拉到本地，直接切
```

### 2.2 容器名不是 `airflow-scheduler`

compose 项目名前缀会进容器名。实际是 `uoip-airflow-scheduler-1`，
`docker ps` 现查最准，不要照抄旧文档里的裸名字。

### 2.3 `spark-submit` 缺 s3a jar

第一次不带 `--jars` 跑，报 `ClassNotFoundException:
org.apache.hadoop.fs.s3a.S3AFileSystem`。三个 job 的 docstring 都写了标准调用
（`hadoop-aws:3.3.4` + `aws-java-sdk-bundle:1.12.262`，与 `dags/_spark_common.py`
里 DAG 用的版本一致），但**没有 UDF 的 job（`plow_shift` / `parking_ban`）不需要
最后三条 `--pyspark.python` 系列 `--conf`**，只有 `plow_zone_boundary` /
`snow_clearing_address` 这两个带 Python UDF 的才需要。

### 2.4 `$S3_ENDPOINT_URL` 在宿主 shell 展开成了空字符串 🔴 最费时的一个坑

补上 `--jars` 后报 `InvalidAccessKeyId` / `403`。表面像密钥错，实际是
**`--conf spark.hadoop.fs.s3a.endpoint=$S3_ENDPOINT_URL` 里的变量在节点宿主 shell
展开**（该 shell 没 source `.env`），展开成空字符串，s3a 于是回退到
`s3.amazonaws.com`，拿 MinIO 的 key 去问真正的 AWS——错误信息是 AWS 返回的，
不是 MinIO 的。

**误诊路径**：一度怀疑是密钥被轮换而容器是旧的启动的（`docker compose
... up -d --force-recreate airflow-scheduler` 重建过一次），事后看是多余操作，
真实原因在下面才找到——`docker exec uoip-airflow-scheduler-1 sh -c 'echo
"endpoint=[$S3_ENDPOINT_URL]"'` 证实容器**内部**这个变量有正确值。

**修法**：整条 `spark-submit` 命令用 `sh -c '...'` 包起来交给容器 shell 展开，
而不是让宿主 shell 展开后把字面值传进去：

```bash
docker exec uoip-airflow-scheduler-1 sh -c 'spark-submit \
    --master spark://spark-master:7077 --deploy-mode client \
    --jars https://repo1.maven.org/maven2/org/apache/hadoop/hadoop-aws/3.3.4/hadoop-aws-3.3.4.jar,https://repo1.maven.org/maven2/com/amazonaws/aws-java-sdk-bundle/1.12.262/aws-java-sdk-bundle-1.12.262.jar \
    --conf spark.hadoop.fs.s3a.endpoint=$S3_ENDPOINT_URL \
    --conf spark.hadoop.fs.s3a.path.style.access=true \
    /opt/airflow/plugins/spark/jobs/<job>.py --bucket uoip'
```

### 2.5 `etl_snow_clearing_address` 依赖 `silver_plow_zone_boundary`

这张表批 4 之后没人重跑过。先跑 `etl_plow_zone_boundary`（82 行）铺好输入，
再跑 `etl_snow_clearing_address`，顺序反了会直接读空表。

---

## 3. 结果

四个 job 全部退出码 0，日志里能看到各自的 `Write Job ... committed`，
job 内置的行数 / PK 唯一 / `shift_start < shift_end` 等断言均未抛异常
（用精确值而非下界，见 20260817-etl-implementation.md 的 E1 记录）。

Trino 复核（跨表门禁 job 查不了，只能手工跑）：

```sql
SELECT DISTINCT plow_zone FROM hive.uoip_silver.silver_plow_shift
EXCEPT
SELECT DISTINCT plow_zone FROM hive.uoip_silver.silver_plow_zone_boundary
-- 空集
```

| 检查 | 结果 | 判定 |
|---|---|---|
| `silver_plow_shift` 行数 | 418 | ✅ 精确匹配 |
| `silver_parking_ban` 行数 | 49 | ✅ 精确匹配 |
| `silver_plow_zone_boundary` 行数（多边形数） | 82 | ✅ 精确匹配 |
| `silver_snow_clearing_address` 行数（zone 数） | 25 | ✅ 匹配 `dim_plow_zone` 语义 |
| `plow_shift` 的 `plow_zone` ⊆ `boundary` 的 `plow_zone` | 空集 | ✅ 通过 |
| **空间命中率**（`sum(address_count)` / Bronze 总数） | 237,858 / 237,867 ≈ **99.996%** | ✅ 远超预期 |

**空间命中率是本次最关键的数字。** `zone_assignment` 在真实的 82 个多边形
（8 个经 `make_valid` 修复）上跑 23 万个地址点，未匹配缺口只有 9 条
（约 0.004%）——证明几何修复逻辑在真实数据上是可靠的，可以放心地把同一份
实现推到 E2 的 1,840 万行 311 上。

---

## 4. 遗留（本次未处理）

### 4.1 `silver/snowfall_events/`（复数）旧前缀待清

C1 改名（`snowfall_events` → `snowfall_event`）之后，改名前写入的数据还躺在
旧的复数前缀下，是块死数据。清理顺序不能反：

1. 重跑一次 `etl_weather_archive`（需要 `--emit-events` + 全量历史窗口 +
   `--snowfall-threshold-cm 3.0`，理由与参数见下）。
2. 确认新路径 `silver/snowfall_event/`（单数）确有数据。
3. 才能删 `s3a://uoip/silver/snowfall_events/`（复数）。反过来做，
   万一新路径是空的，会把唯一一份数据删掉。

⛔ 只删这一个前缀。`silver/weather_archive/` 不碰，`bronze/` 更不碰。

**本次未执行**，原因是这一步比前四个 job 重得多：

- 必须 `--emit-events` 全量重建事件表（不能分段，跨月份的降雪事件会被切断）；
- 阈值取 CLAUDE.md 记录的定案 **3 cm/日**（BO-3 任务 1 已定案），
  不是 job docstring 示例里的 2.0；
- 历史起点需要人工确认（是 Bronze 实际最早一天，还是 BO-3 分析用的约定窗口），
  本次会话未拿到这个答案。

命令形状（起点待定）：

```bash
docker exec uoip-airflow-scheduler-1 sh -c 'spark-submit \
    --master spark://spark-master:7077 --deploy-mode client \
    --jars https://repo1.maven.org/maven2/org/apache/hadoop/hadoop-aws/3.3.4/hadoop-aws-3.3.4.jar,https://repo1.maven.org/maven2/com/amazonaws/aws-java-sdk-bundle/1.12.262/aws-java-sdk-bundle-1.12.262.jar \
    --conf spark.hadoop.fs.s3a.endpoint=$S3_ENDPOINT_URL \
    --conf spark.hadoop.fs.s3a.path.style.access=true \
    /opt/airflow/plugins/spark/jobs/etl_weather_archive.py \
    --bucket uoip --start <历史起点> --end <今天> \
    --emit-events --snowfall-threshold-cm 3.0'
```

#### 4.1.1 事后补测（2026-08-17 当天，同分支，仅验证 job 能否跑通）

**范围声明**：下面这次是在 `feat/etl-e0-skeleton` 分支、节点上手工 `spark-submit`
跑的，**目的仅是验证命令本身能不能跑通**，起点 `2000-01-01` 是随手拍的，不是
上面待定的"历史起点"人工确认结果——§4.1 的历史起点仍未定案，全量重建仍未执行。

⚠️ **走法本身有问题，记录下来避免下次重犯**：`silver_weather_archive` /
`silver_snowfall_event` 都已有 DAG（`dag_backfill_silver_weather_archive.py`，
见 `.claude/rules/backfill.md`「DAG status」），支持任意 `[start, end)` 窗口且
自带 `--emit-events` 语义、重试、失败告警。这次绕开 DAG 直接手工
`spark-submit`，代价是失败时连异常堆栈都没抓到（见下）。真正做全量重建时
应该走 DAG，不是照抄这次的命令。

跑了两次，`--start 2000-01-01 --end 2026-08-18 --emit-events
--snowfall-threshold-cm 3.0`：

| 尝试 | job 1（写 `silver_weather_archive`，304 分区） | job 2（`--emit-events` 写 `silver_snowfall_event`） |
|---|---|---|
| 第一次（前台，`docker exec -it`） | ✅ 完成 commit，耗时约 1330s | ❌ 未执行到；被 exit code **137** 中断——事后确认是操作者误按 Ctrl-C，不是环境问题 |
| 第二次（后台，`docker exec -d`，规避误触） | ✅ 完成 commit，耗时约 1330s（可复现） | ❌ job 1 commit 后进程无声退出：`ps` 查无进程、Spark Master 显示 0 个 running app、日志文件里既无异常堆栈也无正常收尾的 `SparkContext is stopping`。怀疑是再次被 SIGKILL（可能 OOM），但 `docker inspect ... OOMKilled` 未及在这次运行期间查证，且 Python stdout 缓冲很可能吞掉了被杀前的最后几行输出——**根因未查清，本次会话不再深挖** |

**结论**：`etl_weather_archive.py` 的**基础归档写入路径**（不带 `--emit-events`）
经两次独立运行验证为可复现、可跑通。**`--emit-events` 全量历史重建路径未验证
跑通**——两次尝试均未到达该阶段的正常结束。§4.1 描述的清理动作（确认新路径
有完整数据 → 删除旧复数前缀）**依然未执行**，前置条件不满足。

**遗留给下一次真正执行 §4.1 时**：① 走 `dag_backfill_silver_weather_archive.py`
而非手工 `spark-submit`；② 历史起点仍需人工确认；③ 若继续手工验证，
`PYTHONUNBUFFERED=1` 能避免这次"进程死了但日志没留下原因"的问题；
④ 若确认是 OOM，全量 26 年窗口可能需要按年份切片而非一次提交。

### 4.2 分支未 push

`feat/etl-e0-skeleton` 本次会话结束时仍只在本地（6 个 commit：E0 三个 +
E1 两个 + 文档一个），远端没有对应分支。

---

## 5. 下一步

按 `docs/dev/design/20260817-etl-implementation.md` 的关键路径：
E2（`etl_service_request.py`，18.4 M 行 311，关键路径起点）→ E2 全量回填 → E4。

本次实测给 E2 的信心来源：`zone_assignment` 已在真实几何上验证过、
命中率 99.996%，E2 撞上去大概率不会在空间归属这一环出问题；真正的未知量是
1,840 万行规模下的执行时间与内存表现，这需要 E2 自己的分片回填计划
（参照 `scripts/backfill/plan_wpg_311_backfill.sh` 的窗口划分思路）。
