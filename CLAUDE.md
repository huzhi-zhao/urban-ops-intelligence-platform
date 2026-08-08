# NYC-UOIP — Claude Code Instructions

> This file is read by Claude Code at the start of every session.
> Keep it under 1000 lines. Move long procedures to `.claude/rules/`.

@AGENTS.md

---

## Project identity

Urban Operations Intelligence Platform (UOIP). Deployed city: **Winnipeg, MB**.
A production-grade Lakehouse pipeline that ingests civic open data (311 service
requests, plow-zone schedules, snow parking bans, Open-Meteo weather, plow-zone
boundaries) and produces a Winter Operational Load Score + resource allocation
recommendations per ward / neighbourhood.

The repository name still says NYC. That is historical — the NYC deployment is
being retired (see 城市无关护栏 §3 below). Three delivery horizons govern what
is in scope right now: `docs/dev/requirements/project-overview.md` 「交付视野」.
**H1 = the 2026-09-22 conference delivery, and until end of September it is the
only horizon being worked on.**

**目标栈是全自建，没有云托管组件**：MinIO · Spark 3.5.1 Standalone · Airflow ·
Hive Metastore · Trino · Superset，全部 Docker，存储与计算分离部署在两个节点上。

> ⚠️ **"Phase 1 (GCP) / Phase 2 (自建)"的双阶段划分已于 2026-07-30 废除**
> ——四个云组件（Dataproc / Composer / GCS / BigQuery）被逐个放弃，划分已无
> 指代对象。`DEPLOYMENT_PHASE` 环境变量随之作废。决策见
> `docs/dev/adr/0006-storage-compute-query-stack.md`。
>
> **代码迁移已完成**（2026-07-30 → 2026-08-02）：`ingestion/`、`dags/`、`spark/`、
> `infra/` 均已无 GCP 引用，`infra/terraform/` 整目录已删。**不要新增任何 GCP 代码**，
> CI 每个 PR 跑一遍出口 grep。迁移清单见
> `docs/dev/design/20260726-self-hosted-migration.md`。

---

## Build & run commands

```bash
# Install all Python deps (uses uv)
make install

# Lint Python (ruff) + SQL (sqlfluff)
make lint

# Run unit tests only (no Spark / cloud needed)
make test-unit

# Run full integration tests (requires local Docker stack)
make test-integration

# Submit a Spark job locally
make spark-submit JOB=spark/jobs/etl_weather_archive.py

# Trigger a specific Airflow DAG locally
make dag-trigger DAG=dag_ingest_weather_archive

# Bring up the compute-node stack (Airflow + Spark; MinIO runs on the storage node)
# Always go through the make targets: bare `docker compose -f infra/docker/...`
# resolves ${VAR} interpolation against infra/docker/.env, which does not exist,
# so every ${VAR:?} aborts. The targets wrap `--env-file .env`.
make stack-up
make stack-restart-airflow   # after pulling code changes
make stack-down
```

---

## Repository layout (critical paths)

```
dags/                   Airflow DAG definitions — scheduling logic only
docs/guide/             对外操作手册（English only）— 被根 README 按功能引用
docs/dev/               开发文档：需求 / 架构 / ADR / 笔记（中文可）
config                  #各种配置
ingestion/clients/      Thin API wrappers (Socrata, Open-Meteo, GeoJSON)
ingestion/loaders/      Write raw files to Bronze (s3_loader → MinIO)
ingestion/schemas/      Pydantic models — validate raw API shape before write
scripts/backfill/       # 数据回填脚本
spark/jobs/             PySpark entry points, one file per dataset
spark/transforms/       Reusable transform functions imported by jobs
spark/schemas/          Silver layer StructType definitions
sql/ddl/                CREATE TABLE statements — run once at setup
sql/dml/                Daily incremental loads (MERGE / INSERT OVERWRITE)
sql/intelligence/       Load score + driver + recommendation SQL
contracts/              Source registry and data contracts
ingestion/snapshot/     Daily collection of overwrite-in-place upstreams (BO-7)
infra/docker/           计算节点 Docker 栈（Airflow + Spark）
tests/unit/             Pure Python tests, no Spark or cloud deps
tests/fixtures/         Sample JSON/GeoJSON for mocking API responses
```

