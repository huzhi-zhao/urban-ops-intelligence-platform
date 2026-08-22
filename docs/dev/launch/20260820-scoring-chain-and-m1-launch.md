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
- [x] `scripts/models/train_m1.py` —— 读 Trino → 训练 → 写 artefact ✅ `7807995`
- [ ] `scripts/gold/build_gold.py` 加 `scoring` 段与 F5 的 loader
- [x] `tests/unit/test_m1_features.py`（25）· `test_m1_model.py`（17）✅ **42 passed**
- [x] `tests/unit/test_train_m1.py`（32）✅ **`make test-ml` 共 74 passed**

**`train_m1.py` 的三个决定，各自有理由（2026-08-21）**：

1. **取数加了 dump/load 接缝**，不是一步到位读 Trino。
   `--dump-panel` 在计算节点上把面板存成 parquet/csv，`--panel-file` 在任何
   机器上离线训练。理由不只是方便：Trino 在计算节点上，开发机连不到,
   没有这条缝，「真实面板 2,178 格长什么样」就只能等到上节点那天才知道,
   而 §7.4 已经把「没跑过一行真实数据」列为风险。
   单测钉死这条缝**无损**——dump 前后训出同一个 `model_version`。
2. **`model_version = {prefix}-{date}-{fingerprint}`**，fingerprint 是
   config + 面板内容的 sha256 前 8 位。一个决定买下两件事：
   输入没变 → 同版本 → 重写同一份 artefact（**门禁 a6**）；
   输入变了 → 新版本 → **不可能静默覆盖**旧回测所对应的预测（**A3b 的前提**）。
   版本保全因此是设计的推论，不是「记得别覆盖」。
3. **只写 artefact，不碰表**（design §5）。artefact 落
   `gold/_forecast_runs/`，**刻意不在任何 Gold 表前缀之下**——`build_gold`
   会 purge 那些前缀，purge 到 artefact 就等于删掉它存在的理由。

🔴 **单测抓出一个真缺陷，值得记**：`to_csv` 把 `0.30000000000000004` 写成
`0.3`，实测偏移 **5.6e-17**。后果不是精度问题而是**版本身份问题**——同一份
面板经 dump 之后指纹不同，F5 里会凭空多出一个 `model_version`，而 F5 按设计
就是累积的，没有任何门禁能把它和一次真实重训区分开。
修法：指纹走 `_canonical_frame()` 归一化 dtype（Trino 给 `Decimal`、parquet 给
int64/bool、CSV 给字符串），浮点取 9 位小数。上下界各一条单测：
1e-15 的噪声**不**产生新版本，1e-6 的真实改动**必须**产生新版本。

⚠️ 本段只用合成面板跑过。CLI 端到端通了（18 格的玩具面板 → artefact 两个文件
→ 列名与 F5 契约逐个对上），但**真实面板一行都还没读过**。

三条设计红线**落成了代码而不是注释**，这是本段唯一值得强调的事：

| 红线 | 实现 | 单测 |
|---|---|---|
| `shift_number`/`rank_factor` 不进特征 | `feature_names()` 见到就抛，`build_design_matrix()` 再拦一道 | 两头各 2 项 |
| 滞后统计不跨切分边界 | 严格 `shift(1)` 因果，**不按 split 分别算** | 4 项 |
| 严禁随机切分 | `assert_no_history_leak` 查「训练事件都早于留出事件」 | 含一项**专门喂随机切分**，确认它被抓住 |

`evaluate()` 一次返回模型 + 基线两组指标，**没有只返回模型指标的函数**——
BO §4.4 那条「没有基线的模型结论不予采信」因此不依赖谁记得。

#### A3 训练与装载

⚠️ **本节原写的 `--model-version m1-poisson-v1` 这个参数不存在**：版本由
config + 面板内容派生（见 A2），手工指定会把 a6/A3b 的保证一起绕过。
实际命令是下面这组,**在计算节点上跑**：

🔴 **`uv run --python .venv-ml` 是错的**，本节此前两处都这么写，
实测在计算节点上报 `No interpreter found for executable name '.venv-ml'`。
`--python` 要的是一个**已存在**的解释器，而节点上根本还没建过这个环境。
正确写法是 Makefile 里那一种——`UV_PROJECT_ENVIRONMENT` **指定环境目录**，
不存在就建、并按 extra 同步：

