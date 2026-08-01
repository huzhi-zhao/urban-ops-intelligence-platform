# 自建栈迁移执行清单

> **一次性文档**，迁移完成后删除（连同 `docs/README.md` 里的链接）。
> 本篇只讲**做什么、什么顺序、怎么算做完**；为什么这么选见
> [ADR 0006](../adr/0006-storage-compute-query-stack.md)。
> 制定于 2026-07-30，代码尚未开始改动。

---

## 0. 结论先行

代码面约 30 个文件涉及 GCP，但**关键路径只有一条**：

```
MinIO 起服务  →  S3 loader + gzip  →  snapshot 分区策略  →  BO-7 每日快照上线
                                                              ↑ 止血完成
```

其余全部（Spark 侧改造、Terraform 退役、DAG 字符串替换、Trino/Superset 接入）
**不阻塞任何采集**，可以在快照跑起来之后从容做。

**因此执行顺序的第一原则是：不要为了"迁干净"而推迟 BO-7 上线。**
BO-7 是全项目唯一每天都在损失价值的事项，其余都是存量债务，债务不会增长。

---

## 1. Stage S · 止血

按"是否每天在流血"排序，不按工作量排序。

### S1 · BO-7 每日快照采集上线 🔴 每推迟一天永久少一天

唯一真正的出血项。依赖 Stage N 的最小子集（S3 写入 + gzip + snapshot 路径 +
流式），但**不依赖整个迁移完成**。

允许"先糙后精"：实现可以是存储节点上一个独立脚本 + cron，不必先接进
`BackfillFacade`。但有一个硬前提——

> **路径布局与 manifest 字段必须一次定死。**
> 实现可以后续重写，已经落盘的数据不能重写。先把
> `bronze/raw/{sid}/{ds}/ingest_date=YYYY-MM-DD/data.ndjson.gz` +
> `manifest.json`（含新增的 `compression` / `stored_bytes`）确定下来，
> 再动手写代码。

配套（同批做完，否则等于没上线）：

- **失败通知自带**：不在 Airflow 上，没有现成的 `on_failure_callback`。
- **死人开关**：cron 最常见的死法是**根本没跑**（机器重启、cron 被清），
  此时没有活着的进程去发失败通知。用外部"按时打卡、超时反向告警"的服务
  （healthchecks.io 一类）兜住这一类。
- **计算节点侧一个只读核对任务**：每天检查昨天的 snapshot manifest 是否存在。
  这正是 `dag_audit_bronze.py` 已有的模式（按精确路径检查 manifest 存在性），
  复用成本低。注意它只**核对**不**补采**——快照漏了补不回来。

### S2 · 清掉会让 AI 继续写 GCP 代码的约定文件 🟠

`docs/dev/` 已经全部改成自建栈，但**强制约定文件还在讲 GCP**，下一次会话会照着
写 GCS 代码。这是"agent 出血"，修正成本极低。

- `CLAUDE.md` — ✅ **已于 2026-07-30 处理**：Project identity 改为目标栈 +
  存量警告，并新增「城市无关护栏」。剩余 GCP 陈述（Bronze 路径示例、
  Composer 部署流程、Implementation status）随 Stage G 一并改。
- `AGENTS.md` — 待改：Phase awareness 整节作废（`DEPLOYMENT_PHASE` 已废除）、
  SQL dialect 只留 trino、`GOOGLE_APPLICATION_CREDENTIALS` 条目删除。
- `.claude/rules/backfill.md` — 待改：整节「Cloud Composer deployment」删除
  （含那段 `$10/天` 警告——资源都不存在了，警告本身成了噪音）、
  GCS 路径改 MinIO、新增 `snapshot` 行到分发表。

### S3 · `.sqlfluff` 钉死 Trino 方言 🟠 一次性、零成本

当前仓库**没有 `.sqlfluff`**，而 `Makefile` 硬编码了 `--dialect bigquery`。
`sql/` 目录还不存在，所以现在做是零成本；等第一段 SQL 写出来再做，就是返工。

- 新建 `.sqlfluff`：`dialect = trino`
- `Makefile` 的 `lint` target 去掉 `--dialect bigquery`（改由配置文件决定）

### S4 · 拆掉 `terraform apply` 的计费雷 🟠

`make terraform-apply` 当前会创建 `google_composer_environment.main`
（约 $10/天）。完整退役在 Stage G，但**删 Makefile target 只要一分钟**，
可以先拆引信。

### S5 · 撤销 GCP 服务账号密钥 🟡 安全

`infra/terraform/keys/nyc-uoip-sa-key.json` 未入 git（已核实
`infra/terraform/.gitignore` 覆盖 `keys/`、`*.tfstate`，根 `.gitignore` 覆盖
`.env`），但它是一份**仍然有效的凭证**，且被 `docker-compose.yml` 挂载两处。
项目既然弃用 GCP，应在控制台**撤销该 key**，再删本地文件。

> 撤销需要在 GCP 控制台操作，请自行完成——我不碰凭证。