---

## Documentation conventions

文档只有两类，写文档前先确认属于哪一类：

| | `docs/guide/` | `docs/dev/` |
|---|---|---|
| 受众 | 外部读者、使用者 | 开发者 |
| 语言 | **English only** | 中文可 |
| 内容 | 平台是什么、怎么用、怎么排障 | 需求、设计意图、ADR、笔记 |
| 引用 | 根 README 按功能引用 | 只被 `docs/README.md` 引用 |

规则：

- 目录名用语义，**不用数字前缀**；数字只用于 ADR 编号（`docs/dev/adr/NNNN-*.md`）。
- 文件名一律 English kebab-case，语言差异只体现在正文，不体现在路径。
- ADR 不改名、不删除；过时了写新的并把旧的标为 `Superseded by NNNN`。
- 一篇文档只属于一类，且必须被 `docs/README.md` 恰好链接一次。
- 表述保持**城市无关**：平台叫 UOIP，城市是配置维度。
  `SRC-NYC-311` 这类 source ID 是 `config/sources/` 里的真实值，照抄不改。
- 个人的周报、排期、prompt 存档不进本仓库。

---

## 城市无关护栏（写 Winnipeg 代码时必须遵守）

平台叫 UOIP，城市是配置维度。现有代码基本已经是城市无关的——**风险不在存量，
在接下来为 Winnipeg 新写的代码**。三条护栏，成本接近零：

1. **业务语义只落配置与数据，不落库代码。**
   渠道归一化映射（`Self Service + Mobile + SMS In → VOF`）、3,563 个 `type`
   取值的解析字典、P1/P2/P3 承诺时限——这类东西进 `config/` 或 Gold 的种子/维度表。
   `ingestion/`、`spark/transforms/`、`dags/` 里**不得出现城市专有字面量**
   （`winnipeg` / `plow_zone` / `neighbourhood` / `borough` / 具体 dataset id）。
   判据：这段逻辑换个城市要不要改？要改就是配置，不是代码。

2. **通用层用角色名，实例名只出现在 Gold 与配置。**
   角色名：服务请求 / 作业分区 / 行政区。实例名：311、plow_zone、ward、borough。
   摄取层与 Spark 通用 transform 一律用角色名或纯技术名；
   `sql/`、`config/sources/*.yaml`、`dim_*` 表可以用实例名——那本来就是每城一套。
   新增的通用能力（如 `snapshot` 分区策略、`dim_geography` 承载多套互不嵌套的
   几何）按能力命名，不按触发它的城市命名。

3. **NYC 存量退役（2026-08-02 起）。** 此前的规定是"NYC 存量不动，它是可移植性的
   实证基线"——该论证依赖跨城市移植（H3）有真实价值。H3 已降级为**只留围栏、
   不留实现**，论证不再成立，且它与"平台可被 Winnipeg 直接复用"（H2）直接冲突。
   决策与判据见
   `docs/dev/requirements/project-overview.md` 的「交付视野」一节。

   可移植性此后由**本节三条护栏 + CI grep 门禁**保证，不由第二份实现保证。

   > ⚠️ **退役的是城市实例，不是能力。** `etl_dcp.py` / `transforms/dcp.py` 承载的
   > GeoJSON → WKT 通路是 BO-4 的 plow zone 边界也要用的，
   > `etl_weather_archive.py` 是 BO-3 的气象通路。**先按角色名泛化出可用的替代实现，
   > 再删城市实例**——不要反过来。删除范围与顺序按 `docs/dev/roadmap.md` Phase D。

> 例外只有一处：`config/sources/` 与 `contracts/` 本就是城市实例的载体，
> 城市名在那里是内容不是污染。

