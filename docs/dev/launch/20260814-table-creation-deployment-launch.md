# 建表上线记录（Silver / Gold 25 张表）

> **Date**: 2026-08-14 起 · **Result**: **Success**
> **上游**：[20260813-gold-silver-schema-derivation-launch.md](20260813-gold-silver-schema-derivation-launch.md)（表结构评审报告，S3→S4 门禁）·
> [design/20260809-gold-silver-schema-derivation.md](../design/20260809-gold-silver-schema-derivation.md) §5（S0–S7 阶段计划）
> **决策**：[ADR 0006 §9](../adr/0006-storage-compute-query-stack.md)（Trino/Hive 的归属，2026-08-14 增补）

**本篇管什么**：25 张表怎么建到 Trino 里、按什么顺序敲、看到什么才算过、
出错怎么退。**不管什么**：表结构本身为什么长这样（那是评审报告与 design doc），
以及 ETL 怎么写（不在本次上线范围）。

**本次上线明确不含 ETL**。目标只有一个：**把环境调通**——证明
contract → DDL → Trino → Hive Metastore → MinIO 这条链是通的，然后把痕迹清干净。
ETL、backfill、ingestion 下一次上线再做。

---

## 0. 为什么这是一次独立上线

S4 的验收判据是「`make lint` 零告警；**空表能建起来**」。前半条一直在跑，
后半条**从未执行过**——DDL 写完到今天没有任何东西把它们送进 Trino。

这不是形式问题。评审报告 §2.4 的 D2（三张分区表的分区列不在列尾，Trino 直接
拒绝建表）是靠人读 Hive 文档发现的，不是靠建表报错发现的。同类问题还有多少，
在真的建一次之前无法知道。所以先建表、再写 ETL，顺序不能反：
**ETL 写在一个没验证过的表结构上，错误会推迟到回填时才暴露，那时改一次要数小时。**

---

## 1. 前置条件

### 1.1 平台侧（不在本仓库，需先确认）

Trino / Hive Metastore / Superset 是计算节点上的**平台级共享服务**，与
Hadoop / Kafka / Flink 同级，**不在 `infra/docker/docker-compose.yml` 里**。
定性与理由见 ADR 0006 §9。

代价必须显式接受：**`make stack-up` 之后仍然建不了表**，本仓库不再能独立拉起。

上线前逐条确认（2026-08-14 实测状态）：

| 组件 | 状态 | 说明 |
|---|---|---|
| Trino 451 | ✅ 已跑 10 天 | 容器把 8080 映射到宿主 **8090** |
| Hive Metastore 3.1.3 | ✅ healthy | 9083 |
| MinIO（存储节点） | ✅ | Bronze 全量已落盘 |
| Superset | ✅ | 8098，本次不涉及 |
| Grafana | ❌ **未部署** | 本项目唯一缺口，不阻塞本次上线 |
| 计算节点可用内存 | ⚠️ **7 GB** | ADR §2.1 当初实测 8 GB，已降 1 GB |

⛔ **Trino 的 `hive` catalog 必须已配好 MinIO 的 endpoint 与凭证**。这是平台侧配置，
不在本仓库。没配的表现是建表时报连不上对象存储——见 §5 R2。

### 1.2 仓库侧

```bash
make install
```

`.env` 需要（`.env.example` 已列全）：

```
TRINO_HOST=localhost
TRINO_PORT=8090
TRINO_USER=uoip
TRINO_CATALOG=hive
```

`TRINO_HOST` **必填无默认**——沿用 `SocrataClient.domain` 的同一条理由：
默认值会让「忘了配」退化成「静默连到别的实例」。S3 那四个变量本来就要有。

> 🔴 顺手清掉 `.env` 里残留的 `GCS_BUCKET_NAME` 与 `DEPLOYMENT_PHASE`，
> 两者都已于 2026-07-30 废除。

⛔ **必须在计算节点上执行**，不是开发机。`TRINO_PORT=8090` 是宿主端口映射，
从别的机器连要换成节点地址且走网络策略。

---

## 2. schema 划分与 DDL 的部署无关性