### S6 · 补 CI 门禁 🟡 在 1,835 万行回填之前

`.github/` 不存在。311 全量回填是一次长时间操作，期间的代码改动最好有
PR 门禁兜底。一个跑 `make lint` + `make test-unit-offline` 的 workflow 即可。
优先级低于 S1–S5，但应在 Phase 2W 的大回填开始前完成。

---

## 2. Stage G · 去 GCP

原则：**分批提交，每批独立通过 `make lint` + `make test-unit-offline`**。
不要一个大 commit 改 30 个文件。

### G1 · 纯删除（无替代物，最先做）

| 对象 | 说明 |
|---|---|
| `infra/terraform/` | 整个目录：`*.tf`、tfstate 及 5 份 backup、`.terraform/` provider 缓存、`keys/`、`terraform.tfvars` |
| `Makefile` | `terraform-*` 4 个 target、`deploy-composer`、`set-composer-secret`、`COMPOSER_*` / `GCP_PROJECT` 变量、help 文本里的对应段落 |
| `.env.example` | `GOOGLE_APPLICATION_CREDENTIALS`、`DEPLOYMENT_PHASE`、`GCS_BUCKET_NAME`（后者由 S3 系列替代，见 G2） |
| `infra/docker/docker-compose.yml` | keys 卷挂载 2 处（airflow `../../infra/terraform/keys:/opt/airflow/keys:ro`、spark-worker 的单文件挂载）、文件头的 "Phase 1 / GCS" 注释 |
| ~~`docs/dev/notes/bigquery-external-table-pitfalls.md`~~ | **本行作废**：文档侧不删除，改为归档。两篇 BigQuery 笔记移入 `docs/dev/archive/` 并加失效说明，`notes/` 目录整体撤销——见 [docs/dev/README.md](../README.md) |

⚠️ 删 `infra/terraform/` 时**连同 `.gitignore` 一起删**会让残留的 `keys/`
变成未被忽略的未跟踪文件。正确顺序：先撤销 key（S5）→ 删整个目录（含密钥文件）。

`terraform destroy` 可跑可不跑：资源会随额度到期失效，且已决定不保留 GCS 存量
（[ADR 0006 §7](../adr/0006-storage-compute-query-stack.md)）。跑一次更干净。

ADR 不删不改名：`0001-terraform-and-secrets.md` 在文件头标
`Superseded by 0006`，并同步 `docs/dev/adr/README.md` 的状态列。

### G2 · 存储客户端替换（核心批次）

新建 `ingestion/loaders/s3_loader.py`（类 `S3BronzeLoader`），**从
`gcs_loader.py` 复制而非重写**——该文件 419 行里只有两处碰存储：

- `__init__` 的 client 构造（`storage.Client()` → boto3/minio client）
- `_upload()`（`blob.upload_from_string` → `put_object`）

其余原样保留：`ManifestEntry`、`_to_ndjson`、`_group_by_date`、`_date_range`、
`_make_manifest`、三个 `write*` 方法的路径构造。这也是 ADR 0006 §8.2 的判断。

同批改动：

- **gzip**：`_to_ndjson` 的输出经 gzip 后上传，文件名加 `.gz`。
  `sha256_checksum` / `file_size_bytes` 继续描述**未压缩载荷**，
  新增 `compression` / `stored_bytes` 两个 manifest 字段（ADR §4.2）。
  ⚠️ **不要设 `Content-Encoding`**（ADR §4.1）。
- `ingestion/backfill/facade.py`（18 处）：`gcs_bucket` → `bucket`、
  `gcs_client` → `client`、import 与 docstring 里的 `gs://` 路径。
- `scripts/backfill/_common.py`（6）、`scripts/backfill/bulk.py`（9）、
  `scripts/profiling/bronze_profiler.py`（6）：机械替换。
- 测试：`tests/unit/test_gcs_loader.py` → `test_s3_loader.py`；
  `tests/unit/test_backfill_facade.py`（41 处 mock 名）；
  `tests/unit/test_backfill_{bulk,scripts}.py`。
  集成测试 3 个文件改指向本地 MinIO 容器——**这是净收益**：
  MinIO 可以真实往返，比 mock GCS 更接近生产。

### G3 · Spark 与 DAG 侧

`dags/_spark_common.py` 是这批唯一需要动脑的文件：

- `GCS_CONNECTOR_JAR` → `hadoop-aws:3.3.4` + `aws-java-sdk-bundle:1.12.262`。
  **版本必须精确匹配 Spark 3.5.1 自带的 Hadoop 3.3.4**，差一个小版本即
  `NoSuchMethodError`。继续用 `--jars` 挂精确版本，不要用 `--packages`
  ——理由与文件里那段 GCS connector 注释完全相同，那段注释的**结论仍然有效**。
- `fs.gs.*` 三条 → `fs.s3a.*`，必须包含
  `fs.s3a.path.style.access=true`（MinIO 无 virtual-host DNS）与 endpoint。
