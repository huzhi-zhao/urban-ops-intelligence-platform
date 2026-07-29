# NYC-UOIP 接手交接文档

> **写给谁**：两个月没碰这个项目、需要在 30 分钟内重建上下文的人（包括未来的你自己）。
> **代码状态基准**：`main` @ `40d9d23`（2026-07-01，最后一次提交）。本文写于 2026-07-24。
> 单一事实来源是代码；本文如与代码冲突，以代码为准并请更新本文。

---

## 1. 三十秒结论

Bronze 层（原始数据落 GCS）**完全做完了**，而且做得比计划扎实——有增量调度、
有手动回填、还有一个每天自愈补洞的 audit DAG。Silver 层**做完了 4 个数据源里的 2 个**
（天气 + 行政区边界），这 2 个是简单的；难的两个（311、NYPD）还没动。
Gold 层 / BigQuery / 运营负荷分 / Dashboard **一行代码都没有**。

按原始 12 周计划算，进度大约在 **Week 7 的位置**，即整个 Phase 1 的一半。

**环境和质量门禁已于 2026-07-24 修复并验证**（见第 6 节）：
`make lint` 全绿，`make test-unit` **253 passed, 2 skipped**。

---

## 2. 当前分层进度

| 层 | 状态 | 说明 |
|---|---|---|
| **Bronze**（GCS 原始 NDJSON） | ✅ 完成 | 4 个数据源全通，含增量 + 回填 + 每日 audit 自愈 |
| **Silver**（GCS Parquet） | 🟡 2/4 | ✅ Open-Meteo、DCP；❌ **311、NYPD 未开始** |
| **Gold**（BigQuery 星型模型） | ❌ 未开始 | `sql/ddl/`、`sql/dml/`、`sql/intelligence/` 三个目录都不存在 |
| **Intelligence**（负荷分/建议） | ❌ 未开始 | — |
| **Dashboard**（Looker Studio） | ❌ 未开始 | — |
| **CI/CD**（GitHub Actions） | ❌ 未开始 | 没有 `.github/` 目录 |

### 已落地的数据（截至 2026-06-16 的审计报告）

`reports/bronze_quality_report.md`：311 已有 **1,659,723 条**记录、151 个分区，
时间覆盖 2026-01-01 → 2026-05-01，时间戳零解析错误、零异常值。
Borough 字段干净（只有 81 条 `Unspecified`）。这份报告是写 311 Silver 清洗逻辑的输入。

---

## 3. 架构上你可能记错的四件事

这四点是两个月里最容易失忆、且会导致你走错路的地方。

### 3.1 Dataproc 已经被放弃了 —— 即使在 Phase 1

原计划 Phase 1 用 GCP Dataproc 跑 Spark。**实际没有**：Dataproc 节点注册失败率太高，
改用自建的 Docker Spark Standalone（`spark-master` / `spark-worker`，
在 `infra/docker/docker-compose.yml` 里）。

**存储层没变**，Bronze/Silver 仍在 GCS。只是算力从 GCP 换到了自己的 Ubuntu 机器。
Terraform 里还留着 `dataproc_editor` 的 IAM 绑定，那是历史残留。

细节见 `docs/01-architecture/decisions/week3-Silver-Execution-Architecture.md` §4。

### 3.2 Cloud Composer 从来没真正开起来

`infra/terraform/main.tf` 里声明了 `google_composer_environment.main`，
但它**不在 `terraform.tfstate` 里**——没 apply 过，或者已经 destroy 了。

**好消息：现在没有任何 Composer 账单在跑。** Airflow 跑在本地/自建 Docker 上。
如果哪天要用 Composer，记住 ~$10/天，用完立刻 destroy。

### 3.3 Bronze 文件是 NDJSON，不是 JSON

`.ndjson`（每行一条记录），不是 JSON 数组。这不是风格选择——BigQuery 的
`LOAD DATA` 和 `spark.read.json()` 都只吃换行分隔格式。
踩坑记录在 `docs/09-ProjectManagement/week3/LessonLearned.md`（6 个连续的建表报错）。

### 3.4 DCP 几何体存的是 WKT，不是 GeoJSON

