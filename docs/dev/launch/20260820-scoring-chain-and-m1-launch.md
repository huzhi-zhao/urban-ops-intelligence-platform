# 评分链与 M1（L3）上线记录

> **Status**: 待执行（开跑前开篇） · **Date**: 2026-08-20
> **design**: [20260820-scoring-chain-and-m1.md](../design/20260820-scoring-chain-and-m1.md)
> **前一次**: [L2 Gold 维表与事实表](20260819-gold-dimensional-build-launch.md)
> （**硬前置**，阶段 E 收口未完，见本篇 §1）
>
> 判据、口径、被否决选项一律以 design 篇为准，本篇只记**怎么做、做了什么、
> 实际数字是多少**。空的复选框是还没做，不是做了没记。

---

## 0. 一页纸：这次要做的事

把 Gold 最后 **3 张零行表**填满（`fact_request_forecast` / `fact_winter_event_zone_load` /
`fact_recommendation`），17 张 Gold 表齐；再收 DQ 基线与 S7 冻结。

| 段 | 动作 | 估时 | 可否回滚 |
|---|---|---|---|
| **L3-0** 前置解锁 | 修 O17 → `ONLY=facts` 重跑 → 三条探针复跑 | 1 小时 + 14 分钟跑 | ✅ 回填幂等，Gold 秒级重建 |
| **L3-a** M1 + F5 | `models/` + `ml` extra + 训练 + artefact + F5 装载 | 3 天 | ✅ artefact 只追加，表可重建 |
| **L3-b** 评分链 | `sql/intelligence/` 两份 + 执行器 `scoring` 段 | 2 天 | ✅ 秒级重建 |
| **L3-c** 收口 | DQ 基线 17 张表 + 四件套 + CHANGELOG + 讲稿口径 | 1.5 天 | ✅ 只读 |

**与 L1 一样、与 L2 也一样：本篇没有一步是不可逆的。**
F5 的 artefact 写的是新前缀 `gold/_forecast_runs/`，只追加不覆盖；
Silver 回填按日分区幂等；Gold 全部整表重建。

**那为什么还要提前开篇？** 因为 L3 的关键信息**只在跑的那一刻存在**：

1. 🔴 **M1 的 MAE 与 seasonal-naive 基线那一对数**。这是 L3 独有的失败模式——
   ETL 的失败是「数字不对」，建模的失败是「数字对但结论站不住」。
   BO-8 已写明「优于基线」不作为对外承诺、未达标不构成失约，
   **那这个结果如实记在哪里？只有这里**。它同时是讲稿那句话的唯一依据。
2. 🔴 **探针复跑的漂移数**。L2 §4.9 已经量到：Open-Meteo 回修历史存档 →
   `segment_events` 重切边界 → 非零格 916 掉到 908，而 N / 排班期数 / 中位时长
   **全都不动**，没有第二处输出会显示这件事。这次跑出来是多少，只有当场记才有。
3. 门禁表 a1–a7 / b1–b13 要逐条填实测值。

四条最容易翻车的，先说：

1. 🔴 **F5 不能按 R4 原样整表重建。** 契约要求旧 `model_version` 永不覆盖，
   而 R4 的第二步是 purge storage prefix——直接跑会**静默清空上一版的预测与回测**，
   行数门禁只看当前版本，什么都不报。走 artefact 方案（design §5）。
2. 🔴 **`load_score` 在 `partial_no_rank` 上给不给值，DDL 两处注释互相矛盾**
   （design O1）。**L3-b 第一天先定，记进 §4**，不要边写 SQL 边猜。
3. 🔴 **374 / 924 是从「17 个已对齐犁雪作业」推出来的，不是量出来的。**
   门禁红了先查 17 有没有变，再决定改哪一边。
4. 🔴 **`shift_number` / `rank_factor` 不得进 M1 特征**。喂进去等于把 BO-6 的
   0.30 权重项混进 0.40 权重项，三项独立性的实测结论当场失效。单测钉死（a5）。

⚠️ 宿主机 shell 连 Trino 一律加前缀：`TRINO_HOST=localhost TRINO_PORT=8090`。
`.env` 里的 `trino:8080` 是**容器视角**。

---

## 1. 前置检查（开跑前逐条填）

- [ ] **P0 L2 阶段 E 的剩余项**：E1 DQ 基线 / E4 CHANGELOG / E5 PR。
      🟡 E1 的空值率在本篇 L3-c 一并收（17 张表一次跑完，比分两次划算），
      **L2 launch §7.2 第 3 条据此关闭**，不重复跑。

- [x] **P1 修 O17** —— `silver_service_request` 的 08-17/18/19 三天。
      先查 Bronze 到底有多少条，**不要直接回填**：manifest 的 `record_count`
      对上 8 就是上游真没数据，对不上才是 Silver 那侧的问题。