按分层各一个 schema（2026-08-14 裁决，方案 ①）：

| schema | 内容 | 写入方 | 读取方 |
|---|---|---|---|
| `hive.uoip_silver` | 8 张 Silver 表 | Spark | Trino / Spark |
| `hive.uoip_gold` | 17 张 Gold 表 | Trino | Trino / Superset |

不合成一个 schema 靠表名前缀区分：两层的**写入方与生命周期不同**，
且 Superset 只应看到 Gold——schema 边界能直接表达这件事，前缀不能。

`sql/ddl/*.sql` **保持裸表名、不写 catalog/schema 限定名**（方案 ①），
由 `scripts/ddl/apply_ddl.py` 在连接时注入。判据是「换个部署要不要改这个文件」——
要改就是配置，不是 schema 定义。这与城市无关护栏 §1 判定业务语义归属是同一条判据。

---

## 3. 上线步骤

约定同快照采集那篇：每步给出**命令**、**期望看到什么**、**不符合怎么办**。
`⛔` 标记的步骤不通过就**停止**。

| 批次 | 做什么 | 大致耗时 | 中断的后果 |
|---|---|---|---|
| 批 0 | 离线门禁 | 2 分钟 | 无 |
| 批 1 | 演练建表（带 prefix） | 10 分钟 | 无——痕迹全在一次性命名空间里 |
| 批 2 | 演练插数据 + 回读 | 5 分钟 | 无 |
| 批 3 | 清除演练痕迹 | 2 分钟 | 残留一个 schema + 一批小文件 |
| 批 4 | 正式建表（不带 prefix） | 5 分钟 | 表建了一半，可重跑 |

**批 0–3 必须同日完成。** 批 4 可以另择时间——但**批 3 不做完不许做批 4**，
否则演练 schema 会长期留在 Metastore 里，将来没人知道它是什么。

---

### 批 0 · 离线门禁（不碰任何环境）

```bash
make lint && make test-unit
```

**期望**：ruff + sqlfluff 全绿；**708 passed, 2 skipped**
（含三方一致性 177 项 + 建表脚本 33 项）。

**不符合**：先修，不要带着红的测试去连 Trino。这一步跑的正是
「DDL ↔ 契约 ↔ StructType 三方一致」，它红了说明表结构本身还没对齐，
连上 Trino 也只是把同一个问题换个地方报。

---

### 批 1 · 演练建表 ⛔

```bash
make ddl-create PREFIX=smoke-20260814
```

**期望**：先两行 `schema hive.uoip_silver_smoke_20260814 ready` /
`...uoip_gold_smoke_20260814 ready`，随后 **25 行 `created ...`**，退出码 0。

**不符合**：见 §5 风险表逐条对号。**任何一张表失败都停在这里**——
25 张表用的是同一套生成逻辑，一张建不起来通常意味着一类建不起来。

> 演练用 `PREFIX` 的理由不是谨慎，是**必需**：见 §4。

---

### 批 2 · 演练插数据 + 回读 ⛔

```bash
make ddl-smoke PREFIX=smoke-20260814
```

**期望**：25 行 `ok <schema>.<table>: 2 rows, first col non-null=...`，退出码 0。

每张表插 **2 行**：第 0 行填满所有列，第 1 行把**所有可空列置 NULL**。
只插 1 行的话，连接器在 Parquet 里拒绝 NULL 也照样通过——而那正是 ETL 首次
运行会撞上的形态。

回读走 `SELECT COUNT(*)`，不信 INSERT 的返回值：要抓的失败正是
**「写进了 Parquet 但 Metastore 的列清单读不出来」**——这种情况不报错，只是查不到。

**不符合**：`INSERT` 成功但 `COUNT(*)` ≠ 2 是最值得停下的一种——
它说明写入路径与读取路径对同一份数据的理解不一致，见 §5 R4。

---

### 批 3 · 清除演练痕迹 ⛔

```bash
make ddl-teardown PREFIX=smoke-20260814
```

**期望**：25 行 `dropped ...` + 2 行 `dropped schema ...` +
一行 `purged N object(s) under s3a://uoip/smoke-20260814/`，退出码 0。