ADR 里原本决定"存 GeoJSON string，不引入 Shapely"，**实现时推翻了**：
最终是 Shapely UDF 转 WKT，字段名 `geometry_wkt`，下游用 `ST_GEOGFROMTEXT`。
（ADR §7.2 已在 2026-07-24 更正，原决策作为废弃方案保留在文档里。）

代价：这是全项目**唯一一个 Python UDF**，导致 `spark-worker` 必须装 Shapely +
Python 3.11（`Dockerfile.spark-worker` 从源码编译 Python，因为宿主机是 arm64、
deadsnakes 没有 arm64 包），且要配 3 个额外的 `--conf`。
git 历史里 `fix1..fix9: spark + shapely` 那 9 个提交就是在踩这些坑。
所有原因都写在 `dags/_spark_common.py` 的注释里——**动 Spark 配置前先读那个文件**。

---

## 4. 13 个 DAG 分别是干什么的

| DAG | 调度 | 作用 |
|---|---|---|
| `dag_ingest_nyc_311` | `0 6 * * *`, catchup | Bronze 日增量 |
| `dag_ingest_open_meteo` | `0 6 * * *`, catchup | Bronze 日增量 |
| `dag_ingest_nypd` | `0 6 1 * *`, catchup | Bronze 月增量 |
| `dag_ingest_dcp` | None | 静态源，按需刷新 |
| `dag_backfill_nyc_311` / `_nypd` / `_open_meteo` / `_dcp` | None, Params | 手动指定 `[start, end)` 回填 |
| `dag_audit_bronze` | `0 8 * * *` | **自愈**：扫 GCS manifest 找洞（近 14 天 / 3 个月），直接调 `bulk.py` 补，补不上就报错重试 |
| `dag_silver_open_meteo` | `0 7 * * *`, catchup | Silver 日增量，7 天滑动窗口吸收预报修正 |
| `dag_backfill_silver_open_meteo` | None, Params | Silver 任意宽窗口回填 |
| `dag_backfill_silver_dcp` | None | Silver 静态全量覆盖 |

**核心设计约定**：1 个 DAG Run = 1 个时间窗口。**Airflow 不负责切片**——
Bronze 由 `scripts/backfill/bulk.py` 切，Silver 由 Spark job 自己的
`[start, end)` 参数决定。所以"回填一年"是一次 DAG Run，不是 365 次。

---

## 5. 关键路径与命名（照抄用）

- **GCS bucket**：`nyc-uoip-prod`（同时也是 GCP project id）。
  ⚠️ 老文档里的 `nyc-uoip` / `nyc-uoip-bronze` 都是错的，已于 2026-07-24 统一修正。
- **Bronze**
  - daily：`bronze/raw/{sid}/{ds}/{YYYY-MM}/data_{YYYY-MM-DD}.ndjson` + 同名 `manifest_*.json`
  - monthly：`bronze/raw/{sid}/{ds}/data_{YYYY-MM}.ndjson`
  - static：`bronze/raw/{sid}/{ds}/data_static.ndjson`
- **Silver**：`silver/weather/date=YYYY-MM-DD/`、`silver/borough_boundaries/`
  ，被拒绝的行进 `silver/_rejects/{dataset}/`
- **真实 source ID**（只有这 4 个，别自己编）：
  `SRC-NYC-311`、`SRC-NYPD`、`SRC-Open-Meteo`、`SRC-DCP`
  —— 权威定义在 `config/sources/*.yaml`。
  注意 `SRC-NYPD` 下面有 **4 个 dataset**（collisions / complaint_historic /
  complaint_current / shooting_incident），不止 collisions。

---

## 6. 环境与质量门禁（已修复，2026-07-24）

停更期间累积了 **两个** 互相叠加的环境阻塞，现已全部解决：

1. **幽灵依赖**：`pyproject.toml` 的 `[dependency-groups] dev` 里写了
   `apache-airflow-stubs`——这个包在 PyPI 上不存在，导致 `uv sync` / `uv run`
   在依赖解析阶段就失败。已删除该表，dev 依赖统一收敛到
   `[project.optional-dependencies]`（原来两处都声明 pytest，下限还不一致）。
   要 Airflow 类型提示就直接装 `apache-airflow` 本体，它自带 inline types。