---

## Coding conventions

- **Python**: ruff enforced. Line length 100. Type hints required on all public functions.
- **SQL**: sqlfluff, dialect `trino` only — pinned in `.sqlfluff`, not passed on
  the command line. Keywords UPPERCASE. Table/column names `snake_case`.
- **Naming**:
  - DAG files: `dag_<action>_<dataset>.py` (e.g. `dag_ingest_weather_archive.py`).
    Name after the *dataset*, not the source — one source may carry several
    datasets with different partition strategies, and only some run in Airflow.
  - Spark jobs: `etl_<dataset>.py`
  - SQL DDL: `<table_name>.sql` (matches BigQuery table name exactly)
  - Tests: `test_<module_being_tested>.py`
- **Imports**: absolute paths within the package (`from ingestion.clients.socrata_client import ...`).
  Never relative imports at the top level.
- **Secrets**: loaded via `python-dotenv` from `.env`. Never hardcode credentials.
  Reference `.env.example` for all required keys.

---

## Data architecture rules

- Bronze = immutable raw **gzipped NDJSON** (`.ndjson.gz`; newline-delimited so
  Spark can stream it — plain JSON arrays are not loadable). Never overwrite a
  Bronze file. Manifests stay uncompressed `.json`.
  Partition path: `bronze/raw/<sourceId>/<dataset>/[YYYY-MM]/data_<date>.ndjson.gz`
  ⚠️ The `.gz` extension is mandatory and `Content-Encoding` must never be set —
  Spark's `s3a://` reader picks its codec from the extension and ignores headers,
  so a mislabelled object yields garbled rows **without raising**. ADR 0006 §4.1.
- Silver = cleaned Parquet, partitioned by date.
  All timestamps must be UTC. Use `timestamp_normalizer.py` for all conversions.
- Gold = Hive-partitioned Parquet read by Trino, migrating to Iceberg later
  (ADR 0006 §5). `fact_` tables are partitioned by date and clustered by region.
- `dim_geography` stores WKT strings. Use `ST_Contains` for spatial fill —
  never manual postal-code lookup alone.
- All ETL jobs must be **idempotent**: re-running the same `execution_date`
  produces identical output, no duplicates. Use MERGE or INSERT OVERWRITE PARTITION.

### Bronze partitioning strategies

Each source declares `partition_strategy` in its YAML
(`config/sources/<id>.yaml`). The `BackfillFacade` uses it to choose the
object-storage path layout:

| Strategy | Used by | Path under `bronze/raw/{sid}/{ds}/` |
|---|---|---|
| `daily` | SRC-Open-Meteo | `{YYYY-MM}/data_{YYYY-MM-DD}.ndjson.gz` + `{YYYY-MM}/manifest_{YYYY-MM-DD}.json` (per day) |
| `monthly` (default) | — | `data_{YYYY-MM}.ndjson.gz` + `manifest_{YYYY-MM}.json` |
| `static` | — | `data_static.ndjson.gz` + `manifest_static.json` |
| `snapshot` | SRC-WPG-SNOW | `ingest_date={YYYY-MM-DD}/data.ndjson.gz` + `ingest_date={YYYY-MM-DD}/manifest.json` |

⚠️ 「用它的源」一列仅供参考，**唯一权威是 `config/sources/*.yaml`**。

`daily` requires every dataset to declare a `timestamp_field` (Pydantic
validates this in `ingestion/config/source_config.py`). Records are split
by the date portion of that field; records with missing/unparseable
timestamps are dropped. Each daily shard has a paired
`manifest_YYYY-MM-DD.json` file in the same month folder describing that
day's data.

`snapshot` partitions by **采集日**而非记录日期，是唯一允许
`timestamp_field: null` 的策略。它服务于覆盖式更新、不保留历史的上游——
**漏采一天即永久缺失**，因此：走流式写入（`write_snapshot_stream`，
避免全量物化 OOM）、有 `min_records` 下限保护（小样本拒绝落盘，不覆盖前一天）、
**不进 Airflow**（跑在存储节点，自带告警 + 外部死人开关）。
运维手册见 `docs/guide/snapshot-collection.md`。