**验证真的清干净了**（DROP TABLE 不删文件，见 §4）：

```bash
aws --endpoint-url "$S3_ENDPOINT_URL" s3 ls "s3://$S3_BUCKET_NAME/smoke-20260814/" --recursive
```

**期望**：无输出。

**不符合**：手工删该前缀。**不要**用任何不带前缀的批量删除命令。

---

### 批 4 · 正式建表

演练三批全过之后才做。

```bash
make ddl-create
```

注意**不带 `PREFIX`**：这次写的是真实路径 `s3a://uoip/{silver,gold}/…` 与真实
schema `hive.uoip_{silver,gold}`。

**期望**：同批 1，25 行 `created`。

**这一步不跑 `ddl-smoke`。** 正式表里不放假数据——`silver_weather_archive` 与
`silver_plow_zone_boundary` 的真实路径下已经有 2026-08-12 落的真实数据，
往里插 `smoke_0` 是污染。环境是否调通已由批 1–3 证明。

**验收**：

```bash
docker exec -it trino trino --execute "SHOW TABLES FROM hive.uoip_silver"
docker exec -it trino trino --execute "SHOW TABLES FROM hive.uoip_gold"
```

**期望**：8 张 + 17 张。

---

## 4. `--location-prefix`：本次上线最关键的一个设计

它不是「方便的隔离选项」，是**安全前提**。三条事实叠在一起：

1. **`silver/` 下已经有真实数据**——`weather_archive` 与
   `plow_zone_boundary`，2026-08-12 落盘（评审报告 §1 时间线）。
2. **建的是 external table**。`DROP TABLE` 只删元数据，**不删文件**——
   这正是 external 的含义。
3. 于是**不清路径的话，下一轮 smoke 会读到上一轮的行**，`COUNT(*) = 2`
   这条断言随即失去意义；而**照 DDL 字面路径清理**又会把上面那批真实数据一起删掉。

prefix 同时挪动**两样东西**，所以两边都躲开：

```
s3a://uoip/silver/weather_archive/                 ← 真实数据，永不触碰
s3a://uoip/smoke-20260814/silver/weather_archive/  ← 建了就删

hive.uoip_silver                                   ← 正式
hive.uoip_silver_smoke_20260814                    ← 一次性
```

schema 名也带上前缀，是为了让演练与正式在 **Metastore 里**也不会撞——
只隔离存储路径的话，两次 `CREATE TABLE` 会争同一个表名。

### 三道守卫

`teardown` 不带 prefix **直接退 2**，三处独立拦截：

| 位置 | 拦什么 |
|---|---|
| `Makefile` 的 `ddl-teardown` | `PREFIX` 为空则不执行命令 |
| `cmd_teardown()` | 连接建立之前就返回 2 |
| `_purge_storage()` | 即使前两道被绕过，空前缀也拒绝枚举 |

只靠调用点记得传参，等于把正确性押在「每个入口都没写错」上——
与批 3.5 修 `upload_window` 时的判断同理（评审报告引用的那条）。

> 🔴 写单测时抓到一个真实缺陷：`" / "` 这种只含空白的 prefix 能骗过
> `.strip("/")`，让 teardown 放行。已抽出 `normalise_prefix()` 统一处理，
> 该 case 钉进了参数化测试。**守卫本身也需要被测试**。

---

## 5. 风险管理

按「先撞上的排前面」排序。R1–R3 是预期内会遇到的，R4–R6 是遇到就得停。

### R1 · `s3a://` 方案 Trino 可能不认 🟡 预计首个卡点

DDL 里的 `external_location` 用 `s3a://`（与 Spark 一致），而 Trino 451 的原生
S3 文件系统主要认 `s3://`。

- **表现**：批 1 建表报 scheme 不支持 / 找不到文件系统。
- **处置**：改 25 份 DDL 的 scheme 为 `s3://`，**但 Spark 侧仍必须写 `s3a://`**
  ——两个引擎的文件系统实现不同，不是随便统一的。改完重跑批 0（一致性单测会
  跟着验）再重跑批 1。