⚠️ **本节原写的 `aws` 命令在计算节点上不存在**（没装 awscli，也没有
`minio-client` 容器）。仓库自己的 `load_s3_settings()` 是唯一不必额外装东西的路：

```bash
set -a; source .env; set +a      # .env 不会自动进 shell 环境
uv run python -c "
import json
from ingestion.loaders.s3_client import build_s3_client, load_s3_settings

settings = load_s3_settings()          # 四个 S3_* 变量缺一个就一次全报出来
client = build_s3_client(settings)
for day in ('2026-08-16', '2026-08-17', '2026-08-18', '2026-08-19', '2026-08-20'):
    key = f'bronze/raw/SRC-WPG-311/service_requests/2026-08/manifest_{day}.json'
    try:
        m = json.loads(client.get_object(Bucket=settings.bucket_name, Key=key)['Body'].read())
        print(day, m['record_count'], m['fetch_timestamp'])
    except client.exceptions.NoSuchKey:
        print(day, 'MANIFEST MISSING')
"
```

**实测（2026-08-20）**，五天的 Bronze `record_count`：

| 日期 | Bronze | 说明 |
|---|---|---|
| 2026-08-16 | 1,109 | 周日，量级正常 |
| 2026-08-17 | 3,100 | 工作日 |
| 2026-08-18 | **3,006** | 🔴 Silver 侧只有 **8** 行 → **确认是 Silver 的问题** |
| 2026-08-19 | **10** | 🟢 见下，**不是故障** |
| 2026-08-20 | 无 manifest | 同上 |

🔴 **本篇的第一条更正：「08-19 无分区」是预期行为，不是 O17 的一部分。**
用 CLI 原样重抓一遍 `[2026-08-16, 2026-08-21)`，08-19 仍然是 **10 行**、
08-20 仍然是 **0 行**（`No records ... — skipping`），与首次采集**逐日完全一致**。
即 Bronze 是忠实的，**上游 Socrata 自己还没发布这两天的数据**。
这正是 ingest DAG 带 7 天回溯的理由——过一两天的 run 会自己把 08-19 补齐，
不需要人工干预。L2 launch §4.13 把它和 08-18 并列成「三天数据缺失」是**误判**，
真实缺失只有 08-18 一天。

> 判据留给下次：**Bronze 行数异常时先原样重抓一遍**。重抓后数字不变 =
> 上游如此；变了才是采集侧的问题。这一步比查日志便宜得多。

回填（幂等，按日分区覆盖）。⚠️ **触发前先确认 paused 状态**：

```bash
# ⚠️ 容器名是 uoip-airflow-scheduler-1，不是 airflow-scheduler
docker exec uoip-airflow-scheduler-1 airflow dags details \
  dag_backfill_silver_service_request -o yaml | grep is_paused
# is_paused: False 才继续；否则先 unpause，再用 details 复查（unpause 的输出不可信）
docker exec uoip-airflow-scheduler-1 airflow dags trigger \
  dag_backfill_silver_service_request \
  --conf '{"start": "2026-08-12", "end": "2026-08-20", "bucket": "uoip"}'
```

⚠️ 参数名是 **`start` / `end`**（不是 `start_date` / `end_date`），
见 `dags/dag_backfill_silver_service_request.py` 的 `_check_params`。
DAG 自带 `sync_partitions` 任务，Trino 侧不必手工同步。

⚠️ Airflow 3 的 `list-runs` 用**位置参数**，`-d` 已被删除：
`airflow dags list-runs <dag_id> -o table`。

- [x] 回填后逐日行数 —— ✅ **O17 关闭（2026-08-20）**

窗口 `[2026-08-12, 2026-08-20)`，三个任务全 success，`sync_partitions` 59 秒。
**八个日分区逐日与 Bronze manifest 的 `record_count` 精确相等**：

| 日期 | Bronze | Silver 回填前 | Silver 回填后 |
|---|---|---|---|
| 2026-08-12 | — | — | 2,987 |
| 2026-08-13 | — | — | 3,004 |
| 2026-08-14 | — | — | 2,760 |
| 2026-08-15 | — | — | 1,385（周六） |
| 2026-08-16 | 1,109 | — | **1,109** ✅ |
| 2026-08-17 | 3,100 | — | **3,100** ✅ |
| 2026-08-18 | 3,006 | **8** | **3,006** ✅ ← O17 的唯一判据 |
| 2026-08-19 | 10 | 无分区 | **10** ✅（上游只有 10，不是缺失） |

核对用的查询（宿主机 shell，注意 Trino 前缀）：

