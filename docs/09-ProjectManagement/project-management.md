

## Phase 1（GCP Demo）—— 原始 12 周排期计划

> ⚠️ **这是计划，不是已完成状态。** 实际进度约在 Week 7（Bronze 全通，
> Silver 完成 2/4 个源，Gold 未开始）。当前真实状态与接手上下文见
> [handover-2026-07.md](handover-2026-07.md)。
>
> 与计划的两处重大偏离：
> 1. **Dataproc 被放弃**，改用自建 Docker Spark（Week 4 的"Dataproc 最小集群模板"作废）。
> 2. **Cloud Composer 未真正启用**，Airflow 跑在自建 Docker 上。

[Phase 1排期Gannt图](../images/NYC-UOIP-Phase1%20—%2012-Week%20Project%20Plan.pdf)

---

**Week 1 — 仓库 + GCP 基础（13h）**

- 建好 monorepo 目录结构，放入 CLAUDE.md / AGENTS.md / .claude/
- GCP 项目创建，IAM 服务账号（composer_sa / dataproc_sa），最小权限
- 创建 GCS Bronze/Silver bucket，BigQuery dataset `nyc_uoip_gold`
- 初始化 GitHub repo，配置 `.pre-commit-config.yaml`（ruff + sqlfluff）

**Week 2 — Cloud Composer + 311 ingestion（13h）**

- 启动最小 Composer 环境（small size），**用完立即暂停，省预算**
- 写 `socrata_client.py`（分页、App Token、指数退避重试）
- 写 `dag_ingest_nyc_311.py`，测试增量拉取到 Bronze

**Week 3 — NYPD + Open-Meteo ingestion（15h）**

- NYPD DAG，实现 7 天 lookback window（late-arriving facts 的关键）
- Open-Meteo client（NYC 5 borough 中心点坐标）
- Borough GeoJSON 一次性上传
- Pydantic schema 校验原始 API 响应

**Week 4 — Bronze 验证 + Spark 环境（12h）**

- 确认 Bronze 分区结构正确：`bronze/<dataset>/year=/month=/day=/`
- Dataproc 最小集群模板（n1-standard-2，ephemeral，job 完成自动删除）
- 写 `silver_311_schema.py`（StructType）

**Week 5-6 — Spark ETL: 311 + NYPD（20h 合计）**

- `etl_nyc_311.py`：schema enforcement → timestamp_normalizer → dedup → Parquet write
- `etl_nypd_collisions.py`：crash_date + crash_time 两字段合并成 UTC timestamp（这里最容易出错，多留时间）
- `deduplication.py` 和 `geo_enrichment.py` 作为公共 transforms

**Week 7 — Spark ETL: 天气 + Airflow 串联（12h）**

- `etl_open_meteo.py`
- `dag_etl_bronze_to_silver.py` 接在 ingestion DAG 完成后触发 Spark job
- 验证 Silver Parquet 数据质量（行数、null 率、时间戳 UTC 正确性）


**Week 8-9 — BigQuery Gold 建模（21h 合计）**

- DDL：`fact_311_requests`、`fact_vehicle_collisions`、所有 dim 表
- **重点**：`dim_geography` 加载 GeoJSON → `GEOGRAPHY` 类型，验证 `ST_CONTAINS` 空间连接
- DML：Silver External Table → Gold managed table 增量加载
- 把 `dag_etl_silver_to_gold` 接进 Airflow

**Week 10 — Intelligence engine（16h）**

- `calc_load_score.sql`：每日每 borough 计算 0.4×311 + 0.4×collision + 0.2×weather
- `calc_operational_drivers.sql`：IF snowfall>0 THEN 'Blizzard' 等规则
- `calc_resource_recommendations.sql`：Load Score 区间 → 部门建议
- 写入 `fact_daily_operational_summary`

**Week 11-12 — Dashboard + CI/CD + 文档（17h 合计）**

- Looker Studio 接 BigQuery，做 borough 颜色热力图 + 排名列表 + 建议文本
- GitHub Actions `ci.yml`（ruff + pytest unit tests on PR）
- `deploy-dags.yml`（merge to main 自动 push DAGs 到 Composer）
- README、数据字典、架构图