✅ **已实测（2026-08-22）**：下面这组照跑即可。第 1 步在节点上跑时 uv 自己
建了 `.venv-ml` 并装 35 个包（906 ms），不必提前准备环境。

```bash
# 1. 先把面板取下来看一眼——真实面板从没被读过，别一步到位
UV_PROJECT_ENVIRONMENT=.venv-ml TRINO_HOST=localhost TRINO_PORT=8090 \
  uv run --extra ml python -m scripts.models.train_m1 \
    --dump-panel var/m1-panel.parquet

# 2. 离线训练 + 落 artefact（这一步不需要 Trino，可以搬到开发机上跑）
UV_PROJECT_ENVIRONMENT=.venv-ml \
  uv run --extra ml python -m scripts.models.train_m1 \
    --panel-file var/m1-panel.parquet --upload

# 3. 装载进 F5
TRINO_HOST=localhost TRINO_PORT=8090 make gold-build ONLY=scoring
```

⚠️ 第 3 步的 `ONLY=` 是 **`scoring`**，不是 `fact_request_forecast`（当前两者
等价）。F5 被放进一个**独立的 stage**：`--only facts` 是 Silver 修数之后的重建
入口，而 F5 的输入是训练产物、与 Silver 窗口无关，两者不该被同一条命令带上。

🔴 **第 1 步的输出就是 a1 门禁**：面板不是 2,178 格，`train` 会直接抛并在
错误里指向 design O4——「N 漂了是口径问题，不是代码 bug」。这条正是 P3 复跑
要冻结 N=99/59 的原因,两件事在这里合上。

- [x] A3a artefact 落盘 —— ✅ **2026-08-22**

⚠️ 计算节点没有 `aws`（§6 本轮新增第一条），下面这条才是可执行的：

```bash
set -a; source .env; set +a
uv run python -c "
from ingestion.loaders.s3_client import build_s3_client, load_s3_settings
s = load_s3_settings(); c = build_s3_client(s)
for o in c.list_objects_v2(Bucket=s.bucket_name, Prefix='gold/_forecast_runs/').get('Contents', []):
    print(o['Key'], o['Size'], o['LastModified'])
"
```

实测落盘两个对象，`model_version` = **`m1-poisson-20260822-df31d954`**：

```
gold/_forecast_runs/m1-poisson-20260822-df31d954/predictions.csv
gold/_forecast_runs/m1-poisson-20260822-df31d954/metrics.json
```

- [x] A3b 🔴 **版本保全实测** —— ✅ **2026-08-22，两个版本共存，门禁全绿**

🔴 **第二个版本必须换一份 config，不能只是「再训一次」。**
`model_version` 的指纹哈希的是 **config + 面板内容**，同一天用同一份 config
重训得到的是**完全相同的版本号**，只是原样重写同一个 artefact——那什么都验证不了。

也**不要**只改 `random_seed`：那会得到新版本号但预测值逐行相同，届时若真有一个
把行错标到版本上的缺陷，你看不出来。删掉一个特征才能让两版预测值真的不同：

```bash
sed 's/^    - month$//; s/^model_version_prefix: m1-poisson$/model_version_prefix: m1-poisson-nomonth/' \
  config/models/m1.yaml > /tmp/m1-nomonth.yaml

UV_PROJECT_ENVIRONMENT=.venv-ml uv run --extra ml python -m scripts.models.train_m1 \
    --panel-file var/m1-panel.parquet --config /tmp/m1-nomonth.yaml --upload

TRINO_HOST=localhost TRINO_PORT=8090 make gold-build ONLY=scoring
```

实测：`m1-poisson-nomonth-20260822-30af82f4`，**MAE 7.919**（原版 7.345，
基线不变 23.628）——删掉 `month` 模型确实变差，**这同时证明 `--config` 生效了**。

重建输出：

```
2 artefact(s): m1-poisson-20260822-df31d954, m1-poisson-nomonth-20260822-30af82f4
chunk 1/2 · chunk 2/2 · 2,596 rows in 3.8s
[ok] one full panel per artefact: 2 x 1,298 rows -> 2,596
[ok] no version lost in the rebuild: 2 distinct model_version -> 2
```

🔴 **门禁只证明了「有 2 个版本 × 1,298 行」，没证明 v1 的值没被改写。**
完整判据要拿重建**前后**的同一条 SQL 对：

