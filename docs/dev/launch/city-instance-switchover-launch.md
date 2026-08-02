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
| **批 2** 气象双数据集 | 🟡 **代码完成，DAG 与测试未收尾** | `5ebe272` |
| **批 3** 新源接入与回填 | ⬜ 未开工 | — |
| **批 4** 边界能力泛化 | ⬜ 未开工 | — |
| **批 5** 语义配置化 + Silver | ⬜ 未开工 | — |

三次提交都通过 `make lint` + `make test-unit`（当前 **314 passed / 2 skipped**）。

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

### 批 2（`5ebe272`，未完）

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

---

## 4. 🔴 需要你做的（我做不了）

| # | 事项 | 为什么只能你做 |
|---|---|---|
| **1** | **GCP 控制台撤销 service account 密钥** —— `nyc-uoip-sa@nyc-uoip-prod.iam.gserviceaccount.com`（project `nyc-uoip-prod`），或整个删掉该 SA。**撤销之后**再 `rm -rf infra/terraform` | 需要控制台登录。`infra/terraform/keys/nyc-uoip-sa-key.json` 未入 git 但仍是有效凭证，**删本地文件 ≠ 撤销** |
| **2** | **轮换 Airflow 口令**。旧口令 `zc1992` 已在 git 历史里，改文件不等于改口令。在运行中的 Airflow 里改 admin 密码，并在 `.env` 里填新的四个变量（见 `.env.example`）：`POSTGRES_PASSWORD` / `AIRFLOW_ADMIN_PASSWORD` / `AIRFLOW_WEBSERVER_SECRET_KEY` / `AIRFLOW_JWT_SECRET` | 需要访问运行中的实例 |
| **3** | **决定 311 回填范围**：全量 18 年（约 6,600 个日文件）还是只回填 11–3 月分区。设计文档倾向后者，代价是缺夏季对照 | 设计文档标注「需人工决定」。**批 3 开工前必须定** |
| **4** | **`plow_zone` 三个孤儿值的口径**（§2.2）。`X` / `B/D` / `Downtown` 在 Gold 里怎么表述，是否要问导师 | 业务口径判断 |

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
同时 `dag_ingest_*` 的 catchup 从 `INGEST_START_DATE = 2026-06-16` 起也在补。
两者**并发写同一批 Bronze 分区**，且共用同一个 Socrata token 配额。

**正确顺序**：

1. 先在 Airflow UI 里 **pause `dag_audit_bronze`**
2. 用 CLI 跑完历史回填（`python -m scripts.backfill.main --source ... --bucket uoip`）
3. 再 unpause `dag_ingest_*`
4. 最后 unpause `dag_audit_bronze`

> ⚠️ `INGEST_START_DATE = 2026-06-16`（`dags/_dag_common.py`）是 NYC 时代的部署日，
> 对 Winnipeg 新源没有意义，catchup 会从 6/16 一路补到今天。**批 3 必须改这个值。**

---

## 6. 下一个会话从这里继续

### 6.1 先收尾批 2（约半天）

1. **DAG 收尾**：
   - `dag_silver_open_meteo.py` / `dag_backfill_silver_open_meteo.py` 仍指向旧
     dataset 名 `nyc_weather_forecast` 与旧 job。要么改指 `weather_forecast`，
     要么新建 `dag_silver_weather_archive.py` 调 `etl_weather_archive.py`。
   - `spark/transforms/weather.py` 的 `parse_ingest_date` 正则匹配的是
     `data_YYYY-MM-DD.ndjson`（daily 布局）。**forecast 现在是 snapshot 布局**
     （`ingest_date=YYYY-MM-DD/data.ndjson.gz`），正则必须改，否则 `ingest_date`
     全为空串、`dedupe_by_freshness` 退化成任意取一行。**这是当前最需要修的一处。**
   - `spark/transforms/weather.py` 的 `SOURCE_TZ = "America/New_York"` 未改。
2. **单测**：`spark/transforms/weather_archive.py` 的三个函数尚无测试
   （`tests/unit/test_weather_archive_transforms.py`）。参照
   `tests/unit/test_weather_transforms.py` 的写法。
3. **批 2 出口判据**需要真实 MinIO + Spark，**我跑不了**。
   可离线替代：直接打 archive API 拉 2008 起逐日 `snowfall_sum`，
   用同一套阈值逻辑算出事件数，作为 BO-3 的实测数字（替代「百级」预估）。

### 6.2 批 1 出口 grep 的两个已知残留

```bash
grep -rniE "nyc|cityofnewyork|nypd|borough|dcp" --include="*.py" \
  dags ingestion spark scripts/backfill/_common.py scripts/backfill/bulk.py \
  scripts/backfill/main.py scripts/backfill/_registry.py \
  | grep -vE "etl_dcp|transforms/dcp|dcp_schemas"
```

现在只剩两类，都是**已知延后**：

- `nyc_weather_forecast` —— 在 `spark/` 的 weather 三件套与两个 Silver DAG 里。
  **批 2 收尾时一并清掉**（config 已经改了，代码侧没跟上）。
- `_spark_common.py:89` 的 `transforms/dcp` 注释 —— 批 4 清。

### 6.3 批 3 起的注意事项

- **新源 = 一个 `config/sources/*.yaml` + 一个 `backfill_*.py`**，registry 自动发现，
  `main.py` 零改动。测试也会自动覆盖（批 1 改成了发现式）。
- `SocrataClient.domain` 现在必填，新 YAML 别忘了 `domain: data.winnipeg.ca`。
- DAG 数量纪律：回填留 CLI，只给活跃源建 ingest DAG，`ls dags/dag_ingest_*.py | wc -l ≤ 4`。
- 批 4 的出口判据里 `dim_geography` 那张表**还不存在**（`sql/ddl/` 未创建）。
  建议把判据降级为「Silver 侧产出三套 WKT 归属列 + 空间命中率 > 90%，
  分母为有 `geometry` 的冬季工单」，不要把 Gold DDL 拉进批 4。
- 批 4 顺序不可颠倒：**先泛化 `etl_dcp.py` 的 GeoJSON→WKT 通路，跑通 82 个多边形，
  再删存量实例。** `dcp_schemas.py` 里「MultiPolygon 刻意不传 schema」是
  AGENTS.md 的唯一豁免，泛化后必须在新文件里**重述理由**。
