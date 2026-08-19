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
**H1 = the 2026-09-19 conference delivery (Day of Data Winnipeg; moved up 3 days
from the original 2026-09-22), and until end of September it is the
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
config/sources/         Source registry — one YAML per ingested source
config/seeds/           Gold dimension seed CSV (business semantics live here,
                        never in ingestion/ or spark/transforms/)
ingestion/clients/      Thin API wrappers (Socrata, Open-Meteo, GeoJSON)
ingestion/loaders/      Write raw files to Bronze (s3_loader → MinIO)
ingestion/schemas/      Pydantic models — validate raw API shape before write
scripts/backfill/       # 数据回填脚本
spark/jobs/             PySpark entry points, one file per dataset
spark/transforms/       Reusable transform functions imported by jobs
spark/schemas/          Silver layer StructType definitions
sql/ddl/                CREATE TABLE statements — run once at setup
sql/dml/                Gold loads — INSERT OVERWRITE PARTITION only, never MERGE (C6)
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

   > ⚠️ **退役的是城市实例，不是能力。先按角色名泛化出可用的替代实现，
   > 再删城市实例**——不要反过来。删除范围与顺序按 `docs/dev/roadmap.md` Phase D。
   >
   > ✅ 已按此执行完毕：`etl_dcp.py` / `transforms/dcp.py` / `dcp_schemas.py`
   > （`SRC-DCP`，NYC borough GeoJSON）承载的 GeoJSON → WKT 通路先泛化成
   > `etl_plow_zone_boundary.py` + `transforms/geography_boundary.py`，
   > 然后才删。这条规则保留是给**下一次**退役用的。

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
  produces identical output, no duplicates. Use `INSERT OVERWRITE PARTITION`,
  with **one whole day partition as the unit of overwrite** — not `MERGE`.
  `MERGE` on a Hive external table needs Iceberg, and the Iceberg migration is
  ADR 0006 §5's later business, not H1's (decided 2026-08-14, C6/C17).

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

## Implementation status (updated 2026-08-18)

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
`docs/dev/launch/20260803-city-instance-switchover-launch.md` §9–§10。

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

✅ 批 1 的出口 grep 曾经留了一处已知延后项（`_spark_common.py` 里的
`transforms/dcp` 引用），随批 4 泛化 `etl_dcp.py` → `etl_plow_zone_boundary.py` +
`spark/transforms/geography_boundary.py` 一并清掉，NYC 的 `SRC-DCP` 实例代码
（`etl_dcp.py` / `transforms/dcp.py` / `dcp_schemas.py`）已删除。grep 输出现在
是全空，没有已知延后项。

**未完成 —— 接手者从这里继续**：

1. ✅ **BO-7 上线** —— 已于 2026-08-02 完成，见
   `docs/dev/launch/20260802-snapshot-collection-deployment-launch.md`。
2. ✅ **MinIO 环境已验证** —— 生产 MinIO 已完全跑通：Bronze 层全量 backfill
   完毕，每日 ingestion 正常运行中。`tests/integration/`（12 项）本身尚未在
   本地跑过 `make test-integration`——这是套件层面的复核，不是"能不能用"
   的问题；生产已经用真实流量验证过。
3. ✅ **`infra/terraform/` 已删**（`66a1f0d`），GCP service account 密钥也已在
   控制台撤销。本项关闭。
4. ✅ **`docs/guide/` 已同步**（2026-08-18 复核）：9 篇手册全部按 MinIO / Spark
   Standalone / Trino 描述系统。`architecture.md` 仍出现 GCS / BigQuery /
   Dataproc / Composer 四个词，但只在**「被否决的替代方案」一列**里——那是决策
   记录，不是遗留描述，**不要"清理"掉**。本项关闭。

验收判据（Stage G4）：下面这条 grep 输出为空。**已达成**。CI 每个 PR 都会跑一遍。