---

## Airflow conventions

- DAG files contain scheduling logic only. No business logic, no API calls inline.
- All heavy work is delegated to: `ingestion/`, `spark/jobs/`, or `sql/` scripts.
- Use `execution_date` (not `datetime.now()`) for all incremental window logic.
- Every DAG must have: `retries=3`, `retry_delay=timedelta(minutes=5)`,
  `on_failure_callback` pointing to the Slack/email alert utility.
- Socrata DAGs must implement a 7-day lookback window for late-arriving facts.

---

## What NOT to do

- Do not put business logic inside DAG files.
- Do not use `SELECT *` in any Gold-layer SQL.
- Do not hardcode `execution_date` or date strings in SQL — always use parameters.
- Do not create new utility functions in `spark/jobs/` — put them in `spark/transforms/`.
- Do not commit `.env`, `CLAUDE.local.md`, or any `*.json` credentials file.
- Do not pass S3 credentials through a Spark `--conf` flag — they surface in the
  Spark UI environment page, the process list and Airflow task logs. Use the
  worker's `spark-defaults.conf` (mode 600) or environment injection.
- Do not add new GCP code. The GCS/BigQuery code still in `dags/`, `spark/` and
  `infra/` is debt awaiting removal, not a pattern to copy.

---

## Escalate to human when

- The upstream Socrata API schema has changed (new/renamed fields).
- A Spark job produces a Silver partition with 0 rows (possible API outage).
- A `dim_geography` spatial join returns NULL for > 10% of the records
  **that carry geographic information**. The denominator matters: Winnipeg 311
  is 79% without coordinates upstream, so a whole-table threshold fires forever.
  See `docs/dev/requirements/business-objectives.md` §2.1.
- A snapshot collection fails, or a day's `ingest_date=` partition is missing.
  It cannot be re-collected — see `docs/guide/snapshot-collection.md`.

---

## Implementation status (updated 2026-08-02)

> Project was paused after 2026-07-01 for unrelated academic work.
> This section is the single source of truth for implementation progress —
> `docs/dev/` documents design intent only and does not restate status.

### 自建栈迁移（执行清单：`docs/dev/design/20260726-self-hosted-migration.md`）

**已完成（代码 + 单测，2026-07-30）**：

- **约定文件去 GCP**：`AGENTS.md`（Phase awareness 整节与 `DEPLOYMENT_PHASE` 删除）、
  `.claude/rules/backfill.md`（Composer 整节删除）、`.sqlfluff` 新建钉死 trino、
  Makefile 的 terraform/composer target 全删（计费雷已拆）。
- **存储客户端**：`ingestion/loaders/s3_client.py`（boto3 + path-style + SigV4）
  与 `s3_loader.py`（`S3BronzeLoader`）。`gcs_loader.py` 已删除。
  gzip + `.ndjson.gz` + manifest 的 `compression` / `stored_bytes` 已落地。
  `ingestion/` 与 `scripts/` 已无任何 GCP 引用。
- **`snapshot` 分区策略**：config 校验、loader、facade（`upload_snapshot`）、
  bulk（`backfill_snapshot`）、三张分发表、`config/sources/winnipeg_snow_clearing.yaml`
  (`SRC-WPG-SNOW`)、`scripts/backfill/backfill_wpg_snow.py`。
- **BO-7 采集链路**：`ingestion/snapshot/`（流式写入 + 小样本保护 + 告警/死人开关）
  与 CLI `scripts/collect_snapshot.py`。
