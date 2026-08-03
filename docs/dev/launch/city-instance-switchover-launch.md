# 城市实例切换：上线记录与交接

> **Status**: In progress · **Date**: 2026-08-02 · **Branch**: `feat/city-instance-switchover`
>
> 执行的是 [20260802-city-instance-switchover.md](../design/20260802-city-instance-switchover.md)。
> 本篇只记**已经发生的事**与**下一步该做什么**，设计意图不在这里重复。

---

## 1. 进度

| 批 | 状态 | commit |
|---|---|---|
| **批 0** 前置 | ✅ 完成（0c 除外，见 §4） | `387e904` |
| **批 1** 实例退役 + 通用层去字面量 | ✅ 完成 | `2651358` |
| **批 2** 气象双数据集 | ✅ 代码完成（出口判据待真实 MinIO + Spark，见 §6.1） | `5ebe272` + 收尾 |
| **批 3** 新源接入与回填 | ✅ 代码完成 + 四源真实 API 实测通过（回填未跑，见 §8） | 见 §8 |
| **批 4** 边界能力泛化 | ⬜ 未开工 | — |
| **批 5** 语义配置化 + Silver | ⬜ 未开工 | — |

每次提交都通过 `make lint` + `make test-unit`（当前 **373 passed / 2 skipped**）。

---

## 2. 实测结论（推翻了设计文档的三处假设）

这三条是本轮最有价值的产出，**后续所有批次都依赖它们**，写进了
[winnipeg-data-sources.md](../requirements/winnipeg-data-sources.md) §3.9 与 §6.2。

### 2.1 `39ur-higg` 取得到，但不是 22 个多边形，是 **82 个**

```bash
curl -s "https://data.winnipeg.ca/resource/39ur-higg.json?\$limit=200"
```

| 项 | 实测 |
|---|---|
| 行数 | **82**（22 是分区数；一个分区由多个不相邻多边形组成） |
| 字段 | `entity_id`(唯一) · `city_area` · `plow_zone` · `the_geom` |
| 几何 | 全部 MultiPolygon，82/82 非空 |
| 坐标系 | WGS84 (EPSG:4326)，lon `-97.33..-97.03` / lat `49.71..49.95` |

**摘要的「三源联结」措辞无需改，不必等导师。** ADR 0007 §4.2 的降级方案未触发。

### 2.2 🔴 `plow_zone` 两侧取值不一致 —— 新增的风险项

- `tix9-r5tc` 排班表：恰好 22 个值 `A`–`V`，各 19 行。
- `39ur-higg` 边界表：**25 个值**。`A`–`V` 全在，另有 **`X`(8 个多边形)、
  `B/D`(1)、`Downtown`(1)**。

即 **10/82 个多边形（约 31% 包围盒面积）在排班表里没有任何对应记录**。

空间归属本身不受影响（几何完整，任一点都能落到某个分区）；受影响的是
**分区 → 排班的联结**。大概率是真实运营区分（`Downtown` 有独立清雪计划、
`X` 疑为不按分区作业），但**必须在 Gold 显式建模为「无排班分区」，
不能让它以 NULL 静默传播**。批 4 / BO-4 必须处理。

### 2.3 🔴 `case_id` 不唯一，行粒度是 interaction 不是 case

| 项 | 实测 |
|---|---|
| 总行数 | 18,361,362 |
| distinct `case_id` | 18,018,296（**343,066 重复 = 1.87%**） |
| distinct `interaction_id` | 15,649,799（重复更多） |
| `(case_id, interaction_id)` | **无重复 —— 唯一键** |

一个 `case_id` 是一个服务案件，一个 `interaction_id` 是一次交互（市民就同一件事
多次来电/多渠道）。同组各行 `open_date`/`closed_date`/`type` 完全相同。

**影响**：

1. **Silver 去重键 = `(case_id, interaction_id)`**（批 5 的开放项就此关闭）。
   单用 `case_id` 会误删 1.87% 的真实交互。
2. §3.4 的 **275,243 是行数**（interaction 粒度）。Silver 保持 interaction 粒度，
   批 5 的出口判据才复现得出来。