```bash
grep -rniE "gcs|bigquery|google\.cloud|gs://|dataproc|composer|DEPLOYMENT_PHASE" \
  --include="*.py" --include="*.yaml" --include="*.yml" --include="*.toml" \
  --include="*.example" dags ingestion scripts spark tests config .env.example
```

### Silver / Gold ETL 实施（执行清单：`docs/dev/design/20260817-etl-implementation.md`）

把 S4 建出的 25 张空表填满。六批 E0–E6，每批一个 PR，批间停下等确认。
**不新增也不修改任何 schema —— contract 自 2026-08-13 起冻结。**

**E0 代码部分已完成（2026-08-17）**：

- `sql/dml/` · `sql/intelligence/` · `config/seeds/` 三个目录 + 各一份 README。
- `spark/transforms/zone_assignment.py` —— 按点归作业分区的通用 transform，
  E1/E2 共用。三值 `matched` / `unmatched` / `no_geo` **不塌成 NULL**：
  「没坐标」与「坐标落在所有多边形外」责任人不同，合并就是让告警分母算错
  （上游 79% 无坐标，全表口径的命中率会永远报警然后被静音）。
  空多边形列表**直接抛**，不静默把每行标 `unmatched`——那和真实边界故障
  长得一模一样。

**E1 代码部分已完成（2026-08-17）**：三个 static Silver job（见「各层进度」表）
+ `reference_table.py` / `snapshot_partition.py` 两个通用 transform。
行数断言取**精确值而非下界**：全表拉取变多同样是新闻（上游追加了没人审过的
历史），而 418/49/25 到 Gold 就是硬门禁。

✅ **E0/E1 对象存储侧已执行（2026-08-17）** —— 见
`docs/dev/launch/20260817-etl-implementation-launch.md`。四个 job 对真实 Bronze
跑通，行数 418 / 49 / 25 / 82 精确匹配，跨表 `EXCEPT` 空集，
**空间命中率 237,858 / 237,867 = 99.996%**（`zone_assignment` 首次见真实几何，
含 8 个 `make_valid` 修复过的多边形）。该篇 §2 记了四个环境坑，其中最费时的是
`--conf` 里的 `$S3_ENDPOINT_URL` 在**宿主 shell** 展开成空串、s3a 回退到
`s3.amazonaws.com`，报出 AWS 的 403 而看起来像密钥错。

✅ **E0 遗留已收口（2026-08-18，L1 阶段 D）**：`etl_weather_archive --emit-events`
全量重跑（2000-01-01 → 2026-08-18，阈值 3.0，走
`dag_backfill_silver_weather_archive`）**首次跑通**，3 小时 03 分一次过。
`silver_snowfall_event`（单数）**159 行**；复数旧前缀 `silver/snowfall_events/`
下 **0 个对象**——从未写入成功过，故**无需删除**，本阶段没有执行任何不可逆动作。

**2026-08-18 更正：此前两次「疑似 OOM」的判断是错的。** 真因是 job 1 之后的
`FileOutputCommitter` commit 阶段——`silver/weather_archive/` 有 38,688 个对象，
对象存储无原生 rename，每个都是一次 copy+delete 往返 MinIO，表现为
**日志静默 90 分钟以上且不报错**，两次都被人为中断。3 小时 03 分的总耗时里
job 1 只占 22 分钟，其余约 2.5 小时全在 commit。
判活三件套：`ps -o pid,stat,etime,time,pcpu -C java`（TIME 涨不涨）·
`docker stats`（NET I/O 走不走）· 隔 60s 数两次对象数（变不变）。
🔴 **该成本随分区数线性增长，E 阶段 `service_request` 分区更多，开跑前须评估
`mapreduce.fileoutputcommitter.algorithm.version=2`。**
详见 `docs/dev/launch/20260817-silver-etl-runnable-launch.md` §2 阶段 D2 与 §5。