- **Stage G3**：`dags/_spark_common.py` 换 `hadoop-aws:3.3.4` +
  `aws-java-sdk-bundle:1.12.262`（版本必须精确匹配 Spark 3.5.1 自带的 Hadoop）、
  `fs.s3a.*` + path-style；S3 密钥走 spark-worker 环境变量注入，**不经 `--conf`**。
  13 个 DAG 与 `spark/jobs/` 的路径字符串全部改 `s3a://` + `.ndjson.gz`。
  `dag_audit_bronze.py` 换 boto3 并新增 snapshot **只读**核对（查
  `ingest_date=` manifest 是否存在，**只报不补**——快照补不回来，
  "补"只会把今天的数据写进昨天的分区，伪造历史）。
  `docker-compose.yml` 两处 SA key 挂载已删。
- **CI 门禁**：`.github/workflows/ci.yml` —— `make lint` + `make test-unit-offline`，
  外加一个跑 Stage G4 grep 的 job，防止 GCP 代码一次一个 import 长回来。
  该 grep **当前输出为空**（`infra/` 除外，见下）。

### 城市实例切换（执行清单：`docs/dev/design/20260802-city-instance-switchover.md`）

**批 0 已完成（2026-08-02）**：

- `39ur-higg` 实测通过 —— **82 个 MultiPolygon / 25 个 `plow_zone` 取值**，
  不是设计文档假设的 22。`X` / `B/D` / `Downtown` 共 10 个多边形在 `tix9-r5tc`
  排班表中无对应记录（约 31% 面积），Gold 层必须显式建模为「无排班分区」。
- 311 去重键定案：`case_id` **不唯一**（1.87% 重复），行粒度是 interaction 不是
  case，**`(case_id, interaction_id)` 才是唯一键**。详见
  `docs/dev/requirements/winnipeg-data-sources.md` §3.9 与 §6.2。
- `docker-compose.yml` 四处硬编码口令全部改为 `.env` 的 `${VAR:?}` 必填变量。

**批 1 已完成（2026-08-02）**：

- 删 13 个纯城市实例文件：3 份 source YAML、3 个 backfill 脚本、7 个 DAG。
- 通用层去字面量：`SocrataClient.domain` 改**必填无默认**（默认值让「忘了配」
  退化成「静默连到别的城市」）；`NYC_UOIP_CONFIG_DIR` → `UOIP_CONFIG_DIR`；
  包名 `nyc-uoip` → `uoip`；docstring 与 bucket 示例全改角色名。
- `dag_audit_bronze` 与 `bronze_profiler` 的硬编码 `(source_id, dataset)` 分发表
  改为**从 registry 按 `partition_strategy` 派生**，新增源不必再改这两处。
- 通用层测试改用 `tests/fixtures/sources/` 的**合成角色源**（每种分区策略一个），
  从此单测不依赖任何已部署城市。发现式断言取代硬编码清单。

**批 2 已完成（2026-08-02，`5ebe272` + `fa886d5`）**：
`SRC-Open-Meteo` 拆成 archive（`daily`）+ forecast（`snapshot`）两个数据集，
四个气象 DAG 按 dataset 而非 source 重命名。

**批 3 已完成（2026-08-02）** —— 新城市源接入与回填：

- 四份 source YAML + 四个 `backfill_*.py`（registry 自动发现，`main.py` 零改动）：
  `SRC-WPG-311`(daily) · `SRC-WPG-PLOW-SHIFT` · `SRC-WPG-PARKING-BAN` ·
  `SRC-WPG-PLOW-ZONE`（后三个 static）。
- 四个 contract 落到 `contracts/api-contracts/winnipeg-*.yaml`，字段名与填充率
  **全部对真实 API 实测**，不是照抄设计文档。
- 每源脚本收敛到 `_common.run_standard_backfill()`；死参数 `--dataset`（解析了
  但从不生效）删除。
- `dag_ingest_service_requests.py`（`0 5 * * *`，7 天回溯）。三个 static 参照表
  **不建 DAG**——一次拉全表，没有调度可言。