3. Gold 的「工单量」必须区分口径：按行 = 联系次数，按 `distinct case_id` = 案件数。

---

## 3. 各批已做的事

### 批 0（`387e904`）

- 0b 实测（见 §2），结论写回 `winnipeg-data-sources.md`。
- 0d：`docker-compose.yml` 四处硬编码口令 → `.env` 的 `${VAR:?}` 必填变量
  （Airflow admin / Postgres / webserver secret / JWT）。admin 口令改走容器环境变量，
  不走命令行（命令行会出现在 `docker inspect` 与 `ps`）。
  env map 单独加了 YAML 锚点 `x-airflow-env`，否则 `airflow-init` 覆写
  `environment:` 会整块替换、把 `SQL_ALCHEMY_CONN` 从 `airflow db migrate` 里抹掉。

### 批 1（`2651358`）

- 删 13 个纯实例文件：3 份 source YAML、3 个 backfill 脚本、7 个 DAG。
- `SocrataClient.domain` 改**必填无默认**。
- `NYC_UOIP_CONFIG_DIR` → `UOIP_CONFIG_DIR`；包名 `nyc-uoip` → `uoip`。
- `dag_audit_bronze` / `bronze_profiler` 的硬编码 `(source_id, dataset)` 分发表
  → 从 registry 按 `partition_strategy` 派生。
- 通用层单测改用 `tests/fixtures/sources/` 的**合成角色源**（每种分区策略一个）。
  三处「硬编码清单」断言改为不变量断言，比原来更强。

### 批 2 上半（`5ebe272`）

- **dataset 级 `partition_strategy` 覆盖**（设计文档列为开放项，实为前置改造）。
  `SourceConfig.strategy_for(ds)` / `datasets_with_strategy()` 是唯一入口。
- `config/sources/open_meteo.yaml` 拆两个 dataset：
  - `weather_archive` — `daily`，日粒度 `snowfall_sum` / `temperature_2m_min|max`
  - `weather_forecast` — **`snapshot`**，逐小时前瞻窗口
  坐标改 `49.895,-97.138`，时区 `America/Winnipeg`。
- fetcher：`_flatten_hourly` → `_flatten_series`（兼容 `daily` 块）；
  dataset 显式声明的 `past_days`/`forecast_days` 不再被窗口推导覆盖。
- Silver：`weather_archive` 的 schema / transform / job，含 BO-3 降雪事件切分。
- 顺带修掉批 1 引入的一个潜在 bug：`dag_audit_bronze` 遍历 `load_all_sources()`
  拿到的是 key 不是 value，首次调度就会 `AttributeError`。本地测不出来
  （airflow 未安装），所以把派生逻辑挪到 `ingestion.config.load_datasets_by_strategy`。

### 批 2 收尾

代码侧全部完成，`make lint` 干净、`make test-unit` **332 passed / 2 skipped**。

- 🔴 **`parse_ingest_date` 正则改 snapshot 布局**（`ingest_date=YYYY-MM-DD/`）。
  并且**匹配不上时直接抛**——`regexp_extract` 不匹配返回空串而不是 null，
  全表同值时 `dedupe_by_freshness` 仍会每小时留一行，产出的是「看起来对」的错表。
  这种失败模式必须响，不能靠人看出来。
- `normalize_timestamps` 的时区从模块常量 `America/New_York` 改成**入参**
  （护栏 §1：换城市要改的东西是配置不是代码），值由 job 侧提供，
  对齐 `config/sources/open_meteo.yaml` 的 `timezone`。
- **按 dataset 而不是按 source 命名**（CLAUDE.md 命名约定同步更新）：
  | 旧 | 新 |
  |---|---|
  | `spark/transforms/weather.py` | `weather_forecast.py` |
  | `spark/jobs/etl_open_meteo.py` | `etl_weather_forecast.py` |
  | `dag_ingest_open_meteo` | `dag_ingest_weather_archive` |
  | `dag_backfill_open_meteo` | `dag_backfill_weather_archive` |
  | `dag_silver_open_meteo` | `dag_silver_weather_archive` |
  | `dag_backfill_silver_open_meteo` | `dag_backfill_silver_weather_archive` |
  `WEATHER_{RAW,SILVER}_SCHEMA` 同步改 `WEATHER_FORECAST_*`。
  改名的理由不是整洁：一个 source 现在带两个策略不同的 dataset，
  按 source 命名的 DAG 无法表达「只有 archive 跑在 Airflow 里」。