```sql
SELECT model_version, COUNT(*) AS n, SUM(predicted_count) AS sp, SUM(baseline_count) AS sb
FROM uoip_gold.fact_request_forecast GROUP BY model_version;
```

| | n | sp | sb |
|---|---|---|---|
| v1，重建**前** | 1,298 | 31961.938969663406 | 25925.673216519925 |
| v1，重建**后** | **1,298** | **31961.938969663406** | **25925.673216519925** |
| v2 (`nomonth`) | **1,298** | **32451.5406691589** | **25925.673216519925** |

✅ **v1 三个数逐位相同**，连浮点尾数都没动 —— 重建没有丢版本，也没有改写值。
**A3b 闭合，design §5 的方案 A 实测成立。**

🟢 两个附带信号，都指向"对"的方向：

- v2 的 `sp` 与 v1 **不同**，`--config` 确实换掉了模型（否则这轮什么都没证明）。
- 两版的 `sb` **完全相同**，这是应当的：基线是**面板的属性**，不是模型的——
  同一个面板换什么特征，因果扩展均值都不该动。**它跟着变了才是出事**，
  所以这一格相等本身就是一条独立的正确性检查。

⚠️ **会看着像故障但不是**：v1 那 1,298 行的 `built_at` 与 `etl_run_id` **会变**。
整表重建，所有行都是本次 run 写的；不变的是业务列。这正是 artefact 方案的形态
——**artefact 是记录，表是它的视图**。

/tmp 那个 `nomonth` 版本**暂时留在 F5 里**：L3-b 的 F7 要「对 F5 里每个
`model_version` 各算一遍」，有两个版本正好把那条路径压到。要清的话再说——
删 artefact 是这套设计里唯一真正不可逆的动作。

#### A4 门禁表（跑完逐条填）

| # | 判据 | 期望 | 实测 |
|---|---|---|---|
| a1 | 训练面板格数（跨类别聚合后） | 2,178 | ✅ **2,178**（2026-08-22） |
| a2 | F5 行数 / 每个 `model_version` | 1,298 | ✅ **1,298**（两个版本各自，门禁绿） |
| a3 | `baseline_count IS NULL` 的行 | 仅最早雪季，**逐条能解释** | ✅ **0 行**——artefact 已滤掉无历史的格，见下 |
| a4 | 🔴 留出季 MAE：模型 vs seasonal-naive | **成对记录** | 模型 **7.345** / 基线 **23.628** |
| a4b | Poisson deviance：模型 vs 基线 | 成对 | **8.150** / **36.757** |
| a4c | 留出的是哪个雪季 | `snow_season` 最大值 | ✅ **2025-2026**（154 行 = 22×7 事件） |
| a5 | 特征矩阵不含 `shift_number` / `rank_factor` | 单测绿 | ✅ |
| a6 | 同 `model_version` 重跑，行数与预测值不变 | 一致 | ✅ v1 重建前后 `n`/`sp`/`sb` **逐位相同**（§2 A3b） |
| a7 | `make lint` + `test-unit-offline` + `ml` job | 全绿 | ✅ lint 干净 · **883 passed / 6 skipped** · `test-ml` **36 passed** |

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
| `fact_request_forecast` | 1,298 × 版本数 | **1,298**（1 版）→ **2,596**（2 版） | 3.6s / 3.8s |
| `fact_winter_event_zone_load` | 1,298 | ____ | ____ |
| `fact_recommendation` | 374 × 版本数 | ____ | ____ |

### 3.2 DQ 基线（17 张表）

> `make gold-dq` 的 markdown 直接贴这里，不要手抄。
> **这是后续告警阈值的唯一依据**，没有基线的阈值都是拍脑袋。

（待填）

### 3.3 M1 评估（实测 2026-08-22）

| | 模型 | 基线（因果扩展均值） |
|---|---|---|
| 留出季 MAE | **7.345** | **23.628** |
| Poisson deviance | **8.150** | **36.757** |

留出雪季：**2025-2026**（154 行 = 22 分区 × 7 事件）
｜ `model_version`：**`m1-poisson-20260822-df31d954`**
｜ 面板：**2,178** 训练格 / **1,298** 预测格