```bash
TRINO_HOST=localhost TRINO_PORT=8090 uv run python -c "
from scripts.ddl.apply_ddl import load_trino_settings, _connect
conn = _connect(load_trino_settings(), 'uoip_silver')
cur = conn.cursor()
cur.execute('''SELECT open_date_local, COUNT(*) FROM silver_service_request
               WHERE open_date_local >= DATE '2026-08-12' GROUP BY 1 ORDER BY 1''')
for row in cur.fetchall():
    print(row[0], row[1])
"
```

⚠️ `load_trino_settings` / `_connect` 都在 **`scripts.ddl.apply_ddl`** 里，
没有 `scripts.trino_settings` 这个模块。

- [x] **P2 `ONLY=facts` 重跑**，对 design §2.1 的五个行数。
      §4.13 已论证不该变，**但那是推理不是实测**。
      ✅ **已实测（2026-08-20，`run_id=l2-20260820T153519Z`）：五个数逐个相同，
      全部门禁绿。** 推理这次是对的。

```bash
set -a; source .env; set +a
TRINO_HOST=localhost TRINO_PORT=8090 make gold-build ONLY=facts
```

| 表 | 期望 | 实测 | 耗时 |
|---|---|---|---|
| `fact_plow_shift` | 418 | **418** ✅ | 1.7s |
| `fact_parking_ban` | 49（19 匹配 / 30 NULL） | **49 / 19 / 30** ✅ | 0.9s |
| `fact_event_zone_rank` | 418 | **418**，`rank_factor=0` 为 0，扇出 17=17 ✅ | 1.0s |
| `fact_service_request_zone_event` | 13,068 / 2,178 / 1,298 / 非零 ≥880 | **13,068 / 2,178 / 1,298 / 908** ✅ | 397s（19 片） |
| `fact_winter_request_daily_by_label` | 141,377 / 18 个年份 | **141,377 / 18** ✅ | 431s（19 片） |

耗时：**约 832 秒（14 分钟）**，97% 在 F1 + F8 两张 19 分片的表上——与 L2 那两趟
几乎一致，再次印证成本是**分片数的固定开销**，与数据量无关。

🟢 **补了 08-18 的 3,006 行 Silver，Gold 一个数都没动**，包括最敏感的非零格 908。
这不是巧合也不是门禁不灵敏：补的三天是 **2026 年 8 月**，不落在任何降雪事件窗口内，
而 F1 的事件来源是 `JOIN dim_snowfall_event`。§4.13 的推理链条到此**实测闭合**。

⚠️ 门禁输出里那条 `[note] not machine-checked: ... HAVING SUM(request_count) > 0) = 916`
是 DDL 头注里不执行的 prose，**以 launch §4.9 的 908 下界为准**（L2 遗留的
`-- relationships:` 数字问题，要改走变更流程）。别把它当成一条红的门禁。

- [x] **P3 三条探针复跑**（2026-08-21，两条公开 API 探针已跑；第三条读 Gold，
      见下）。**这一步的产出就是本篇的价值之一。**

```bash
uv run python -m scripts.analysis.snowfall_events \
  --thresholds 3 --accum-window-days 10 --accum-threshold-cm 10 --zone-panel
uv run python -m scripts.analysis.score_collinearity --threshold 3
```

🔴 **跑之前必须把 `var/probe-cache/` 挪开。** 缓存按窗口键命中，而 P3 要查的
恰恰是 Open-Meteo 回修历史存档——带着缓存跑，探针会把上一次的天气原样再算一遍，
**输出稳定得像没漂，而那正是假象**。本次把 30 MB / 69 个文件整目录改名为
`var/probe-cache.pre-p3-20260821/`（未跟踪，留着可对拍），第一条探针因此
全量重取 18 个雪季窗口 × 25 分区；第二条命中新缓存，秒级。

| 量 | 2026-08-09 台账 | 2026-08-19 复测 | **本次（2026-08-21）** |
|---|---|---|---|
| 事件数 N / 排班期 | 99 / 59 | 99 / 59 | **99 / 59** ✅ 不动 |
| 中位事件时长 / 间隔 | 1.0 / — | 1.0 / — | **1.0 / 15.0 天** |
| 22 分区面板非零率 | 70.57%（916） | 69.8%（≈906） | **69.8%（≈906）** |
| ward × 事件非零率 | 77.9% | — | **80.7%** |
| Gold F1 排班期非零格 | — | 908 | **未测**（见下） |
| `r(顺位, 请求量)` | +0.017 | — | **+0.017** |
| `r(顺位, 天气)` | −0.006 | — | **−0.006** |
| `r(请求量, 天气)` | +0.460 | — | **+0.460** |
| 顺位项改变排序 | 15/15，ρ 中位 0.41 | — | **15/15，ρ 中位 0.45** |
| 311 空间命中率 | 99.9%（134,123/134,258） | 134,281 | **99.9%（134,150/134,285）** |

**结论：N=99/59 冻结，design O4 未触发。** 面板格数 2,178、374/924、回测次数
全部照 §2.1 不动，L3-a 可以按现有口径开工。