- **四个 Airflow DAG 只服务 archive**。forecast 是 snapshot，Bronze 由存储节点
  `ingestion/snapshot/` 采集（ADR 0006 §2.2），且**根本不可回填**——上游不留历史。
  `etl_weather_forecast.py` **故意不建 DAG**：它的产出在 M1 之前无人消费。
- `etl_weather_forecast.py` 读法改成 **glob + 按解析出的采集日过滤**，
  不再逐日枚举路径。漏采的快照日是永久缺失、属于正常状态，
  枚举路径会让它变成读取失败。窗口语义也在 docstring 里点明：
  `--start/--end` 选的是**采集日**，输出 `date=` 分区是**记录日**，一次运行
  必然改写窗口外的记录日分区——这正是预报修正的正确行为。
- `dag_backfill_silver_weather_archive` 无条件带 `--emit-events` 重建事件表
  （日常 DAG 不重建：跨窗口边界的暴雪会被切成两个事件）。
  没做成 Param 是因为 Jinja 条件渲染在关闭时会产出空串 argv，argparse 会报
  `unrecognized arguments`。
- 新增 `tests/unit/test_weather_archive_transforms.py`（16 项）：日期不因时区偏移、
  单列 null 不整天丢弃、事件切分的 gap/边界/阈值相等/负参数。
  `test_weather_transforms.py` → `test_weather_forecast_transforms.py`，
  新增「daily 布局路径必须抛」与「时区是入参」两项。

---

## 4. 🔴 需要你做的（我做不了）

| # | 事项 | 为什么只能你做 |
|---|---|---|
| **1** | ✅ **已完成（2026-08-02）** —— GCP service account 密钥已撤销。剩下的只是清理：`rm -rf infra/terraform`（225 MB，代码侧已无引用） | 需要控制台登录。`infra/terraform/keys/nyc-uoip-sa-key.json` 未入 git 但仍是有效凭证，**删本地文件 ≠ 撤销** |
| **2** | 🟡 **轮换 Airflow 口令 —— 你已排到重新部署时做**。旧口令 `zc1992` 已在 git 历史里，改文件不等于改口令。在运行中的 Airflow 里改 admin 密码，并在 `.env` 里填新的四个变量（见 `.env.example`）：`POSTGRES_PASSWORD` / `AIRFLOW_ADMIN_PASSWORD` / `AIRFLOW_WEBSERVER_SECRET_KEY` / `AIRFLOW_JWT_SECRET` | 需要访问运行中的实例 |
| **3** | ✅ **已定（2026-08-02）** —— 311 回填范围：近 10 年全量 + 更早只回填冬季。详见 §7.1 | 设计文档标注「需人工决定」 |
| **4** | ✅ **已定（2026-08-02）** —— 「无排班分区」在 Gold 层建成显式类别。详见 §7.2 | 业务口径判断 |

生成四个密钥：

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## 5. ⚠️ GCS → MinIO 切换：不是「手动重跑 DAG」那么简单

代码侧已确认干净（CI grep 范围内零 GCP 引用）。但有五个环境前提和一个会咬人的顺序问题。

### 五个前提

1. **bucket 必须先在 MinIO 手工建好。** 代码里没有任何 `create_bucket`，
   `S3BronzeLoader` 直接 `put_object`，bucket 不存在会 `NoSuchBucket`。
2. **`.env` 缺 `S3_ACCESS_KEY_ID`/`S3_SECRET_ACCESS_KEY` 时 `docker compose up` 会直接拒绝启动**
   （`${VAR:?}`）。这是好事——不会静默起一个连不上存储的栈。现在四个 Airflow 口令同理。