2. **仓库被移动过**：目录从 `Workspace/jimmy-pink/` 改成了
   `Workspace/huzhi-zhao/`，但 `.venv/bin/` 里的 console script（`pytest`、
   `sqlfluff`）shebang 硬编码着旧的绝对路径，于是 `pytest` 明明 `which` 得到
   却 spawn 失败（`No such file or directory`）。
   Makefile 已改成走 `uv run --extra dev python -m <tool>`，绕过 shebang，
   顺便也不再要求你手动激活 venv。**根治方式是重建 venv**：
   `rm -rf .venv && uv sync --all-extras`（我没执行，删除操作留给你决定）。

现在的状态：

```bash
make lint              # All checks passed
make test-unit         # 253 passed, 2 skipped
make test-unit-offline # 252 passed（跳过唯一一个联网测试，可离线跑）
```

顺带修掉的：47 个历史 ruff 报错（32 个自动修 + 15 个手工），以及
`gcs_loader.py` 里已废弃的 `datetime.utcnow()`（14 条 DeprecationWarning 清零）。

### 关于那个曾经失败的测试

`test_api_structure.py::test_311_api_returns_valid_structure` 一开始是**红的**，
报 `Missing required fields: ['latitude', 'longitude']`。

**这不是上游 schema 变更**（我核实过：抽 50 条，9 个字段全部存在，
`latitude`/`longitude` 出现在 49/50 条）。真实原因是 Socrata 对空值**直接不返回 key**，
而这个测试只抽 `limit=1` 条就断言这两个字段必须存在——等于每次跑都在赌
那一条记录有没有被 geocode。已修正：坐标字段移入 `OPTIONAL_FIELDS`，
抽样量提到 50，并改成聚合报告（"50 条里 0 条有该字段"才是真的 schema 变更）。

该测试也是 `tests/unit/` 里唯一会联网的，违反 CLAUDE.md 对 unit 测试的定义。
已打 `@pytest.mark.network` 标记，可用 `make test-unit-offline` 排除。

---

## 7. 建议的下一步（按优先级）

1. ~~修依赖，跑通质量门禁~~ ✅ 已完成。
2. **写 311 的 Silver 层**（最大的一块，也是解锁 Gold 的前提）。
   照 `docs/01-architecture/decisions/week3-Silver-Execution-Architecture.md` §5 的
   四步复用模板抄 weather 的结构。清洗规则的输入是 `reports/bronze_quality_report.md`
   里已经做好的字段剖析——**不用重新探查数据**。
   注意 311 的坑：7 天回溯窗口会产生重复，去重键要定清楚。
3. **写 NYPD 的 Silver 层**。最容易出错的点是 `crash_date` + `crash_time`
   两个字段拼成一个 UTC 时间戳（原计划文档特意提醒过这里要多留时间）。
4. **补 `contracts/`**。现在只有 `open-meteo.yaml`，另外 3 个源没有契约文件。
   AGENTS.md 要求"写 Spark 代码前先读 contracts/"，但实际无可读——先补上再写 2、3 步。
5. **Gold 层**：建 `sql/ddl/`，重点是 `dim_geography` 的 `GEOGRAPHY` 字段和
   `ST_CONTAINS` 空间填充（这是整个项目技术上最有含量的一环）。
   ⚠️ 开工前先解决第 8 节的 BigQuery project 错位问题。

---

## 8. 已知的坑与技术债

### 🔴 未解决 —— 动 Gold 层之前必须先处理