两处值得记的：

- 🟢 **全量重取之后 69.8% 原样复现**，与 08-19 那次到小数点一致。08-09 的
  70.57% → 69.8% 那次漂移不是随机噪声，是一次已经落定的边界重切；
  两周内没有第二次。F1 的 `>= 880` 下界离实测 ≈906 还有余量。
  ⚠️ 但 ADR 0010 记的「≥70% 判据达成」今天仍是 69.8%，**仍贴着线**，
  维持 08-19 那个「四舍五入视为达成、不重开签字」的决定。
- 🟡 顺位项的 ρ 中位从 0.41 动到 **0.45**，方向是「顺位的影响略微变小」。
  两个数都远离 1.0，BO-6 的判据（顺位可见地改变排序）照样成立，
  **不构成重开**；记在这里是因为它是这轮唯一真动了的数。
- 探针口径提醒：这条探针按 `--align-lag-days 3` 报「19 次犁雪里 15 次落在事件内」，
  而 Gold 的 `dim_plow_event` 用的是 lag 7d、报 17。**两个数不矛盾，别互相对**——
  374/924 的推导前提是 Gold 那个 17，量它是 P5 的事。

🔴 **第三条（Gold F1 排班期非零格）与 P5 都读 Trino，本次未测**：Trino 跑在
计算节点上，不在开发机；`.env` 里的 `trino:8080` 是 Airflow 容器视角。
这两条留给下一个在计算节点上的会话，命令见 P5 与 §1 P2。

- [ ] **P4 计算节点资源**：训练是秒级、面板 2,178 格，内存不是问题；
      但确认 Spark 没有在跑作业再动手（7 GB 共用）。

```bash
docker stats --no-stream
```

- [ ] **P5 `dim_plow_event` 的 17 还是 17**（374/924 的推导前提）

```sql
SELECT COUNT(*) AS ops,
       COUNT(matched_snowfall_event_id) AS matched,
       COUNT(DISTINCT matched_snowfall_event_id) AS distinct_events
FROM hive.uoip_gold.dim_plow_event;   -- 期望 19 / 17 / 17
```

结果：____ / ____ / ____

---

## 2. 执行清单

### 阶段 A · L3-a：M1 与 F5

#### A1 环境：`ml` extra 与独立 venv

🔴 **必须独立环境 `.venv-ml`**，理由与 `make test-dags` 完全一样：
`uv` 不会因为两个发行包往同一个 `namespace` 目录写文件而报冲突
（O15 那次 `pyspark-client` 把钉死的 3.5.1 覆盖成 4.2.0，lock 里还写着 3.5.1）。

- [x] A1a `pyproject.toml` 加 `[project.optional-dependencies] ml`
      （`pandas>=2.0` + `statsmodels>=0.14`）✅ `da89af4`
- [x] A1b `make test-ml` target（照抄 `test-dags` 的形状）✅
- [x] A1c CI 加 `ml` job ✅
- [ ] A1d 装完核一次版本，别再被静默覆盖一遍

🔴 **顺手修了一个会让整个隔离失效的疏漏**：`.gitignore` 里 `.venv-airflow` 是
**逐个列**的，`.venv-ml` 因此不被忽略、会整个进版本库。已改成 `.venv-*` 前缀——
下一个隔离环境在建出来那天就被忽略，而不是在有人注意到 `git status` 那天。

```bash
uv run --python .venv-ml python -c "import pyspark, statsmodels, pandas; \
print(pyspark.__version__, statsmodels.__version__, pandas.__version__)"
```

结果：pyspark = ____（**必须是 3.5.1**）

#### A2 代码

- [x] `models/request_forecast/{features,model}.py` —— 角色名，不出现城市字面量 ✅ `da89af4`
- [x] `config/models/m1.yaml` —— 特征清单 / 切分 / `model_version` 前缀 ✅
- [ ] `scripts/models/train_m1.py` —— 读 Trino → 训练 → 写 artefact
- [ ] `scripts/gold/build_gold.py` 加 `scoring` 段与 F5 的 loader
- [x] `tests/unit/test_m1_features.py`（25）· `test_m1_model.py`（17）✅ **42 passed**

三条设计红线**落成了代码而不是注释**，这是本段唯一值得强调的事：

| 红线 | 实现 | 单测 |
|---|---|---|
| `shift_number`/`rank_factor` 不进特征 | `feature_names()` 见到就抛，`build_design_matrix()` 再拦一道 | 两头各 2 项 |
| 滞后统计不跨切分边界 | 严格 `shift(1)` 因果，**不按 split 分别算** | 4 项 |
| 严禁随机切分 | `assert_no_history_leak` 查「训练事件都早于留出事件」 | 含一项**专门喂随机切分**，确认它被抓住 |