- **为什么没有预先解决**：无法离线判定，取决于平台侧 Trino 的
  `fs.native-s3.enabled` 配置。这正是要真跑一次的原因。

### R2 · Trino 的 `hive` catalog 未配 MinIO 凭证 🟡

平台侧配置，不在本仓库。

- **表现**：建表时报连不上对象存储 / 403。
- **处置**：在平台侧 catalog properties 里配 endpoint、access key、path-style
  （MinIO 无虚拟主机 DNS，**必须** path-style）。
- ⚠️ **凭证只进平台侧配置文件，不进本仓库任何文件**（AGENTS.md 安全规则）。

### R3 · 分区表建不起来 🟢 已处理，但要确认

Hive 连接器要求 `partitioned_by` 恰好是列清单的**末尾且同序**。三张表
（`silver_weather_archive` / `silver_weather_forecast` / `silver_service_request`）
的分区列在契约里排在中间，DDL 已把它们移到末尾并就地注明原因。

- **表现**：`Partition keys must be the last columns in the table`。
- **处置**：这条应该已经不会触发（评审报告 D2 已修，一致性单测按
  「列名比集合、分区列比后缀」校验）。**若仍触发，说明单测的这条规则写漏了情况**，
  先补测试再改 DDL。

### R4 · INSERT 成功但 COUNT(*) 读不到 🔴 停

- **表现**：批 2 里 `INSERT` 无异常，`SELECT COUNT(*)` 返回 0。
- **含义**：写入路径与 Metastore 的列清单/分区注册不一致。**这是最危险的一类
  失败，因为它不报错**——ETL 上线后的表现是数据"写进去了但查不到"。
- **处置**：⛔ 停止上线。查分区是否需要 `CALL system.sync_partition_metadata`，
  以及分区列是否被写成了 `__HIVE_DEFAULT_PARTITION__`
  （smoke 数据刻意不让分区列为 NULL 就是为了排除这条）。

### R5 · 误删真实 Silver 数据 🔴 灾难级，已用三道守卫防住

- **触发条件**：不带 prefix 跑 teardown。
- **后果**：`silver/weather_archive/`（18 个冬季）与
  `silver/plow_zone_boundary/`（82 行 / 25 zone / 8 repaired）被删。
  前者可从 Bronze 重跑恢复，**后者也可以**——Bronze 都还在。
  所以这条是"重跑数小时"，不是"永久丢失"。
- 🔴 **但 Bronze 本身不可重建**：BO-7 快照漏一天永久缺失。
  **任何清理命令都绝不能碰 `bronze/`。** `_purge_storage()` 只接受显式前缀，
  正是为此。

### R6 · 内存不足 🟡

计算节点 23 GB 中当前仅 **7 GB 可用**（比 ADR §2.1 实测时少 1 GB）。

- 本次上线只建空表 + 每表 2 行，**内存不是瓶颈**。
- 真正要盯的是 S6 全量回填时。本篇不处理，记在 §7。

---

## 6. 验收判据

| # | 判据 | 怎么验 | 通过标准 |
|---|---|---|---|
| A1 | 离线三方一致 | `make test-unit` | 708 passed, 2 skipped |
| A2 | **空表能建起来**（S4 原判据） | 批 1 | 25 张全部 `created`，退出码 0 |
| A3 | 能写能读 | 批 2 | 25 张全部 `2 rows`，退出码 0 |
| A4 | 可一键清除 | 批 3 | 25 张 dropped + 2 schema dropped + 前缀下 `s3 ls` 无输出 |
| A5 | 清除不误伤 | 批 3 后 | `silver/weather_archive/` 与 `silver/plow_zone_boundary/` 仍在 |
| A6 | 正式表就位 | 批 4 | `SHOW TABLES` 得到 8 + 17 |

**A5 是唯一一条"什么都没发生才算过"的判据**，也是最容易忘记验的。
批 3 之后**必须**单独确认：

```bash
aws --endpoint-url "$S3_ENDPOINT_URL" s3 ls "s3://$S3_BUCKET_NAME/silver/" 
```