⚠️ 一处 schema 与实现的张力，已按「不动 schema」化解：
`silver_snow_clearing_address` 的 PK 是 `(plow_zone, snapshot_date)`，语义像
按 `snapshot_date` 分区，但 `sql/ddl/` 里**没有声明 `partitioned_by`**——
真按分区写会生成 `snapshot_date=` 目录、把列从 Parquet 里抽走，Trino 那张
非分区外部表**读出零行而目录看起来是满的**。故实现为扁平全量覆写，一次只存
一个采集日；Gold 本来也只读 `MAX(snapshot_date)`，历史日可用 `--snapshot-date`
从 Bronze 重建。要真做成时间序列，得走变更流程改 DDL。

**E2–E6 未开工。执行已于 2026-08-17 拆成三次上线**，判据是**回滚粒度**而非工程量
（全量回填跑完是既成事实、只能重跑；Gold 分钟级可反复重建）。伞篇
`20260817-etl-implementation.md` 退为需求级总计划，口径不重开：

| 上线 | 覆盖 | design | 状态 |
|---|---|---|---|
| **L1** Silver 全链路跑通 | E2 + 两个 DAG + 全量回填 + 告警端到端验证 | `20260817-silver-etl-runnable.md` | **代码部分已完成（2026-08-17）**，见下 |
| **L2** Gold 维表与事实表 | E3 + E4（9 维 + 5 事实） | `20260817-gold-dimensional-build.md` | 框架 |
| **L3** 评分链与 M1 | E5 + E6（4 表 + DQ 基线） | `20260817-scoring-chain-and-m1.md` | 框架 |

**L2 进行中（2026-08-19）** —— 交接在
`docs/dev/launch/20260819-gold-dimensional-build-launch.md` **§7**，接手先读那节。

- ✅ 阶段 A：`CREATE OR REPLACE TABLE` **在 Hive 连接器上不存在**（`TRUNCATE` /
  `DELETE` 同样 `NOT_SUPPORTED`），design §4.3 的第一条定案已被实测推翻。
  整表重建 = `DROP` → **清 prefix** → `CREATE` → `INSERT`，四步，**清 prefix 不可省**
  （外部表 DROP 不删文件，重建后立刻读到上一代数据），且**不是原子的**。
  规则落 `.claude/rules/gold-sql.md` R4。
- ✅ 阶段 B：`scripts/gold/`（执行器 + 门禁解析）· 4 份 `config/seeds/*.csv` ·
  `dags/dag_gold_build.py` · 33 项单测。`make test-unit-offline` = 828 passed, 2 skipped。
- 🔸 阶段 C：9 张维表的 DML **全部就绪**（3 张种子由执行器从 CSV 生成 + 6 份手写）。
  **一份 DML 都还没对生产跑过**——只过了 sqlfluff 与 `--dry-run` 渲染。
  `dim_service_type` 与 `dim_admin_label` 一样**按年分片**（要枚举全历史 distinct
  `type`，正是 O13 的墙），仲裁顺序与优先级正则由执行器把两份 CSV 渲染成
  **字符串占位符**注入（17 张 Gold 表冻结，没地方落这两份字典）。
- ❌ 阶段 D（5 张事实表）未开工。

四个开放项已结案：**O12**（上述实测）· **O14**（F8 = **141,377** 行，不是 ≈1.6 M）·
**O4**（多命中仲裁改**最具体优先**：SNOW 优先会让 WINDROW 与 ICE_CONTROL 拿到 0 个 type）·
**O10/O11**（已签字）。另更正 design 两处会让门禁永远过不了的数：冬季子集
**256,077 行 / 2.05%**（design 的 275,282 / 1.5% 是上游分子配 Silver 分母），
以及 `ddl_parser.py` 此前**并未**解析 `-- relationships:`。

⚠️ 宿主机 shell 连 Trino 必须加 `TRINO_HOST=localhost TRINO_PORT=8090`——
`.env` 里的 `trino:8080` 是给 Airflow 容器的视角。