`evaluate()` 一次返回模型 + 基线两组指标，**没有只返回模型指标的函数**——
BO §4.4 那条「没有基线的模型结论不予采信」因此不依赖谁记得。

#### A3 训练与装载

```bash
TRINO_HOST=localhost TRINO_PORT=8090 \
  uv run --python .venv-ml python -m scripts.models.train_m1 --model-version m1-poisson-v1
TRINO_HOST=localhost TRINO_PORT=8090 make gold-build ONLY=fact_request_forecast
```

- [ ] A3a artefact 落盘

```bash
aws --endpoint-url "$S3_ENDPOINT_URL" s3 ls --recursive \
  "s3://$S3_BUCKET_NAME/gold/_forecast_runs/"
```

- [ ] A3b 🔴 **版本保全实测**：训第二个版本、重建 F5，
      确认**第一个版本的行还在**。这是 design §5 那条冲突的唯一验证方式，
      不做等于把「artefact 方案有效」当成信仰。

```sql
SELECT model_version, COUNT(*) FROM hive.uoip_gold.fact_request_forecast
GROUP BY model_version;   -- 每个版本各 1,298
```

结果：____

#### A4 门禁表（跑完逐条填）

| # | 判据 | 期望 | 实测 |
|---|---|---|---|
| a1 | 训练面板格数（跨类别聚合后） | 2,178 | ____ |
| a2 | F5 行数 / 每个 `model_version` | 1,298 | ____ |
| a3 | `baseline_count IS NULL` 的行 | 仅最早雪季，**逐条能解释** | ____ |
| a4 | 🔴 留出季 MAE：模型 vs seasonal-naive | **成对记录** | 模型 ____ / 基线 ____ |
| a4b | Poisson deviance：模型 vs 基线 | 成对 | ____ / ____ |
| a4c | 留出的是哪个雪季 | `snow_season` 最大值 | ____ |
| a5 | 特征矩阵不含 `shift_number` / `rank_factor` | 单测绿 | ____ |
| a6 | 同 `model_version` 重跑，行数与预测值不变 | 一致 | ____ |
| a7 | `make lint` + `test-unit-offline` + `ml` job | 全绿 | ____ |

> 🔴 **a4 不优于基线不阻塞上线**（BO-8 §0.2.2：可比事件仅 15 个，样本量不足以让
> 「优于基线」成为可辩护的公开结论）。**但必须如实写在这里**，并按 design §9
> 的口径讲。达成了可以讲，没达成不构成失约——**改数字或不写才构成失信**。

### 阶段 B · L3-b：评分链 F6 / F7

- [x] **B0 先定 O1**：`load_score` 在 `partial_no_rank` 上给不给值。
      ✅ **已定案（2026-08-20，开工前）：给值**，按 design §6.2。
      裁决与三条佐证见 **§4.2**；`load_score` 列那句 "Null when score_status
      != scored" 判定为过时表述，**不改 DDL**（契约冻结，以本篇为准）。
      b9 不受影响。

- [ ] B1 `sql/intelligence/fact_winter_event_zone_load.sql`
- [ ] B2 `sql/intelligence/fact_recommendation.sql`
- [ ] B3 执行器 `scoring` 段的门禁接进 `extra_gates`
- [ ] B4 单测（含 R6 的 `{{ silver }}` 检查——**这两份一行 Silver 都不读**，
      所以 R1/R6 天然满足，但单测的扫描规则要确认不会误伤）

```bash
TRINO_HOST=localhost TRINO_PORT=8090 make gold-build ONLY=scoring
```

预算：**2 分钟量级**。两张表都不分片、只读 Gold（最大 141,377 行且不分区）。
🔴 **超过 5 分钟就是有 Silver 被误连进来了**，回查 R1。

#### B5 门禁表（跑完逐条填）

| # | 判据 | 期望 | 实测 |
|---|---|---|---|
| b1 | F6 行数 | 1,298 | ____ |
| b2 | `score_status = 'scored'` | 374 | ____ |
| b3 | `partial_no_rank` | 924 | ____ |
| b4 | `no_schedule_era` | 0 | ____ |
| b5 | `rank_factor = 0` | 0 | ____ |
| b6 | profile 与 status 绑死（两向） | 各 0 | ____ |
| b7 | 前排班期事件混入 | 0 | ____ |
| b8 | 天气项事件级常量（每事件 DISTINCT ≤ 1） | 违反组数 0 | ____ |
| b9 | `load_score` 值域 [0, 100] | 越界 0 | ____ |
| b10 | F7 行数 / 每个 `model_version` | 374 | ____ |
| b11 | `attribution_rule_id` anti-join | 0 | ____ |
| b12 | 每事件 `rank_model` 是 1..22 的排列 | 违反事件数 0 | ____ |
| b13 | 连跑两次行数逐张相同（R4 purge） | 相同 | ____ |