- 🚨 **保留** `spark.pyspark.python` / `spark.pyspark.driver.python` /
  `spark.executorEnv.PYTHONPATH` 三条及其长注释——与存储无关，是 Shapely UDF
  和 Python 3.11 的依赖，删掉会重现 ADR 0005 记录的两个坑。
- 🚨 **S3 密钥不得经 `--conf` 传递**（会出现在 Spark UI 环境页、进程列表、
  Airflow 任务日志）。走 worker 上的 `spark-defaults.conf`（权限 600）或
  环境变量注入。

其余机械替换：`spark/jobs/etl_dcp.py`(7)、`etl_open_meteo.py`(5)、
`spark/schemas/dcp_schemas.py`、`dags/_dag_common.py`(5，含 `GCS_BUCKET_NAME`
→ S3 系列环境变量与 Param description)、13 个 DAG 文件里的路径字符串。

`dags/dag_audit_bronze.py` 有一处运气好：它按**精确路径检查 manifest 存在性**，
而 manifest 保持未压缩的 `.json`，**缺口检测逻辑无需改动**，仅换客户端。

### G4 · 验收断言

迁移完成的判据是一条可执行的 grep：

```bash
grep -rniE "gcs|bigquery|google\.cloud|gs://|dataproc|composer|DEPLOYMENT_PHASE" \
  --include="*.py" --include="*.yaml" --include="*.yml" --include="*.toml" \
  --include="Makefile" --include="*.example" \
  dags ingestion scripts spark tests config infra .env.example Makefile
```

**期望输出为空。** `docs/dev/adr/` 例外（ADR 记录历史，不清洗）。

---

## 3. Stage N · 新增能力（不是"去 GCP"，是迁移必须新写的）

### N1 · `snapshot` 分区策略

- `ingestion/config/source_config.py`：枚举加 `snapshot`；放宽校验——
  `snapshot` 允许 `timestamp_field: null`（现有校验只覆盖 `daily` 必填与
  `static` 必空，需要新增第三条分支）。
- loader 加 `write_snapshot()`：复用**已存在但此前从未被 facade 调用**的
  `ingest_date=YYYY-MM-DD/` 布局（`gcs_loader.write()` 就是这个布局）——
  这意味着路径代码几乎不用新写。
- `facade.py` / `bulk.py` / `_common.py` 的分发表加一行。
- `.claude/rules/backfill.md` 的两张分发表同步（S2 已列）。

### N2 · 流式写入（只有快照需要）

现有 fetch 路径把全量记录物化为 Python list；23.8 万行在小内存节点上会 OOM。
改为：分页拉取 → 边写临时 gzip 文件 → 分片上传，使内存占用只与单页大小成正比。

**只对快照做**。311 的 1,835 万行看似更大，但回填按天切片，18 年摊下来平均每天
约 2,800 行，每片都很小——约束的是**单文档大小**，不是总行数。

---

## 4. Stage T · 计算节点服务接入

Hive Metastore（MySQL 后端）+ Trino + Superset 加入
`infra/docker/docker-compose.yml`。

**Gold 层之前用不上，可以最后做。** 唯一要提前注意的是内存预算：计算节点
23 GB 中实测已用 14 GB、可用 8 GB，Trino 单节点堆 4–6 GB + Metastore 约 1 GB
是按这个余量算的，接入前先复核当时的实际余量。

---

## 5. 依赖关系与建议顺序

```
S3 .sqlfluff ─┐
S4 拆雷      ─┤ 全部独立，随时可做，各几分钟
S5 撤 key    ─┘

S2 约定文件 ────────────→ 越早越好（每次会话都在受影响）

MinIO 起服务 → G2 loader+gzip → N1 snapshot → N2 流式 → 🔴 S1 BO-7 上线
                                                          （关键路径，唯一在流血）

G1 删除  ─┐
G3 Spark ─┤ 不阻塞 S1，之后从容做
S6 CI    ─┘

Stage T ──────→ Gold 层开工前做即可
Phase 2W 其余（Winnipeg 源 YAML / 311 回填 / Silver）─→ 依赖 G2+G3 完成
```

一句话：**先花几分钟做完 S2–S5，然后一头扎进关键路径把 BO-7 顶上去，
剩下的所有事都可以排在它后面。**

---

## 6. 需要决定的开放项

| 项 | 现状 | 建议 |
|---|---|---|
| GCS 上的 NYC Bronze 存量 | 额度 2026-08 到期即失效 | **已决定放弃**（ADR 0006 §7）：可从 Socrata 重放，仅作可移植性基线。若改主意，一次 `rclone` 即可，但须在到期前 |
| 快照的 Silver 是否逐日存全量 | 未定 | 先逐日全量（2.6 GB/年，容量不紧张）；delta 化留到用得上时 |
| `closed_date` 业务语义 | 未确认，阻塞 BO-5 结论强度 | 与 Winnipeg 源 contract 一起做，别单独排期 |