| 问题 | 位置 | 影响 |
|---|---|---|
| **`make terraform-apply` 会开出 Composer，约 $10/天** | `terraform plan` 实测：`1 to add`，唯一新增就是 `google_composer_environment.main` | 项目现在根本不用 Composer。**别裸跑 apply**，要么 `-target` 指定资源，要么用完立刻 `terraform destroy -target=google_composer_environment.main` |
| **BigQuery dataset 在旧 project** | state 里是 `projects/pace-lab-bdp/datasets/nyc_uoip`，其余资源都在 `nyc-uoip-prod`。因为资源块没写 `project`，Terraform **不报任何 diff**，会一直静默保持错的那个 | SA 的 BigQuery IAM（`bigquery_data_editor` / `job_user`）只授在 `nyc-uoip-prod`，所以往 `pace-lab-bdp.nyc_uoip` 写会**权限报错**。修法：给资源块加 `project = var.project_id`；⚠️ 该字段会 force replacement，先 plan 并确认 dataset 是空的 |
| `ingestion/schemas/` 目录不存在 | — | CLAUDE.md/AGENTS.md 都提到它（Pydantic 校验原始 API 形状），实际只有 `ingestion/config/source_config.py` 在校验**配置**，对 **API 响应**没有任何校验 |
| `contracts/` 只有 1/4 | 只有 `open-meteo.yaml` | AGENTS.md 要求"写 Spark 前先读 contracts/"，实际无可读 |
| 无 CI | 无 `.github/` | 质量门禁全靠手动 `make lint` |
| `terraform.tfstate` 无远程 backend | `infra/terraform/` | state 只在这台机器上，丢了就得 import 重建 |
| SA key 明文落盘 | `infra/terraform/keys/nyc-uoip-sa-key.json` | 已被 `.gitignore` 挡住、确认未进版本库；但本地明文存在，别误传 |
| venv 里的 console script 指向旧路径 | `.venv/bin/pytest` 等 | Makefile 已绕开；根治要 `rm -rf .venv && uv sync --all-extras` |

### ✅ 本次（2026-07-24）已修

| 问题 | 修法 |
|---|---|
| `apache-airflow-stubs` 幽灵依赖导致环境装不上 | 删除 `[dependency-groups]`，dev 依赖收敛到一处 |
| `make lint` / `test-unit` 用裸命令，PATH 上没有 | 改走 `uv run --extra dev python -m <tool>` |
| 47 个历史 ruff 报错 | 32 自动修 + 15 手工；E402/N811 改为带说明的 per-file-ignore |
| `test_dcp_transforms.py` 里重复的 schema 块 + 6 个别名 import | 删掉未使用的那份，统一用真名 |
| 3 处死变量（`cfg`/`sname`/`geojson`/`schema`） | 删除 |
| `datetime.utcnow()` 已废弃 | 抽成 `_utc_now_naive()`，**刻意保持 naive** 以不改变 Bronze manifest 的落盘格式 |
| 311 契约测试是掷硬币（`limit=1` 断言可空字段） | 坐标移入 optional，抽样 50 条，聚合断言 |
| `DCP_RAW_SCHEMA` 的 docstring 声称"必须用它防止类型漂移"，实际无人使用 | 文档改为如实描述（这是 AGENTS.md "必须传 schema" 的唯一豁免，原因写在文件里）。常量本身保留待定：要么接上只管标量字段，要么删掉 |
| Makefile 的 `GCP_PROJECT` 指向旧项目 `pace-lab-bdp` | 改为 `nyc-uoip-prod` |

---

## 9. 想深入某个话题看哪份文档

| 想知道什么 | 读哪里 |
|---|---|
| 每个 session 的强制约定（AI 和人都适用） | `CLAUDE.md` + `AGENTS.md` |
| 回填三层架构、dispatch 表、DAG 清单 | `.claude/rules/backfill.md` |
| Silver 层怎么真正跑起来（Airflow/Driver/Master/Worker 职责边界） | `docs/01-architecture/decisions/week3-Silver-Execution-Architecture.md` |
| Silver 清洗规则怎么定的方法论 | `docs/01-architecture/decisions/week3-Build_Silver_Layer.md` |
| BigQuery 建外部表踩的 6 个坑 | `docs/09-ProjectManagement/week3/LessonLearned.md` |
| Bronze 数据实际长什么样（字段 null 率等） | `reports/bronze_quality_report.md` |
| 原始 12 周排期 | `docs/09-ProjectManagement/project-management.md` |
| 回填命令照抄 | `docs/03-datasources/backfill-comands.md` |
| Spark 配置为什么长这样（3 个反直觉的 conf） | `dags/_spark_common.py` 的注释 |