附带记录（不是门禁，是讲稿素材）：

| 量 | 值 |
|---|---|
| `rank_delta > 0` 的格数（模型排序优于基线） | ____ / 374 |
| 各 `attribution_rule_id` 的命中分布 | ____ |
| `RULE-NO-SCHEDULE` 命中数 | **预期 0**（design §6.3，不是缺陷） |
| `load_level` 分布（按 profile 分开） | ____ |

### 阶段 C · L3-c：DQ 基线与 S7 冻结

```bash
TRINO_HOST=localhost TRINO_PORT=8090 make gold-dq > /tmp/dq.md
```

- [ ] C1 17 张表逐张：行数 · 各列空值率 · 构建耗时 → 贴进 §3
- [ ] C2 零行的表数 = **0**（`dq_baseline` 会自己报并返回 1）
- [ ] C3 测试四件套：`unique` / `not_null` / `relationships` / `accepted_values`
- [ ] C4 S2 bus matrix 逐格复核
- [ ] C5 `CHANGELOG.md` 记 schema **v1.0**
- [ ] C6 讲稿口径页按 design §9 定稿
- [ ] C7 PR

---

## 3. 实测数字（跑完填）

### 3.1 三张新表

| 表 | 期望 | 实测行数 | 耗时 |
|---|---|---|---|
| `fact_request_forecast` | 1,298 × 版本数 | ____ | ____ |
| `fact_winter_event_zone_load` | 1,298 | ____ | ____ |
| `fact_recommendation` | 374 × 版本数 | ____ | ____ |

### 3.2 DQ 基线（17 张表）

> `make gold-dq` 的 markdown 直接贴这里，不要手抄。
> **这是后续告警阈值的唯一依据**，没有基线的阈值都是拍脑袋。

（待填）

### 3.3 M1 评估

| | 模型 | seasonal-naive 基线 |
|---|---|---|
| 留出季 MAE | ____ | ____ |
| Poisson deviance | ____ | ____ |

留出雪季：____ ｜ 训练事件数：____ ｜ 特征数：____ ｜ `model_version`：____

---

## 4. 与设计的偏差 / 过程中的决定

> 一条一段，写清**为什么改**，不只是改了什么。design 篇不改，以本节为准。

### 4.1 O17 的范围比 L2 记的小一天（2026-08-20）

L2 launch §4.13 记的是「08-17/18/19 三天数据缺失」。实测只有 **08-18 一天**
是真缺失（Bronze 3,006 / Silver 8）。08-19 的 Bronze 本身只有 10 行、08-20 没有
manifest，而**原样重抓一遍数字分毫不变**——上游 Socrata 还没发布这两天，
7 天回溯窗口会自己收掉。详见 §1 P1。

判据留下来：**Bronze 行数看着不对时，先原样重抓一遍再下结论。**
重抓后不变 = 上游如此；变了才是采集侧的问题。比翻日志便宜。

### 4.2 O1 裁决：`partial_no_rank` **给** `load_score`（2026-08-20，开工前定）

design §10 O1 说 DDL 的两条注释互相矛盾，要在 L3-b 第一天定。
**读完整份 DDL 之后这条其实不需要投票——三比一。**

| 出处 | 说的是 |
|---|---|
| `load_score` 列注释 | "Null when score_status != scored — never a fabricated 0." |
| `load_level` 列注释 | "**71.2% of the panel (partial_no_rank)** is scored on a 0.70 weight sum … its load_score/load_level are **systematically lower**" |
| `score_weight_profile` 列注释 | `demand_weather_only` = 权重和 0.70，**不重归一化**；与 `partial_no_rank` **1:1** |
| `score_status` 列注释 | `partial_no_rank` = "rank factor NULL, **score computed from BO-1 + BO-3 only**, weighting disclosed not silently renormalized" |

后三条都在**描述 `partial_no_rank` 行身上那个分数长什么样**——
「systematically lower」「score computed from」这种话，对着 NULL 说不通。
只有 `load_score` 自己那一行说 NULL。

**定案：按 design §6.2 给值。** 三条佐证：

1. 判 NULL 则 `demand_weather_only` 这个 profile **一行都不会有**（924 格全 NULL），
   而它是 schema 里的 `accepted_values` 之一、还配了整段注释解释它为什么不重归一化。
   一个没有任何行会取到的取值，不会被写成这样。
2. `no_schedule_era` 在 H1 内恒为 0 行（design §2.1），所以「非 `scored`」
   在 H1 内**就等于** `partial_no_rank`。`load_score` 那条注释若成立，
   等价于说 71.2% 的面板整列为空——那 F6 也就没什么可展示的了。
3. 注释里 "never a fabricated 0" 的**靶子是 0 不是 NULL**：它防的是
   「顺位缺失填 0」（design §8 明确否决过、ADR 0008 §2.3 的同一条），
   而那件事由 `rank_factor` 保持 NULL 来落实，不需要 `load_score` 也跟着空。