3. **必须重启 Airflow 容器，不能只 `git pull`。** LocalExecutor 从内存中的 scheduler fork；
   且 `_spark_common.py` 的 `s3a_endpoint()` 在 **DAG parse 时**读 `os.environ`，
   不重启 dag-processor/scheduler，Spark job 会拿到空 endpoint。
4. **`S3A_JARS` 是两个 https URL**，spark-worker 容器要能出网到 `repo1.maven.org`，首次提交会现下。
5. **旧 GCS 数据一行都不会迁过来。** MinIO 是空的，且 Bronze 格式已改 `.ndjson.gz`。
   这是**全量重新回填**，不是「切换」。

### 会咬人的顺序

`dag_audit_bronze` 在空存储上会**自愈风暴**：它扫 14 天 / 3 个月滚动窗口，
发现 manifest 不存在就调 `bulk.py` 回填。MinIO 全空 → 每个 daily 源 14 天全 gap，
同时 `dag_ingest_*` 的 catchup 从 `INGEST_START_DATE` 起也在补。
两者**并发写同一批 Bronze 分区**，且共用同一个 Socrata token 配额。

**正确顺序**：

1. 先在 Airflow UI 里 **pause `dag_audit_bronze`**
2. 用 CLI 跑完历史回填（`python -m scripts.backfill.main --source ... --bucket uoip`）
3. 再 unpause `dag_ingest_*`
4. 最后 unpause `dag_audit_bronze`

> ✅ **批 3 已改**：`INGEST_START_DATE`（`dags/_dag_common.py`）由 `2026-06-16`
> （退役实例的部署日，对 Winnipeg 新源毫无意义）改为 **`2026-08-02`**，即城市实例
> 切换日 —— 当前 bucket 里存在 Bronze 的最早时点。历史不归 catchup 管，归 CLI 回填管。

---

## 6. 下一个会话从这里继续

### 6.1 批 2 已收尾（2026-08-02，见 §7）

代码侧完成。**唯一剩下的是出口判据**：需要真实 MinIO + Spark，**我跑不了**。
可离线替代：直接打 archive API 拉 2008 起逐日 `snowfall_sum`，
用同一套阈值逻辑算出事件数，作为 BO-3 的实测数字（替代「百级」预估）。

### 6.2 批 1 出口 grep 的最后一处残留

```bash
grep -rniE "nyc|cityofnewyork|nypd|borough|dcp" --include="*.py" \
  dags ingestion spark scripts/backfill/_common.py scripts/backfill/bulk.py \
  scripts/backfill/main.py scripts/backfill/_registry.py \
  | grep -vE "etl_dcp|transforms/dcp|dcp_schemas"
```

批 2 收尾已清掉 `nyc_weather_forecast`。现在只剩一处，**已知延后**：

- `_spark_common.py:89` 的 `transforms/dcp` 注释 —— 批 4 清。

### 6.3 批 3 已完成（2026-08-02，见 §8）

代码与四源真实 API 实测全部完成，**回填未跑**（需要 MinIO，见 §8.4）。
批 3 起的那三条注意事项都已兑现：新源确实只花了一份 YAML + 一个脚本，
`main.py` 零改动，`domain` 都配上了，ingest DAG 只加了一个。

**下一批（批 4）的注意事项：**

- 批 4 的出口判据里 `dim_geography` 那张表**还不存在**（`sql/ddl/` 未创建）。
  建议把判据降级为「Silver 侧产出三套 WKT 归属列 + 空间命中率 > 90%，
  分母为有 `geometry` 的冬季工单」，不要把 Gold DDL 拉进批 4。
- 批 4 顺序不可颠倒：**先泛化 `etl_dcp.py` 的 GeoJSON→WKT 通路，跑通 82 个多边形，
  再删存量实例。** `dcp_schemas.py` 里「MultiPolygon 刻意不传 schema」是
  AGENTS.md 的唯一豁免，泛化后必须在新文件里**重述理由**。

---

## 7. 已定的口径决策（2026-08-02）