- `INGEST_START_DATE` 2026-06-16 → **2026-08-02**（前者是退役实例的部署日）。
- `scripts/backfill/plan_wpg_311_backfill.sh`：8 个雪季窗口 + 1 个全量窗口，
  范围依据 launch 文档 §7.1。

🔴 **批 3 修掉的一个真实缺陷**：平台此前没有 `static` + 普通 Socrata 的组合，
而 `static` 禁止 `timestamp_field`、窗口式 Socrata fetcher 又必须有——两层
配置直接打架，实测表现为该类源**一行都取不到**。修法是
`build_fetcher(..., strategy=...)` 接收数据集的**生效分区策略**，`static` 走
全表拉取。同批修掉 GeoJSON fetcher 只取单页（1000 行封顶、**静默截断**）的隐患。
详见 `.claude/rules/backfill.md`「The strategy also decides *how* a dataset is fetched」。

**批 3.5 已完成（2026-08-02）** —— 上线前代码审查，修掉两个缺陷：

🔴 `upload_window()` / `fetch_window()` **不按生效策略过滤数据集**，
而 `SRC-Open-Meteo` 自批 2 起带两个策略不同的 dataset。后果是每天的
`dag_ingest_weather_archive` 会把预报写进 `ingest_date=昨天/`、
**覆盖存储节点当天真实采集的快照**；历史回填则把逐小时存档写进
`ingest_date=2008-…` 伪造采集历史。两者都不抛异常，且快照不可恢复。
修法：两个方法新增 `strategy=` 关键字（`bulk.py` 显式传 `daily`），
再加一层兜底——`_upload_window` 遇到 snapshot 数据集而没有
`ingest_date_override` 一律抛。只靠传参等于把正确性押在「每个调用点都记得」上。

🟡 `run_standard_backfill` 的 dry-run 分支失败仍退出 0，而 dry-run 正是长回填
前的预检查、`plan_wpg_311_backfill.sh` 又靠非零退出码停下。已与 upload 分支
共用 `_exit_on_failure()`。

审查结论与**分阶段上线执行计划**见
`docs/dev/launch/city-instance-switchover-launch.md` §9–§10。

**批 4–5 未开工**（边界能力泛化 → 语义配置化 + Silver）。

### 指标可用性探针（执行清单：`docs/dev/design/20260808-metric-feasibility-probe.md`）

产出物：`docs/dev/requirements/metric-feasibility-audit.md`（每个指标一行，
数字 + 可重跑入口 + 结论）。探针在 `scripts/analysis/`，一个探针一个模块，
只读公开 API，不依赖 MinIO / Silver。共用取数层 `_probe_common.py`
（气象存档缓存在 `var/probe-cache/`，未跟踪，删了就是全量重取；点在多边形内的
`ZoneIndex` 也在这里，`make_valid` 修复过的分区会报出来不静默）。

**任务 1（BO-3 降雪事件切分）已完成（2026-08-08）** —— 三个交付数全部达标：
选定阈值 **3 cm/日**、事件数 **N = 100**（排班期 59）、ward × 事件非零率 **77.9%**。
**M1 按原设计成立**，不走降级退路。两条要改 BO-3 的口径已记在产出物：
①「事件」实测中位时长 1.0 日，本质是「降雪日」；② 19 次犁雪里 **4 次**
（2019-02-10 / 2021-01-07 / 2021-11-27 / 2022-11-24）在任何阈值下都不落在降雪
事件内，不能表述为「降雪事件驱动犁雪」。

**任务 2（BO-2 顺位反证）已完成（2026-08-08）** —— 顺位**可以**作核心结论：
三条反证全部无法解释掉它（户数 `r=+0.491` 方向相反；分区间降雪极差仅 2.1%、
`r=+0.074`）。`shift_number` = 作业批次序，19/19 事件与 `shift_start` 排序一致。
第四条反证（路网 `ngsx-caav`）**判定不需要接**，依据是 design §6 的条件触发规则。
🔴 但顺位**不是常量**：前后半期 ρ = +0.591，V/M 两个分区移动超过一整个班次——
BO-6 的 0.30 顺位权重不得喂十年均值。
🟡 附带发现：25 个 plow zone 里 **8 个**含 OGC 非法几何，Silver 建
`dim_geography` 前必须 `make_valid`。