**`load_score` 那行注释因此是过时表述，不是权威。** 🔴 **不改 DDL**——
契约自 2026-08-13 冻结，改注释要走变更流程；本篇即是那条注释的口径覆盖，
与 L2 launch §4.9 处理五张事实表 `-- relationships:` 里那个 `= 916` 的做法一致
（prose 不执行，以 launch 为准）。

对门禁的影响：**b9 保持 `[0, 100]` 且越界行为 0**，不需要因为 O1 改写。
两个 profile 的实际上限不同（100 / 70），但都落在 `[0, 100]` 内。

### 4.3 基线口径取**因果扩展均值**，调和 design §4.1 与 F5 的 DDL（2026-08-20）

两处措辞不完全一致，实现只能选一个：

| 出处 | 说的是 |
|---|---|
| design §4.1 | seasonal-naive = 同分区**在训练期所有事件上**的 `request_count` 均值 |
| F5 的 `baseline_count` 列注释 | "Null **only for the earliest events** with no prior history to average over" |
| 门禁 a3 | `baseline_count IS NULL` 的行 = 仅最早那个雪季的事件 |

「训练期均值」是**每个分区一个常量**，它在训练行上也有定义、而且永远不为 NULL
（除非某分区一个训练事件都没有）。那样后两条就没有着落——不会只有最早的事件为空。

**定案：因果扩展均值**（该分区在**严格更早**的事件上的均值，无更早事件则 NULL）。
它同时满足三条：

- 对**留出季**的每一行，它前面的事件全都是训练期事件，所以取值**就等于**
  design §4.1 说的训练期均值——两种说法在真正做比较的那些行上不冲突；
- 训练行上也有定义，于是 F5 的 1,298 行都填得满；
- 只有每个分区**最早**的那个事件为 NULL，正是 DDL 与 a3 描述的形状。

实现上它**就是 `expanding_mean` 那个特征本身**（`seasonal_naive()` 直接返回它），
所以基线与特征不可能各算各的、事后漂开。

预留的两处，开工时再落笔：

- **P3 探针漂移**对 374/924 与面板密度的影响
- **a4 的模型 vs 基线结论**如何进讲稿

---

## 5. 上线后要盯什么

Gold 是手动触发，没有「连续观察 3 天」这回事。真正要盯的是：

1. **下一次 Silver 增量跑完之后**，`dim_snowfall_event` 与 F1 的行数会不会变。
   按当前口径不该变——除非真的下了一场新的雪。变了就是 design O4 触发。
2. **F5 的版本数只增不减。** 任何一次 `--only scoring` 之后版本数变少，
   就是 artefact 方案破了，立刻停手查 purge 范围。
3. **`_forecast_runs/` 前缀不进任何清理脚本。** 它在 `gold/` 下面但**不是表**，
   `_purge_storage` 按表名前缀清理所以碰不到它——**这条依赖于表名不叫
   `_forecast_runs`**，别哪天加张同名表。

---

## 6. 这轮踩过的坑（跑的时候随手记）

L2 的四个坑照抄，别重新发现一遍：

- **改了 compose 的卷用 `make stack-recreate-airflow`**（restart 不重挂卷）。
- **新 DAG 默认 paused，`dags trigger` 对 paused 的 DAG 照样返回成功**——
  run 排队后永不执行。判据只有 `dags details -o yaml | grep is_paused`，
  **`unpause` 打印的是改之前的状态**。
- **宿主机连 Trino 加 `TRINO_HOST=localhost TRINO_PORT=8090`。**
- **占位符必须落在字符串字面量里**，否则 sqlfluff 判 unparsable 并
  **连带停掉该文件其余所有检查**。
- **排查「不跑」先用 `dag_smoke_alert` 划范围**（1 秒被调度、6 秒失败）。

本轮新增（2026-08-20，L3-0 那半天）：

- 🔴 **计算节点没有 `aws` 也没有 `mc`**，`minio-client` 容器同样不存在。
  本篇 §1 P1 原写的 `aws s3 cp` 一行是**不可执行的**。走仓库自己的
  `ingestion.loaders.s3_client.load_s3_settings()`——它不需要额外装东西，
  且四个 `S3_*` 缺哪个会一次全报出来。
- 🔴 **`.env` 不会自动进 shell 环境。** `KeyError: 'S3_ENDPOINT_URL'` 不是配置
  缺失，是没 `source`。宿主机跑任何读 `.env` 的命令前先
  `set -a; source .env; set +a`。
- **Airflow 容器名是 `uoip-airflow-scheduler-1`**（compose 项目名前缀），
  不是文档里各处写的 `airflow-scheduler`。