**结论：模型优于基线，且不是勉强赢**（MAE 少 69%，deviance 少 78%）。
按 design §9 的口径可以讲——但**必须连着下面三条保留一起讲**，
BO-8 §0.2.2 那条「可比事件仅 15 个」并没有因为这组数字而失效。

#### 三条必须一起讲的保留

1. 🔴 **留出季只有 7 个事件**，7.345 这个 MAE 的置信区间很宽。
   雪季之间格数极不均衡：2021-2022 有 242 格（11 事件），
   2024-2025 只有 **44 格（2 事件）**。**「优于基线」仍不能升格为可辩护的
   公开结论**，只能如实陈述为「在这一季的 154 格上，模型误差更小」。
2. 🔴 **目标高度零膨胀**：`request_count` 的 25 分位 = **0**、中位 **3**、
   均值 21、最大 381。至少四分之一的格子是零，分布极度右偏。
   Poisson GLM 在这种形状上容易低估尾部，而 **F6 消费的是排序不是取值**——
   尾部低估会不会改变分区排序，是 **L3-b 要单独看的一件事**，
   不能因为 MAE 好看就跳过。
3. 🟡 **日志里的 "seasonal-naive" 是个错名。** 实现在
   `models/request_forecast/model.py:98`，语义是**每个分区在其严格更早的事件
   上的扩展均值**（即 §4.3 定案的因果扩展均值），不是「上一季同期」。
   函数名与日志字符串对不上实现，只读日志的人会**误判基线强度**。
   `metrics.json` 里写的是对的（`"seasonal_naive (causal expanding mean)"`）。

#### 面板本身（`--dump-panel` 的第一次实测）

**零缺失**——13 列一个 NaN 都没有，比预想的好。三条交叉验证自己合上了：

| 观察 | 数值 | 它验证了什么 |
|---|---|---|
| `is_scheduling_era` True / False | **1,298 / 880** | 1,298 = 22×59 正是 F5 期望行数；880 = 22×(99−59)。**N=99/59 在 Gold 侧独立复现了一次**，不靠探针背书 |
| `duration_days = 1` | 1,188 格 = **54 个事件** | 与 BO-3「中位时长 1.0 日」一致 |
| `accum_flag = True` | 176 格 = **8 个事件** | 阈下累积那条补充判据确实在起作用，不是摆设 |

其余分布：`address_count` 1,414–22,480（22 个分区各自恒定）·
`total_snowfall_cm` 0.21–29.05 · `min_temperature_c` −35.6–+1.0 ·
`severity_score` 0.063–0.898 · 事件覆盖 18 个雪季（2008-2009 … 2025-2026）。

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

### 4.4 F5 的 loader：`scoring` 是独立的 stage（2026-08-22）

design §5 定了 artefact 方案，没定它在 `build_gold` 里长什么样。实现时定了四件事：

1. **F5 单独一个 stage `scoring`，不并进 `facts`。**
   `--only facts` 是 Silver 修数之后的重建入口（L2 §7.2 用过一次），而 F5 的
   输入是训练产物、与 Silver 窗口毫无关系。并在一起意味着每次修 Silver 都会
   顺手重建一次 F5——**结果正确但毫无必要**，而且把「这张表依赖什么」讲错了。

2. **一个 artefact 渲染一条 `INSERT`**，不是把所有版本拼成一条巨型 `VALUES`。
   每条语句因此恒为 1,298 元组，与累积了多少版本无关；某个版本装载失败时
   报的是**它自己的名字**，而不是一条大语句整体失败。

3. 🔴 **artefact 在 `DROP` 之前读。**
   它们是旧版本的**唯一副本**，先读意味着「artefact 缺失或损坏」这类错误在
   旧表还立着的时候就炸掉，而不是在表已经 drop + purge 之后。

4. **两条门禁的期望值是动态算的**（`_dynamic_extra_gates`）：
   总行数 = `版本数 × 1,298`、`COUNT(DISTINCT model_version)` = 版本数。
   两者都不是 schema 的属性，写不成常量——但**它们恰恰是 design §5 全部主张的
   可执行形式**：「重建不丢版本」。没有它们，「purge 吃掉了两个版本」和
   「本来就只训过一个」建出来的表**长得一模一样**。
   每个版本 1,298 行那条是静态的，写进 `extra_gates`；1,298 这个数**读
   `config/models/m1.yaml`**，与训练侧同一个 key，不抄第二遍。