**任务 3（BO-6 三项独立性）已完成（2026-08-09）** —— 两条判据全过，**不走退路**，
公式不改残差形式、权重不重分配。三项两两 |r| 最大仅 **+0.460**（请求量 × 天气），
`r(顺位, 请求量) = +0.017`、`r(顺位, 天气) = −0.006`；删掉顺位项在 **15/15** 个
事件上都改变分区排序（ρ 中位 0.41）。三条要改 BO-6 的口径已记在产出物：
① 天气项方差 **99.4% 在事件之间**、仅 0.6% 在事件之内，它决定评分高低而几乎不影响
事件内排序——而 BO-8 消费的正是排序，故不得表述为「天气影响调度建议」；
② 实际影响序是 **顺位(0.300) > 请求量(0.270) > 天气(0.167) 分数单位**，与名义权重
0.40/0.30/0.30 的字面顺序相反；③ 请求量因子的地址数分母是承重的——去掉它
`r(顺位, 请求量)` 会从 +0.017 虚涨到 +0.139。
🟢 顺带交付了任务 5 的头号数字：**311 工单空间命中率 99.9%**（134,123/134,258）。

**BO-3 遗留待办（4 次无降雪犁雪）已查清（2026-08-09）**，探针
`scripts.analysis.plow_without_snowfall`：原记的三条猜想（吹雪风积 / 冻雨 /
单点气象代表性）**全部证伪**——三个比值都 ≤ 0.31 且方向与假设相反。
真实成因是**阈下累积**：21 日累计降雪保留了对照组的 76%，单日峰值只保留 26%
（差 2.92 倍），雪照样下了同量级，只是从没有单日超过 3 cm。
🔴 这是关于**事件定义**的结论：换阈值救不了，BO-3 必须在单日阈值之外再加一条
滚动累积判据，且该改动会连带改变 N、ward × 事件面板与 BO-8 回测次数。

🔴 **同批更正了台账里一处错的名单**：未对齐的 4 次是
**2021-01-07 / 2021-11-27 / 2022-11-24 / 2026-02-26**，不是此前写的
2019-02-10 / …。旧名单取自 `--align-lag-days 3` 的运行，改 lag 7d 时划错了一个。

**任务 4、6、7 未开工**（任务 5 只差「无排班分区工单占比」与 ward 标签一致率）。
接手顺序与判据见 design §3.3。另有一个从任务 2 掉出来的待办：
「后排分区户数更多」的 `r = +0.491` 须在近期窗口上重算（十年均值已被证明会掩盖重排）。

⚠️ 批 1 的出口 grep 现在只剩一处**已知延后**项：`_spark_common.py` 里的
`transforms/dcp` 引用（批 4 改）。除此之外输出为空。

**未完成 —— 接手者从这里继续**：

1. ✅ **BO-7 上线** —— 已于 2026-08-02 完成，见
   `docs/dev/launch/20260802-snapshot-collection-deployment-launch.md`。
2. 🔴 **MinIO 环境未验证**：所有 S3 代码只跑过 mock 单测。
   `tests/integration/`（12 项）在 `S3_*` 缺失时自动 skip，配好后跑
   `make test-integration` 才算真正打通。
3. ✅ **`infra/terraform/` 已删**（`66a1f0d`），GCP service account 密钥也已在
   控制台撤销。本项关闭。
4. **`docs/guide/` 尚未同步**：7 篇手册仍按 GCS/BigQuery 描述系统
   （新增的 `snapshot-collection.md` 除外）。ADR 0006 §8.4 规定手册在代码迁移
   完成后才更新——现在这个前提已满足，可以做了。

验收判据（Stage G4）：下面这条 grep 输出为空。**已达成**。CI 每个 PR 都会跑一遍。