- **Airflow 3 的 `dags list-runs` 用位置参数**，`-d` 已被删除。
  同一批 CLI 变更还删掉了 `days_ago`（L2 §4.10 的四个缺陷之一）——
  **凡是从 Airflow 2 时代文档抄来的命令都值得先 `--help` 一下**。
- **回填 DAG 的参数名是 `start` / `end`**，不是 `start_date` / `end_date`。
  传错了 `check_params` 会以 `KeyError` 失败，但那是在 run 已经排队之后。
- 🟡 **`load_trino_settings` / `_connect` 在 `scripts.ddl.apply_ddl` 里**，
  没有 `scripts.trino_settings` 这个模块。
- 🟢 **`.gitignore` 的 venv 是逐个列的**，加隔离环境时会漏。已改前缀匹配。

---

## 7. 交接 —— 下个会话从这里继续

### 7.1 已经做完的（不要重做）

**L3-0 前置解锁：P1 / P2 完成，P3 的两条公开 API 探针完成；
P3 的第三条（Gold F1 非零格）与 P4 / P5 未做——三条都要计算节点。**

- ✅ **P1 O17 关闭**：真实缺失只有 08-18 一天，回填后 Silver 与 Bronze
  逐日精确相等。08-19/20 是上游未发布，**不是故障**（§1 P1、§4.1）。
- ✅ **P2 `ONLY=facts` 重跑全绿**：五个行数逐个不变，14 分钟（§1 P2）。
  补三天 Silver 对 Gold 零影响的推理链条**实测闭合**。
- ✅ **O1 提前定案**：`partial_no_rank` **给** `load_score`（§4.2）。
  原计划是 L3-b 第一天定，但读完整份 DDL 是三比一，不需要等。
- ✅ **L3-a 的 A1/A2 前半**（`da89af4`）：`ml` extra + `.venv-ml` + CI `ml` job +
  `models/request_forecast/{features,model}.py` + `config/models/m1.yaml` +
  42 项单测。`make lint` 干净，`make test-unit-offline` **866 passed** 未受影响。
- ✅ **基线口径定案**：因果扩展均值（§4.3）。

### 7.2 下一步，按顺序

1. ✅ **P3 的两条探针已复跑**（2026-08-21，§1 P3 表格已填）：**N=99/59 不动，
   design O4 未触发**，L3-a 的 2,178 格与 L3-b 的 374/924 口径不变。
   清空缓存后 69.8% 原样复现，三条相关系数逐个相同。
   **仍欠的是要连 Trino 的那两条**——Gold F1 排班期非零格（期望仍 908、下界 880）
   与 **P5** `dim_plow_event` 的 19/17/17（374/924 的推导前提）。
   下次上计算节点时**先跑这两条**，命令在 §1 P3 与 P5。
2. **`scripts/models/train_m1.py`** —— 读 Trino 取面板 → 训练 → 写 artefact
   到 `s3a://{bucket}/gold/_forecast_runs/{model_version}/`。
   🔴 它和 `config/models/m1.yaml` 是**唯二**可以出现 Winnipeg 字段名的地方；
   `models/request_forecast/` 已经只认角色名，别往里塞映射。
3. **`build_gold` 的 `scoring` 段 + F5 的 loader**（design §5 方案 A）。
   形状同种子表：读全部 artefact → `SELECT * FROM (VALUES ...)`，
   F5 因此仍是 R4 的四步整表重建，而**被 purge 的是表不是 artefact**。
4. **A3b 版本保全实测**——训第二个版本、重建 F5，确认第一版的行还在。
   design §5 那条冲突只有这一种验证方式。
5. 然后才进 L3-b（F6/F7），O1 已经不挡路了。

### 7.3 这轮踩过的坑

见 §6「本轮新增」七条。最费时的两条：计算节点**没有 `aws`/`mc`**，
以及 **`.env` 不会自动进 shell**（报出来是 `KeyError`，看着像配置缺失）。

### 7.4 还没验证的

- **A1d 从未执行**：`.venv-ml` 建出来了、42 项测试在里面跑过，但
  **没有核过 `pyspark.__version__`**。O15 的教训正是「uv 不报冲突、lock 还写着
  旧版本」，所以这一条是要**实际 import 出来看**的，不是推理。
  ⚠️ 但注意 `.venv-ml` 里**本来就不该有 pyspark**（`ml` extra 不含它），
  所以 A1d 该核的是**主 `.venv` 里的 pyspark 仍是 3.5.1**，
  即「装了 ml extra 之后主环境没被动过」。§2 A1d 那条命令写的是
  `--python .venv-ml`，**方向反了，执行前先改**。
- **`models/request_forecast/` 没跑过一行真实数据。** 42 项单测全在合成面板上，
  真实面板 2,178 格是什么形状（缺失、极端值、`accum_flag` 的分布）一无所知。