关键路径 = **L1 单季 → L1 全量 → L2 事实表**。

**L1 代码部分已完成（2026-08-17）—— 一行生产数据都还没有，别把「代码写完」读成「跑通了」**：

- `spark/jobs/etl_service_request.py` —— 唯一的新 job。按**月前缀**读 Bronze
  （逐日枚举 4,876 天＝开跑前同样多次 HEAD，s3a 视 403 不可重试）、显式传 schema、
  分区键取**本地日**（按 UTC 日会把本地 18:00 后的工单挪到次日）、
  无坐标的一支绕开 UDF 但取值一律引用 `MATCH_STATUS_*` 常量、
  PK **断言不去重**、`partitionOverwriteMode=dynamic`（漏了它
  `mode("overwrite")` 会先删整表再写这一个窗口，且不报错）。
  拒绝行落 `silver/_rejects/service_request/window={start}_{end}/` ——
  最常见的拒绝理由就是「日期不可用」，那种行没有日分区可落。
- 两个 DAG：`dag_silver_service_request`（`0 7 * * *`，7 天回溯）+
  `dag_backfill_silver_service_request`（手动，**拒绝 > 400 天的窗口**）。
  两者都**不显式写 `on_failure_callback`**。
- `scripts/backfill/plan_silver_service_request.sh` —— 窗口划分与 Bronze 侧
  **逐个对齐**（8 个雪季 + 全量段按日历年切），串行。为此给 `_plan_lib.sh` 加了
  `WINDOW_RUNNER` 钩子：checkpoint / 告警 / watchdog 与层无关，Bronze 默认走
  CLI，Silver 走 spark-submit，不复制第二份库。
- `tests/unit/test_etl_service_request.py` —— 20 项，离线本地 Spark。
  时区断言在 **Spark 内**格式化：`collect()` 出来的 timestamp 按**驱动机器**的
  时区渲染，Python 侧比较会变成「取决于谁的笔记本」。

门禁绿：`make lint` 干净，`make test-unit-offline` = 789 passed, 2 skipped。
⚠️ 其中一个 skip 是 `test_dag_imports`（本地没装 airflow），**两个新 DAG 的
import 尚未被任何自动化验证过**——只做了 `py_compile`。

**L1 执行进度（2026-08-18）**：阶段 A（代码）· B（部署）· C（单季门禁）·
D（收 E0 遗留）· **E（全量回填）已完成**。余下 **F（收口）**。判据见
`20260817-silver-etl-runnable.md` §5，实测数字在 launch 篇 §3.2。

E 开跑前的两个前置条件都已解决：`sync_partition_metadata` 生效（H1 实测
分区数 4,878，否则 Trino 侧会是假 0 且不报错）；commit 算法已设为
`algorithm.version=2`。

**Silver 全量首次落地（2026-08-18）**：`silver_service_request`
**12,474,313 行 / 4,878 个日分区 / 拒绝行 0**，全表行数与 Bronze 实测**完全相等**。
PK 唯一性按年核对 2008–2026 全部为 0。

🔴 **对账的分母不是 18.4 M。** 契约的 `full_table_min` 与本文件早先写的
「18.4 M 行」指**上游整表**（18,375,656 @ 2026-08-09），而 Bronze 采集范围
**有意不是全历史**（2016-08-01 起全天，之前只采冬季）。拿 18.4 M 对 Silver
会看到 590 万行的假缺口。

🔴 **全量回填中途暴露了一个 Bronze 数据完整性事故**：窗口式 Socrata 抓取
缺 `$order=:id`，分页边界同时造成重复与丢行，波及 55 天。已修复并验证，
复盘见 `docs/dev/postmortem/bronze-socrata-pagination-incident.md`。
两条结论对后续有约束：① **重复与丢失在行数上相互抵消**，所以扫描与对账
两种检查缺一不可；② `dag_audit_bronze` 只核对分区存在性、核不到内容，
三层校验方案已写在复盘附录，列为 L2 的 O8。