> 本篇**不包含** design doc §6.1 的三个数字（1,298 / 916 格 / 134,123 命中）。
> 那是 **S5** 的门禁，需要真实数据，而本次上线建的是空表。见 §7。

---

## 7. 上线后的状态与下一步

### 7.1 建完表之后，表是空的

这不是缺陷，是本次上线的范围。要让它们有数据，还缺：

- **4 张 Silver 表没有生产它们的 Spark job**：`silver_service_request` /
  `silver_plow_shift` / `silver_parking_ban` / `silver_snow_clearing_address`。
  `spark/jobs/` 目前只有 3 个 job（weather archive / forecast / plow zone boundary）。
- **`sql/dml/` 与 `sql/intelligence/` 不存在**，Gold 一行都还没法填。

### 7.2 写 DML 之前必须先定的三条 🔴

三条全部是评审报告 §8.2 当时按「S4 时天然要决定」推迟、**而 S4 并没有决定**的。
它们的共同点是**回填之后改成本跳一个量级**：

| # | 是什么 | 为什么必须在回填前 |
|---|---|---|
| **C6 / C17** | ✅ **已定（2026-08-15）**：统一 `INSERT OVERWRITE PARTITION`，**以一整天的分区为覆盖单位**，不用 `MERGE`。ingest 时实时转换出的新数据整分区覆盖写入。15 篇 Gold 契约的增量声明按此补 | CLAUDE.md 把幂等列为硬规则；覆盖单位一旦定了就决定了每份 DML 的写法与分区列的选择 |
| **C7** | `silver_service_request` 小文件（按月 coalesce） | 落点在 Spark job 的写入决策，**16 GB 回填后只能重跑** |
| **C1 / C2** | ✅ **已完成（2026-08-15）**：契约文件改名 `snowfall_events.yaml` → `silver_snowfall_event.yaml`；物理路径 `silver/snowfall_events/` → `silver/snowfall_event/`（**改之前 DDL 与 Spark job 写的根本不是同一个路径**——DDL 已是单数，job 还是复数，见下）；裸 `event_id` 全量改 `snowfall_event_id`，覆盖 8 份契约 + 8 份 DDL + 3 个 StructType 模块 + transform。`plow_event_id` / `matched_snowfall_event_id` 不受影响；`scripts/analysis/` 的探针保持原名，它们不参与数据模型 | 物理路径已落数据，回填后改要重跑 |

> 🔴 C1 顺带暴露了一个**已经存在的真实缺陷**：`sql/ddl/silver_snowfall_event.sql` 的
> `external_location` 写的是 `silver/snowfall_event/`（单数），而
> `spark/jobs/etl_weather_archive.py` 写入的是 `silver/snowfall_events/`（复数）。
> 也就是说 8/14 建的那张表**指向一个空目录**，Spark 落的数据在旁边另一个前缀下，
> Trino 查这张表会返回 0 行且不报错——正是 §5 R4 那类「不报错的失败」。
> 建表上线时表是空的，所以没暴露。现已统一为单数。
>
> **因此需要一次人工清理**（在计算节点上做，本次改动不含）：重跑一次
> `etl_weather_archive`，确认新路径下有数据后，删掉旧的
> `s3a://{bucket}/silver/snowfall_events/`（复数那个）。⛔ 只删这一个前缀，
> 绝不能碰 `silver/weather_archive/` 与 `bronze/`。

### 7.3 需要跟着更新的文档

- `docs/guide/` 的部署前置条件要写上 **Trino/Hive 是外部依赖**
  （ADR 0006 §8.4 规定的手册更新，本条并入）。
- Grafana 缺口：不阻塞本次，但要记进待办。

---

## 8. 实际执行记录

**一切正常。** 批 0–4 按 §3 的计划顺序执行完毕，25 张表建起来、smoke 数据
写进去也读得到、清除后前缀下为空且真实 Silver 数据未受影响，§6 的六条验收判据
全部通过。没有触发 §5 的任何一条风险，与计划无偏差。

本次是一次小发布，范围只有「建表 + 烟测 + 清干净」，不逐批展开记录。