另外两件顺带确认的：

- `_purge_storage` 清的是 `gold/fact_request_forecast/`，
  artefact 在 `gold/_forecast_runs/` —— **两个前缀互不包含**，
  单测 `test_the_purged_prefix_is_the_table_not_the_artefacts` 把这条钉死了。
  §5 第 3 条那句「别哪天加张叫 `_forecast_runs` 的表」因此有了自动检查。
- `scripts/gold/forecast_artefacts.py` **不 import pandas**，也不 import
  `train_m1`——`build_gold` 跑在没有 `ml` extra 的环境里。artefact 用 CSV
  正是为了「写的人要 pandas，读的人不要」。代价是 `ARTEFACT_ROOT` /
  `PREDICTIONS_FILE` / 列序被写了两遍，由 **ml 测试套件**里的
  `test_forecast_artefact_constants_match_the_trainer` 对齐（那边两个包都有）。

代码：`scripts/gold/forecast_artefacts.py` + `build_gold` 的 `from_artefacts`
分支，17 项单测（`tests/unit/test_forecast_artefacts.py`）+ ml 侧 2 项。
`make lint` 干净 · `test-unit-offline` **883 passed / 6 skipped** ·
`test-ml` **36 passed**。**只跑过 dry-run 与合成 artefact，没对真实 MinIO 跑过。**

### 4.5 a3 实测是 **0 个空**，而 DDL 说「最早的事件为 NULL」（2026-08-22）

F5 的 `baseline_count` 注释写的是 *Null only for the earliest events with no
prior history to average over*，§4.3 定案时也是这么推的。**实测 0 行为空。**

不是缺陷，是两层过滤的先后：`feat.drop_rows_without_history()` 在训练之前就把
「没有更早事件」的格**整行删掉**了，所以它们根本不会进预测面板，也就没有机会
以 NULL 的形式出现在 F5 里。1,298 这个数本身已经是过滤之后的。

结论与处置：

- **门禁 a3 保持「0 个空」的等值形式**，它现在检查的是
  「artefact 留下的行都算得出基线」——比原本设想的形状更强，不是更弱。
- **DDL 那句注释描述了一个不可能出现的状态**。契约自 2026-08-13 冻结，
  **不动**；以本节为准。要真改得走变更流程，和五张事实表 DDL 头注里
  `= 916` 那条一样（L2 launch §4.9）。


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

🟡 **环境变量前缀打错一个字符，报出来是 60 行 urllib3 traceback。**
实测把 `TRINO_HOST=...` 敲成了 `xTRINO_HOST=...`：那一项变成了一个没人读的变量，
`TRINO_HOST` 于是回落到 `.env` 的**容器视角** `trino`，而 `TRINO_PORT=8090` 照常
生效——所以栈底那行是 `host='trino', port=8090`，一个**两边各对一半**的组合。
判读要点：traceback 里 `Failed to resolve 'trino'` 就是「前缀没生效」，
不是 Trino 挂了。而且 trino 客户端会自己重试，看起来像卡住。

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
- ✅ **P3 的两条公开 API 探针复跑**（§1 P3）：**N=99/59 不动**，O4 未触发。
- ✅ **A1d 关闭**（§7.4）：两个环境的 pyspark 都是 3.5.1。
- ✅ **`scripts/models/train_m1.py` + 32 项单测**（`7807995`，§2 A2）。
  `make lint` 干净 · `make test-ml` **74 passed** · `make test-unit-offline`
  **866 passed** 未受影响。**只跑过合成面板。**

- ✅ **A1/A3a/A4 的 a1/a4/a4b/a4c/a5/a7 已实测（2026-08-22，§2 A3 + §3.3）**：
  真实面板 **2,178 格、零缺失**，模型 MAE **7.345** vs 基线 **23.628**，
  留出季 2025-2026。artefact 已上传 MinIO，
  `model_version` = `m1-poisson-20260822-df31d954`。
  🔴 **三条保留必须跟着结论一起讲**，见 §3.3。
- ✅ **F5 的 loader 已完成并跑通生产**（§4.4）：`scripts/gold/forecast_artefacts.py`
  + `build_gold` 的 `scoring` stage，19 + 2 项单测。
  第一次真实构建 **1,298 行 / 3.6 秒**、五条门禁全绿（§2 A3）。