两块被伞篇漏掉、现已归位的工作：① **DAG 失败告警**
（`20260816-failure-alerting-and-followups.md`）—— 代码已于 `ba43372` 落地
（见下「DAG 失败告警」小节），L1 只欠一次端到端验证；② **Gold 侧一个 DAG 都没有**
—— 17 张表怎么触发、日期参数怎么传完全未设计，是 L2 细化的第一件事。

### DAG 失败告警（`ba43372`，2026-08-16）

CLAUDE.md 的 Airflow 约定要求每个 DAG 带 `on_failure_callback`，
而该能力**此前根本不存在**——`dag_backfill_silver_weather_archive` 连续失败
12 天无人知晓。现已实现，引用旧文档时注意时点：

- `dags/_alerts.py`：`alert_on_failure`（Discord，`BACKFILL_ALERT_WEBHOOK_URL`
  回落 `SNAPSHOT_ALERT_WEBHOOK_URL`）+ `ping_watchdog`
  （`AIRFLOW_WATCHDOG_URL`，**无回落**——在 snapshot 没跑的那天替它签到，
  会压掉那个 check 唯一存在的理由）。
- 挂载点是 `_dag_common.DEFAULT_ARGS`，**一处生效、覆盖全部 DAG，包括还没写的**。
  🔴 新建 DAG **不要**显式写 `on_failure_callback`——写了就是把它覆盖掉。
- `dags/dag_smoke_alert.py`：故意失败的手动 DAG（`retries=0`，不写任何数据），
  给「不能靠看起来配好了代替」的人工验证用。

**仍欠两条**：① 端到端验证从未跑过（L1 launch 阶段 C5）；
② 死人开关未注册，`AIRFLOW_WATCHDOG_URL` 为空时 `ping_watchdog` 静默跳过
（这是设计，不是 bug）。批 3「日志噪音」（`scripts/` 挂在 `plugins/` 下被
Airflow 逐个 import，每次任务刷 15 行无关 ERROR）未做，等回填跑完。

### 各层进度

- **Bronze ingestion** — fully implemented and tested. Entry points:
  `scripts/backfill/` (CLI) + `ingestion/backfill/facade.py`.
  See `.claude/rules/backfill.md` for the 3-layer architecture and dispatch tables.
- **`dags/`** — 9 DAGs: 1 Bronze backfill (manual), **2** Bronze incremental
  （气象存档 + 311 服务请求），1 Bronze audit/self-heal (`dag_audit_bronze`,
  审计目标由 `partition_strategy` 从 registry 派生，不再硬编码),
  **2** Silver incremental, **2** Silver backfill（气象存档 + 311 服务请求，
  后一对是 L1 新增、尚未跑过），
  1 告警冒烟 (`dag_smoke_alert`, `schedule=None`, 故意失败, 不写数据).
  三个下划线开头的是共用模块不是 DAG：`_dag_common` · `_spark_common` · `_alerts`.
  批 1 删掉了 7 个纯城市实例 DAG；批 3 只加了 1 个（DAG 数量纪律：回填留 CLI，
  只给活跃源建 ingest DAG，static 参照表不建）。