```bash
grep -rniE "gcs|bigquery|google\.cloud|gs://|dataproc|composer|DEPLOYMENT_PHASE" \
  --include="*.py" --include="*.yaml" --include="*.yml" --include="*.toml" \
  --include="*.example" dags ingestion scripts spark tests config .env.example
```

### 各层进度

- **Bronze ingestion** — fully implemented and tested. Entry points:
  `scripts/backfill/` (CLI) + `ingestion/backfill/facade.py`.
  See `.claude/rules/backfill.md` for the 3-layer architecture and dispatch tables.
- **`dags/`** — 6 DAGs: 1 Bronze backfill (manual), **2** Bronze incremental
  （气象存档 + 311 服务请求），1 Bronze audit/self-heal (`dag_audit_bronze`,
  审计目标由 `partition_strategy` 从 registry 派生，不再硬编码),
  1 Silver incremental, 1 Silver backfill.
  批 1 删掉了 7 个纯城市实例 DAG；批 3 只加了 1 个（DAG 数量纪律：回填留 CLI，
  只给活跃源建 ingest DAG，static 参照表不建）。
- **Silver** — 3 jobs exist：`etl_weather_archive.py`（日粒度存档 + BO-3 降雪事件
  切分，有 DAG）、`etl_weather_forecast.py`（snapshot 布局，**故意没有 DAG**——
  Bronze 由存储节点采集，产出在 M1 之前无人消费）与
  `etl_dcp.py`（静态几何，**批 4 泛化后才能删** —— 它是唯一跑通过的
  GeoJSON → WKT 通路，BO-4 的 plow zone 边界要走同一条）。
  `SRC-NYC-311` / `SRC-NYPD` 的 Silver 是**取消，不是推迟**（城市无关护栏 §3）。
- **Compute engine** — Dataproc was abandoned in favour of self-hosted Docker
  Spark Standalone (`spark-master`/`spark-worker`). Storage moved from GCS to
  MinIO on 2026-07-30 (ADR 0006, superseding ADR 0005 §4's "storage stays on GCS").
- **Gold / Trino / intelligence SQL** — not started. `sql/ddl/`, `sql/dml/`,
  `sql/intelligence/` do not exist yet. `.sqlfluff` already pins the dialect to
  trino, so the first file written is linted correctly.
- **Stage T (Hive Metastore + Trino + Superset)** — not started; not needed
  before the Gold layer. Re-check the compute node's free memory before adding
  them (ADR 0006 §2.1 measured 8 GB available).
- **`contracts/`** — 批 3 补齐了四个 Winnipeg 源的契约
  （`api-contracts/winnipeg-{311,plow-shifts,parking-bans,plow-zones}.yaml`），
  字段名、类型、填充率、低基数取值域全部对真实 API 实测。
  两处已知残留：`open-meteo.yaml` 仍写着批 2 已废弃的 dataset 名
  `nyc_weather_forecast`（该源现已拆成 archive + forecast 两份，契约需跟着拆）；
  `AGENTS.md` 引用的 `contracts/source-registry.md` **不存在**。
  `ingestion/schemas/` (Pydantic raw-API models) 从未创建 —— 原始形状校验目前
  只在 `ingestion/config/source_config.py` 里。
- **Dependency resolution** — resolved 2026-07-28. Dev deps now live in a single
  `[project.optional-dependencies] dev` table; the old `[dependency-groups] dev`
  (which carried a phantom `apache-airflow-stubs`, not a real PyPI package, and
  broke every `uv sync` / `uv run`) is gone. Do not re-add that table — two dev
  tables means two conflicting pytest lower bounds. `boto3` was added and
  `google-cloud-storage` retained only until the last GCS call site is gone.
  Gates verified green on 2026-08-02: `make lint` clean,
  `make test-unit` = 383 passed, 2 skipped（批 3.5 后）。

@.claude/rules/backfill.md
