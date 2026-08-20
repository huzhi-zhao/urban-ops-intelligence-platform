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

- [ ] **P1 修 O17** —— `silver_service_request` 的 08-17/18/19 三天。
      先查 Bronze 到底有多少条，**不要直接回填**：manifest 的 `record_count`
      对上 8 就是上游真没数据，对不上才是 Silver 那侧的问题。

```bash
aws --endpoint-url "$S3_ENDPOINT_URL" s3 cp \
  "s3://$S3_BUCKET_NAME/bronze/raw/SRC-WPG-311/service_requests/2026-08/manifest_2026-08-18.json" - \
  | python -m json.tool | grep -i record_count
```

结果：`record_count = ____`（Silver 侧当前为 **8** 行）

回填（幂等，按日分区覆盖）。⚠️ **触发前先确认 paused 状态**：

```bash
docker exec -it airflow-scheduler airflow dags details \
  dag_backfill_silver_service_request -o yaml | grep is_paused
# is_paused: False 才继续；否则先 unpause，再用 details 复查（unpause 的输出不可信）
docker exec -it airflow-scheduler airflow dags trigger \
  dag_backfill_silver_service_request \
  --conf '{"start": "2026-08-12", "end": "2026-08-20"}'
```

- [ ] 回填后逐日行数（工作日应在 ~3,000 量级）

| 日期 | 回填前 | 回填后 |
|---|---|---|
| 2026-08-17 | ____ | ____ |
| 2026-08-18 | 8 | ____ |
| 2026-08-19 | 无分区 | ____ |

- [ ] **P2 `ONLY=facts` 重跑**，对 design §2.1 的五个行数。
      §4.13 已论证不该变，**但那是推理不是实测**。

```bash
TRINO_HOST=localhost TRINO_PORT=8090 make gold-build ONLY=facts
```

| 表 | 期望 | 实测 |
|---|---|---|
| `fact_plow_shift` | 418 | ____ |
| `fact_parking_ban` | 49（19 匹配 / 30 NULL） | ____ |
| `fact_event_zone_rank` | 418 | ____ |
| `fact_service_request_zone_event` | 13,068 / 2,178 / 1,298 / 非零 ≥880 | ____ |
| `fact_winter_request_daily_by_label` | 141,377 / 18 个年份 | ____ |

耗时：____ 秒（L2 实测 14 分钟，97% 在 F1 + F8 两张 19 分片的表上）

- [ ] **P3 三条探针复跑**，刷新会漂的数。**这一步的产出就是本篇的价值之一。**

```bash
uv run python -m scripts.analysis.snowfall_events \
  --thresholds 3 --accum-window-days 10 --accum-threshold-cm 10 --zone-panel
uv run python -m scripts.analysis.score_collinearity --threshold 3
```

| 量 | 2026-08-09 台账 | 2026-08-19 复测 | 本次（____） |
|---|---|---|---|
| 事件数 N / 排班期 | 99 / 59 | 99 / 59 | ____ |
| 22 分区面板非零率 | 70.57%（916） | 69.8%（≈906） | ____ |
| Gold F1 排班期非零格 | — | 908 | ____ |
| `r(顺位, 请求量)` | +0.017 | — | ____ |
| `r(请求量, 天气)` | +0.460 | — | ____ |

🔴 **N 变了就是 design O4 触发**：面板格数、374/924、回测次数全要跟着改，
**但不改 schema**。

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

- [ ] A1a `pyproject.toml` 加 `[project.optional-dependencies] ml`
- [ ] A1b `make test-ml` target（照抄 `test-dags` 的形状）
- [ ] A1c CI 加 `ml` job
- [ ] A1d 装完核一次版本，别再被静默覆盖一遍

```bash
uv run --python .venv-ml python -c "import pyspark, statsmodels, pandas; \
print(pyspark.__version__, statsmodels.__version__, pandas.__version__)"
```

结果：pyspark = ____（**必须是 3.5.1**）

#### A2 代码

- [ ] `models/request_forecast/{features,model}.py` —— 角色名，不出现城市字面量
- [ ] `config/models/m1.yaml` —— 特征清单 / 切分 / `model_version` 前缀
- [ ] `scripts/models/train_m1.py` —— 读 Trino → 训练 → 写 artefact
- [ ] `scripts/gold/build_gold.py` 加 `scoring` 段与 F5 的 loader
- [ ] `tests/unit/test_m1_features.py` · `test_m1_model.py`

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

- [ ] **B0 先定 O1**：`load_score` 在 `partial_no_rank` 上给不给值。
      定完记进 §4，再动 SQL。

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

（待填）

预留的三处，开工时大概率要落笔：

- **O1 的裁决**（`load_score` 在 `partial_no_rank` 上给不给值）
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

本轮新增：

（待填）