这两条原本挂在 §4「需要人工决定」，现已定案。写在这里是因为**它们不体现在
代码里**——第一条只体现为批 3 的几条 CLI 参数，第二条要到批 4/5 才落成表结构，
中间隔着好几次会话。

### 7.1 311 回填范围：近 10 年全量 + 更早只回填冬季

| 区间 | 范围 | 约日数 |
|---|---|---|
| `2016-08-01` ~ 今天 | **全量**，每一天 | ~3,650 |
| `2008-06-17` ~ `2016-07-31` | **只回填 11-01 ~ 03-31**（8 个雪季） | ~1,210 |

合计约 4,860 天，相比全量 18 年的 ~6,620 天省下约 27% 的下载；因早年工单量低于
近年，省下的行数比例低于此。

**为什么这个切法不引入管道特例**：`dag_audit_bronze` 只扫最近
`AUDIT_WINDOW_DAYS = 14` 天（`dags/dag_audit_bronze.py`），而近 10 年是完整的，
所以它永远扫不到稀疏区。稀疏区是一段冻结的历史存量，不是每天要判断的状态——
不需要「哪些月份缺了算正常」这类季节窗口配置，代码零改动。

**为什么边界定在 2016-08-01**：给出恰好 10 个完整雪季（2016-17 … 2025-26）
及其配套的平季基线，M1 的训练集与对照组都是整季的，不会出现半个雪季。

**代价，写下来免得以后当成数据丢失**：2016 年之前只有冬季，所以
「冬季 vs 平季」的对照只能做近 10 年，做不了 18 年的长期季节性趋势。
如果论文某一章需要 18 年季节对照，**这不是不可逆的**——311 是 Socrata 上游，
历史一直在，随时可以补跑那几个夏季窗口（与 snapshot 源不同，那才是漏一天就
永久缺失）。

### 7.2 「无排班分区」在 Gold 层建成显式类别

§2.2 实测的 `X`(8 个多边形) / `B/D`(1) / `Downtown`(1)，合计 10/82 个多边形、
约 31% 面积，在排班表 `tix9-r5tc` 里没有任何对应记录。

**定案**：`dim_geography` 给这些多边形打显式标记（如 `has_plow_schedule = false`），
一切与排班相关的指标（响应达标率、作业覆盖率）**在分母里排除它们**，并单独
列出「31% 面积不适用本指标」。

**不允许的两种做法**：让它以 NULL 静默向下传播（最终变成报表上来源不明的空白，
或被当成 0 参与均值）；就近合并进相邻分区（等于编造一份不存在的作业计划）。

**尚未查证、但不挡开发**：这三个值的业务含义。`Downtown` 大概率有独立清雪计划，
`X` 疑为不按分区作业的区域。查证只影响论文措辞（「采用独立作业模式故不纳入
分区分析」比「数据缺失」准确得多），不影响建模——显式标记这个做法在任何一种
解释下都成立，将来查清了只需改标记值。

---

## 8. 批 3 · 新源接入与回填（2026-08-02 完成）

### 8.1 交付内容

| 类别 | 内容 |
|---|---|
| source YAML | `winnipeg_311`(daily) · `winnipeg_plow_shifts` · `winnipeg_parking_bans` · `winnipeg_plow_zones`（后三个 static） |
| backfill 脚本 | 四个 `backfill_wpg_*.py`，registry 自动发现，`main.py` 零改动 |
| contract | `contracts/api-contracts/winnipeg-{311,plow-shifts,parking-bans,plow-zones}.yaml` |
| DAG | `dag_ingest_service_requests.py`（`0 5 * * *`，7 天回溯）**仅此一个** |
| 回填计划 | `scripts/backfill/plan_wpg_311_backfill.sh`（8 雪季窗口 + 1 全量窗口） |

出口判据：`ls dags/dag_ingest_*.py | wc -l` = **2**（≤ 4 ✅）。
`make lint` 干净，`make test-unit` **373 passed / 2 skipped**。

三个 static 参照表**不建 ingest DAG**：一次拉全表，没有调度可言；
上游变了从 CLI 重拉。

### 8.2 🔴 实测暴露的真实缺陷（不是文档问题，是取不到数）