- ✅ **A3b 版本保全实测通过并闭合**（§2 A3b）：两个版本共存 **2,596 行**，
  动态门禁全绿，且 **v1 重建前后三个聚合数逐位相同**。
  design §5 的方案 A **实测成立**，不再是设计推论。
- 🔴 **a3 实测是 0 个空**，与 DDL 注释说的「最早事件为 NULL」不符——
  不是缺陷，是过滤发生在预测之前，见 §4.5。

### 7.2 下一步，按顺序

1. ✅ **P3 的两条探针已复跑**（2026-08-21，§1 P3 表格已填）：**N=99/59 不动，
   design O4 未触发**，L3-a 的 2,178 格与 L3-b 的 374/924 口径不变。
   清空缓存后 69.8% 原样复现，三条相关系数逐个相同。
   **仍欠的是要连 Trino 的那两条**——Gold F1 排班期非零格（期望仍 908、下界 880）
   与 **P5** `dim_plow_event` 的 19/17/17（374/924 的推导前提）。
   下次上计算节点时**先跑这两条**，命令在 §1 P3 与 P5。
2. ✅ **A2/A3a 已完成**（2026-08-22）：真实面板取下、训练跑通、artefact 上传。
   数字在 §3.3，门禁在 §2 A4。§7.4 那条「没跑过一行真实数据」**就此了结**。
3. ✅ **F5 的 loader 已跑通生产**（§2 A3、§4.4）。
4. ✅ **A3b 已闭合**（§2 A3b），v1 的值逐位不变。
5. 然后才进 L3-b（F6/F7），O1 已经不挡路了。

### 7.3 这轮踩过的坑

见 §6「本轮新增」七条。最费时的两条：计算节点**没有 `aws`/`mc`**，
以及 **`.env` 不会自动进 shell**（报出来是 `KeyError`，看着像配置缺失）。

### 7.4 还没验证的

- [x] ✅ **A1d 已执行（2026-08-21）：两个环境的 pyspark 都是 3.5.1，没被动过。**
  O15 那种「uv 不报冲突、lock 还写着旧版本」的覆盖**没有发生**。
  `.venv-ml` 实测 `pyspark 3.5.1` + `statsmodels 0.14.6` + `pandas 3.0.5`。

  🔴 **顺带更正本节此前写的两条错话**：

  1. 「`.venv-ml` 里本来就不该有 pyspark」是错的——`ml` 是
     `[project.optional-dependencies]` 的一个 extra，装它会**连主依赖一起装**，
     所以 `.venv-ml` 里有 pyspark 是正常的，该核的是**版本对不对**（3.5.1 ✅），
     不是**在不在**。§2 A1d 那条 `--python .venv-ml` 因此**方向没反**，照跑即可。
  2. 探一个 venv 里装了什么，**必须用它自己的解释器**
     `.venv-ml/bin/python -c ...`。本次先用 `uv run --python .venv-ml` 探，
     报出 `statsmodels` 缺失而 `pyspark` 存在——**两条都是假的**：
     `uv run` 会按项目环境自己解析依赖，`--python` 只换解释器不换包集合。
     差点据此得出「ml extra 没装上」的错误结论。

  ⚠️ `pandas` 实测是 **3.0.5**，不是 2.x。`pyproject.toml` 写的是 `>=2.0`，
  形式上满足，但 pandas 3.0 是有破坏性变更的大版本；42 项单测在它下面全绿，
  真实面板跑通之前先别把这当成已验证。
- [x] ✅ **已了结（2026-08-22）：`models/request_forecast/` 跑通了真实面板。**
  2,178 格、**零缺失**，形状记在 §3.3。`pandas 3.0.5` 上真实面板跑通，
  §7.4 上面那条对 pandas 大版本的保留**可以撤了**。
- [x] ✅ **loader 已对真实 MinIO 跑过**（2026-08-22，§2 A3/A3b）：
  单版本 1,298 行、双版本 2,596 行，两趟门禁全绿。`LastModified` 时区与
  pandas 的空 `baseline_count` 两个悬念都没出事——不过 `baseline_count`
  实测**一个空都没有**（§4.5），所以空值那条路径**真实数据其实没走到**。
- [x] ✅ **v1 的值在重建后没被改写，已验证**（2026-08-22，§2 A3b 表格已填满）。