- **Silver** — 7 jobs。**已在生产跑出数据的只有 2 个**，其余五个是
  「代码写完 + 单测过，尚未对真实数据跑过」——两者不要混为一谈：

  | job | 状态 |
  |---|---|
  | `etl_weather_archive.py` | ✅ 生产有数据（日粒度存档 + BO-3 降雪事件切分，有 DAG） |
  | `etl_weather_forecast.py` | 代码就绪。**故意没有 DAG**——Bronze 由存储节点采集，产出在 M1 之前无人消费 |
  | `etl_plow_zone_boundary.py` | 代码就绪（批 4 对 `etl_dcp.py` 的泛化，后者已删） |
  | `etl_plow_shift.py` | E1 新增（2026-08-17），未跑 |
  | `etl_parking_ban.py` | E1 新增（2026-08-17），未跑 |
  | `etl_snow_clearing_address.py` | E1 新增（2026-08-17），未跑 |
  | `etl_service_request.py` | L1 新增（2026-08-17），未跑。**关键路径起点**，18.4 M 行 |

  `SRC-NYC-311` / `SRC-NYPD` 的 Silver 是**取消，不是推迟**（城市无关护栏 §3）。

  三个 static 参照表**不建 DAG**（DAG 数量纪律）：全量覆写，没有调度可言，
  Bronze 重新拉取后手动跑一次。

  共用 transform：`zone_assignment.py`（按点归作业分区，三值 `geo_match_status`，
  E1/E2 共用**同一份实现**——BO-4 的空间命中率只有在两边用同一套修复过的几何
  数着才可比）、`reference_table.py`（全表拉取型 Silver 的公共形状）、
  `snapshot_partition.py`（`ingest_date=` 布局的路径解析）。
- **Compute engine** — Dataproc was abandoned in favour of self-hosted Docker
  Spark Standalone (`spark-master`/`spark-worker`). Storage moved from GCS to
  MinIO on 2026-07-30 (ADR 0006, superseding ADR 0005 §4's "storage stays on GCS").
- **Gold / Trino / intelligence SQL** — **S4 已完成（2026-08-14）**：
  `sql/ddl/` 25 个文件（8 Silver + 17 Gold）+ `spark/schemas/` 五个新 StructType 模块，
  与 22 份 contract 由 `tests/unit/test_contract_ddl_schema_consistency.py`
  做三方一致性校验（契约为权威，177 项断言）。执行入口
  `scripts/ddl/apply_ddl.py`（`make ddl-create` / `ddl-smoke` / `ddl-teardown`）。
  `sql/dml/` · `sql/intelligence/` · `config/seeds/` 三个目录**已建但只有 README**
  （E0，2026-08-17）——17 张 Gold 表一行数据都还没有。
  三条写 DML 前必须先定的口径**已在 20260814 篇定案**，本篇照做不再讨论：
  Gold 增量与幂等 = `INSERT OVERWRITE PARTITION`（覆盖单位是一整天的分区，
  不用 `MERGE`）· `silver_service_request` 保持日分区 +
  `repartition(N, "open_date_local")`，每个日分区恰好 1 个文件 ·
  C1/C2 已统一为单数 `snowfall_event`。
  执行计划见 `docs/dev/design/20260817-etl-implementation.md`（E0–E6 六批）。
- **Stage T (Hive Metastore + Trino + Superset)** — **已就绪（2026-08-04 前后部署，
  2026-08-14 确认）**，但**不在本仓库的 compose 栈里**：它们是计算节点上的平台级
  共享服务，与 Hadoop / Kafka / Flink 同级，可被其他项目共用。定性见
  **ADR 0006 §9**（2026-08-14 增补）。
  连接参数走 `.env` 的 `TRINO_HOST/PORT/USER/CATALOG`（`TRINO_HOST` 必填无默认）。
  schema 按分层切：`hive.uoip_silver`（8 表）+ `hive.uoip_gold`（17 表）；
  `sql/ddl/*.sql` 不写 catalog/schema 限定名，由 `scripts/ddl/apply_ddl.py`
  连接时注入。
  ⚠️ 代价：`make stack-up` 之后仍然建不了表，本仓库不再能独立拉起。
  Grafana 是唯一尚未部署的组件。计算节点内存已从 8 GB 可用降到 **7 GB**。
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
  Gates verified green on 2026-08-17: `make lint` clean,
  `make test-unit-offline` = 769 passed, 2 skipped（E1 后）。

@.claude/rules/backfill.md
@.claude/rules/gold-sql.md