四个源全部打了真实 API dry-run。行数与批 0 逐一吻合：
**82 / 418 / 49**，311 单日 2,827 行（2026-01-15）与 3,774 行（2009-01-15）——
后者同时证明 `open_date` 口径正确、稀疏区窗口也拉得到。

但前两次 dry-run 里，两个 static 源**一行都取不到**：

```
DRY-RUN SRC-WPG-PLOW-SHIFT FAILED: Socrata dataset 'plow_shifts' missing timestamp_field
```

根因是两层配置直接打架，且此前没有任何一个源踩到这个组合：

- 配置层：`static` **禁止** `timestamp_field`（`source_config.py` 的交叉校验）。
- 取数层：`SocrataFetcher` **必须**有 `timestamp_field` 才能构造 `$where` 窗口。
- 平台原有的 `static` 源都是 GeoJSON，走另一个 fetcher，所以从没暴露。

**修法**：`build_fetcher(ds, start, end, strategy=...)` 新增**必填关键字**
`strategy`，取自 `SourceConfig.strategy_for(ds)`。`api_type` 单独不足以决定
怎么取数——`static` + socrata 走全表拉取（`$order=:id`），其余走窗口。

⚠️ **刻意没有用「`timestamp_field` 为 None 就拉全表」这个更省事的写法**：
那会让一个忘了配 `timestamp_field` 的 `monthly` 源从「大声报错」退化成
「静默把整张表写进每个月的分片」。单测把这条钉死了。
必填而非带默认值，同理——默认值正是批 1 处理 `SocrataClient.domain` 时的同一个教训。

**同批修掉的第二个隐患**：`SocrataGeoJsonFetcher` 只调 `fetch_page(limit=1000)`，
即单页封顶。边界表现在 82 行所以看不出来，但它的失败模式是**静默截断**而非报错
——几何表悄悄少几行，空间联结就会无声地丢工单。改为分页 + `$order=:id`。

### 8.3 其余修正

- `INGEST_START_DATE` 2026-06-16 → **2026-08-02**（§5 点名要改的那条）。
  前者是退役实例的部署日，catchup 会白补六周，还要跟一次性 CLI 回填抢同一个
  Socrata token 配额。
- 每源脚本收敛到 `_common.run_standard_backfill()`；死参数 `--dataset`
  （解析了但从不生效，会骗人以为限定了范围）删除，`docs/guide/backfill.md` 同步。
- `plan_wpg_311_backfill.sh` 改用 `${PYTHON:-python3}`：PEP 394 只保证 `python3`，
  本机就没有裸 `python`——这个错会在多小时任务的第一个窗口炸，而那时人已经走开了。
- `dag_audit_bronze` 的派生结果加了断言测试（DAG 里跑不了单测，就在它调用的
  `load_datasets_by_strategy` 那层测）：311 在 daily 审计目标里、三个参照表
  不在任何补洞策略里、snapshot 源只被核对不被回填、且**没有任何已部署数据集
  落在四个策略之外**——那种源会被摄取然后无人审计，直到缺口已经错过。

### 8.4 ⬜ 剩下要你做的

**回填本身没有跑，一行都没写进 MinIO。** 前提是 §5 的五个环境条件，
其中 `S3_*` 环境变量本机 `.env` 里根本没有（只有 `SOCRATA_APP_TOKEN`，
外加两个批 1 就该删的 `DEPLOYMENT_PHASE` / `GCS_BUCKET_NAME` 残留）。

按 §5「会咬人的顺序」执行：

1. MinIO 里手工建好 bucket（代码里没有 `create_bucket`）
2. Airflow UI 里 **pause `dag_audit_bronze`**
3. 三个 static 源各跑一次（秒级），再跑
   `PYTHON="uv run python" S3_BUCKET_NAME=uoip ./scripts/backfill/plan_wpg_311_backfill.sh`
   （几小时，用 `tmux`/`nohup`；中断了直接重跑，同一天同一文件，幂等）
4. unpause `dag_ingest_*` → 最后 unpause `dag_audit_bronze`
