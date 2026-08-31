# 上线记录：BO 循环 EDA 与呈现层 SQL

> **设计**: [design/20260827-bo-eda-and-presentation-sql.md](../design/20260827-bo-eda-and-presentation-sql.md)
> **台账（结论与图表落在这里）**: [requirements/bo-conclusions-and-figures.md](../requirements/bo-conclusions-and-figures.md)
> **Status**: 阶段 0–4 已完成（2026-08-30 → 08-31，§17）· **阶段 5a 已验收（§18.8）** · **阶段 5b 已落仓待验收（§19）** · **开始**: 2026-08-27
>
> 本篇是**执行计划**，随每一轮 EDA 追加，不回改已写下的数。
> 设计篇冻结口径，本篇记实际发生的事——包括与设计不符的地方。

---

## 0. 怎么用这篇文档

工作是**循环**的，一轮一个 BO，一轮一停（design C1）：

```
文档给出 SQL  →  你在线上跑  →  结果贴回  →  我总结成结论 + 定图  →  进下一轮
```

**本篇当前已展开到阶段 4（§16 提问单 / §17 结果）。** 阶段 5 是建载体，不再是提问单
——第 2 轮问什么取决于第 1 轮答什么，批量出题会得到一批问错问题的 SQL。

### 0.1 两种执行方式

**简单查询 —— 直接用容器里的 Trino CLI**（容器名 `trino`，宿主端口 8090）：

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold --execute "SELECT 1"
```

多行 SQL 走 stdin（无需临时文件）：

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
SELECT count(*) FROM fact_event_zone_rank;
SQL
```

⚠️ **stdin 这条路本轮是第一次用**，P0 里有一条专门验它。若不通，退回到
`--execute "$(cat <<'SQL' ... SQL
)"`，或把 SQL 写进 `/tmp/q.sql` 后 `docker cp` 进容器再 `--file`。

**复杂查询 / 需要跨库比对 —— 在线上仓库用 Python CLI**：

```bash
cd <repo> && TRINO_HOST=localhost TRINO_PORT=8090 uv run python -m scripts.gold.talking_points
```

🔴 **宿主机 shell 连 Trino 必须显式加 `TRINO_HOST=localhost TRINO_PORT=8090`**
——`.env` 里的 `trino:8080` 是给 Airflow 容器的视角（`.env` 只存容器视角，这是规则）。

🔴 **探针（`scripts/analysis/*`）打的是公开 API，不是 Gold。** 它和管道的数**今天就会差
1–2 格**（F1 的 908 vs 906，L2 launch §4.9）。用它交叉验证时，先记住这是两个数据源。

### 0.2 结果怎么贴回

原样贴 CLI 输出即可，**不要手工整理成表**——对齐时容易丢行，而「少了一行」正是
最有价值的信号。若某条查询报错，也把报错原样贴回：**报错和结果一样是数据**
（L2 的四个必然失败的缺陷全是这么找到的）。

---

## 1. 阶段地图

| 阶段 | 覆盖 | 状态 | 结果记在 |
|---|---|---|---|
| **0** 前置核对 | 认证状态 · 构建批次 · 载体连通性 · CLI 形态 | ✅ 已完成（2026-08-30） | §4 · §4.1 |
| **1** BO-2 排班顺位（P0−） | F2 · F3 · D1 · `dim_plow_event` · F4 | ✅ 已完成（2026-08-30）。Q11/Q12 已回，**Q13 只回了 3/22 行** | §4（表）· §4.3–§4.6 · §4.10 · §8 |
| **2** BO-4 空间对齐（P0−） | D3 · D2 · D1 | ✅ 已完成（2026-08-30）。B8/B9 已回，**派生出 B10/B11** | §4.8 · §4.9 · §4.10 · §11 · §12 |
| **3** BO-3 + BO-1 | D4 · F1 · F8 · F5 · D5 | ✅ **已完成并收口**（2026-08-31） | §13.1（判据）· §14 · **§15**（收尾）|
| **4** BO-6 + BO-8 | F6 · F7 · D7 | 🚧 **下一轮展开** | — |
| **5** 定稿与落地 | `sql/presentation/` · 三个载体 | ⏸ 未展开 | — |

---

## 2. 阶段 0+1 的提问单

判据**先于结果写**（design §3.1）。「期望」一列是跑之前就该知道的数，
**跑出来不一致不等于错**——它可能是数漂了（design C5），也可能是这条判据本来就问错了
问题。两种都要查，但结论不同。

### 2.1 阶段 0 —— 前置核对（4 条）

| # | 问题 | 期望 | 出处 | 不符时 |
|---|---|---|---|---|
| P0 | 多行 SQL 走 stdin 能不能跑 | 返回 `1` | — | 换 §0.1 的两条退路 |
| P1 | 最近一次 Gold 认证是什么状态 | `certified`，`error_count = 0` | ADR 0012 三态 | `suspect`/`unknown` → **先查审计，别急着出图** |
| P2 | BO-2 五张表的构建批次可不可比 | 每表恰好一个 `etl_run_id`；`source_max_ingest_date` 全等；事实表 `built_at` 不早于维表 | R4 整表重建 | 上游截止日不等，或维表比事实表新 → 先重建再 EDA |

> ⚠️ **P2 的判据在 2026-08-30 首跑后改过一次**：原写「五张表 `etl_run_id` 一致」，
> 而 `ONLY=dims` / `ONLY=facts` 部分重建是受支持的正常操作，该状态不可达。见 §4.1。
| P3 | Grafana 的数据源连的是不是 Trino | — | design O1 | 未连 → OPS 三张图降级 Superset，**不新装插件**。实测：已连，且已有三块 BO 面板（§4.2） |

### 2.2 阶段 1 —— BO-2（10 条）

| # | 问题 | 期望 | 出处 |
|---|---|---|---|
| Q1 | 每个分区的平均顺位与轮候时长 | 最快 ≈ 1.26 班、最慢 ≈ 3.47 班，差 ≈ 26 h；22 个分区 | Session Description（生效中的合同）· BO-2 §2.1 |
| Q2 | 顺位在前 9 次 / 后 10 次之间漂了多少 | 至少两个分区位移 > 1 个班次（V 1.89→3.20、M 1.78→2.80 量级） | BO-2「顺位不是常量」 |
| Q3 | 前后半期的 Spearman ρ | ≈ +0.59 | 同上 |
| Q4 | 顺位 × 地址数的 Pearson r（全期 / 2021 起） | +0.491 / +0.403，**方向为正** | BO-2 反证一（2026-08-09 首测，08-23 复现） |
| Q5 | 19 次犁雪与降雪事件的对齐情况 | 17 对齐 / 2 未对齐，未对齐的是 **2021-01-07 / 2026-02-26** | `dim_plow_event`；名单已于 2026-08-09 更正过一次 |
| Q6 | 顺位面板完不完整 | 418 = 19 × 22，无重复对 | L2 阶段 D 门禁 |
| Q7 | `shift_number` 的结构 | 取值 1..5，`rank_factor` ∈ [0.2, 1.0]，**从不为 0** | `dim_plow_event` / F3 DDL |
| Q8 | 停车禁令与犁雪的匹配 | 49 = 19 匹配 + 30 NULL（**语义不是缺数据**） | L2 阶段 D |
| Q9 | 一个班次计划时长是不是恒定 | 恒定（预期 12 h）——**Q1 的「× 12 小时」全靠它** | ADR 0008：`shift_end` 是计划值，不是完成时间 |
| Q10 | 25 个分区里有排班的有几个 | 22 有 / 3 无（B/D · X · Downtown），无排班分区地址占比 ≈ 6.0% | ADR 0008 · 批 0 实测 |

🔴 **Q9 是 Q1 的承重件。** 「26 小时」这个要上台的数 = 顺位差 × 班次时长。
班次时长若不恒定，Q1 的 `mean_wait_hours` 一列就是**编的**，那张图得换算法。
先跑 Q9 再信 Q1。

---

## 3. 执行

### 3.1 阶段 0

**P0 · CLI 形态验证**

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
SELECT 1 AS ok;
SQL
```

**P1 · 最近的 Gold 认证状态**

🔴 看 `certified_at`，**不只看 `status`**：审计自己坏掉那天，`unknown` 要等
`retries=3 × 5min` 耗尽约 16 分钟才落表，这期间最新一行仍是上一趟的 `certified`。

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_meta <<'SQL'
SELECT run_id, cadence, certified_at, status, error_count, warn_count,
       checks_total, checks_could_not_run
FROM gold_certification
ORDER BY certified_at DESC
LIMIT 5;
SQL
```

**P2 · BO-2 五张表的构建批次与行数**

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
SELECT 'fact_event_zone_rank' AS table_name, etl_run_id, MAX(built_at) AS built_at,
       MAX(source_max_ingest_date) AS src_max, COUNT(*) AS row_count
FROM fact_event_zone_rank GROUP BY etl_run_id
UNION ALL
SELECT 'fact_plow_shift', etl_run_id, MAX(built_at), MAX(source_max_ingest_date), COUNT(*)
FROM fact_plow_shift GROUP BY etl_run_id
UNION ALL
SELECT 'dim_plow_event', etl_run_id, MAX(built_at), MAX(source_max_ingest_date), COUNT(*)
FROM dim_plow_event GROUP BY etl_run_id
UNION ALL
SELECT 'dim_plow_zone', etl_run_id, MAX(built_at), MAX(source_max_ingest_date), COUNT(*)
FROM dim_plow_zone GROUP BY etl_run_id
UNION ALL
SELECT 'fact_parking_ban', etl_run_id, MAX(built_at), MAX(source_max_ingest_date), COUNT(*)
FROM fact_parking_ban GROUP BY etl_run_id
ORDER BY table_name;
SQL
```

判据：**五行**（每张表恰好一个 `etl_run_id`——多于一个说明 purge 没生效，R4 的
「`INSERT` 是追加」失败模式），行数 418 / 418 / 19 / 25 / 49。

**P3 · Grafana 数据源**（这条不是 SQL）

在 8092 的 Grafana 里看 Connections → Data sources，回答两件事：
① 有没有 Trino 数据源；② 现有那几张已跑通的图连的是什么后端。
若没有 Trino 数据源，**不要现装插件**——按 design O1 降级。

---

### 3.2 阶段 1 —— BO-2

> 全部只读 `uoip_gold`，最大的表 418 行，**不碰 Silver**，R1 的 4,878 分区墙不适用。

**Q9 先跑**（它是 Q1 的前提）：

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
SELECT date_diff('hour', shift_start_utc, shift_end_utc) AS planned_hours,
       COUNT(*) AS shifts,
       COUNT(DISTINCT plow_event_id) AS events
FROM fact_plow_shift
GROUP BY date_diff('hour', shift_start_utc, shift_end_utc)
ORDER BY planned_hours;
SQL
```

判据：**只有一行**。多于一行 → Q1 的小时数换算作废，改用「班次」为单位讲。

---

**Q1 · 分区平均顺位与轮候时长**（→ FIG-BO2-01）

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
SELECT plow_zone,
       COUNT(*) AS events,
       ROUND(AVG(CAST(shift_number AS DOUBLE)), 2) AS mean_shift,
       MIN(shift_number) AS min_shift,
       MAX(shift_number) AS max_shift,
       ROUND(STDDEV_SAMP(CAST(shift_number AS DOUBLE)), 2) AS sd_shift,
       ROUND((AVG(CAST(shift_number AS DOUBLE)) - 1) * 12, 1) AS mean_wait_hours
FROM fact_event_zone_rank
GROUP BY plow_zone
ORDER BY mean_shift;
SQL
```

判据：22 行、每行 `events = 19`；`mean_shift` 首尾 ≈ 1.26 / 3.47；
`mean_wait_hours` 极差 ≈ 26。
⚠️ 那个 `12` 是 Q9 的产物写死在这里的——**Q9 若不是 12，先改这条 SQL 再跑**。
`min_shift`/`max_shift` 是给图上的须用的：讲「平均」而不给离散度，就是在暗示恒定。

---

**Q2 · 顺位漂移（前 9 次 → 后 10 次）**（→ FIG-BO2-02，**这张图不画就等于在说
「十年没变」这句禁语**）

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
WITH ev AS (
    SELECT plow_event_id,
           ROW_NUMBER() OVER (ORDER BY first_shift_start_utc) AS seq
    FROM dim_plow_event
),
tagged AS (
    SELECT r.plow_zone,
           CAST(r.shift_number AS DOUBLE) AS shift_number,
           CASE WHEN e.seq <= 9 THEN 'early' ELSE 'late' END AS half
    FROM fact_event_zone_rank r
    JOIN ev e ON e.plow_event_id = r.plow_event_id
)
SELECT plow_zone,
       ROUND(AVG(CASE WHEN half = 'early' THEN shift_number END), 2) AS mean_early,
       ROUND(AVG(CASE WHEN half = 'late' THEN shift_number END), 2) AS mean_late,
       ROUND(AVG(CASE WHEN half = 'late' THEN shift_number END)
             - AVG(CASE WHEN half = 'early' THEN shift_number END), 2) AS drift
FROM tagged
GROUP BY plow_zone
ORDER BY drift;
SQL
```

判据：22 行；至少两个分区 `|drift| > 1.0`。
⚠️ **切分口径在这里是「事件序号 9/10 对半」，而探针用的是日期**（`--since 2021-01-01`）。
两者不必相等——**若要上台，图注必须写清用的是哪一种**，不能两个数混着讲。

---

**Q3 · 前后半期的 Spearman ρ**

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
WITH ev AS (
    SELECT plow_event_id,
           ROW_NUMBER() OVER (ORDER BY first_shift_start_utc) AS seq
    FROM dim_plow_event
),
halves AS (
    SELECT r.plow_zone,
           AVG(CASE WHEN e.seq <= 9 THEN CAST(r.shift_number AS DOUBLE) END) AS mean_early,
           AVG(CASE WHEN e.seq > 9 THEN CAST(r.shift_number AS DOUBLE) END) AS mean_late
    FROM fact_event_zone_rank r
    JOIN ev e ON e.plow_event_id = r.plow_event_id
    GROUP BY r.plow_zone
),
ranked AS (
    SELECT plow_zone,
           RANK() OVER (ORDER BY mean_early) AS rank_early,
           RANK() OVER (ORDER BY mean_late) AS rank_late
    FROM halves
)
SELECT COUNT(*) AS zones,
       ROUND(CORR(CAST(rank_early AS DOUBLE), CAST(rank_late AS DOUBLE)), 3) AS spearman_rho
FROM ranked;
SQL
```

判据：`zones = 22`，ρ ≈ +0.59。
🔴 **`RANK()` 遇到并列给的是「最小名次」，scipy 给的是平均名次**——有并列时两个 ρ
不会逐位相同。这个数要上台就**用探针的值**，本条只作数量级复核：

```bash
cd <repo> && TRINO_HOST=localhost TRINO_PORT=8090 \
  uv run python -m scripts.analysis.zone_schedule_rank --json var/probe-cache/bo2-rank.json
```

---

**Q4 · 顺位 × 地址数**（→ FIG-BO2-04，BO-2 的头号反证）

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
WITH ev AS (
    SELECT plow_event_id, first_shift_start_utc FROM dim_plow_event
),
z AS (
    SELECT r.plow_zone,
           AVG(CAST(r.shift_number AS DOUBLE)) AS mean_all,
           AVG(CASE WHEN e.first_shift_start_utc >= TIMESTAMP '2021-01-01 00:00:00'
                    THEN CAST(r.shift_number AS DOUBLE) END) AS mean_since_2021,
           COUNT(CASE WHEN e.first_shift_start_utc >= TIMESTAMP '2021-01-01 00:00:00'
                      THEN 1 END) AS events_since_2021
    FROM fact_event_zone_rank r
    JOIN ev e ON e.plow_event_id = r.plow_event_id
    GROUP BY r.plow_zone
)
SELECT COUNT(*) AS zones,
       MAX(z.events_since_2021) AS events_since_2021,
       ROUND(CORR(z.mean_all, CAST(d.address_count AS DOUBLE)), 3) AS r_all,
       ROUND(CORR(z.mean_since_2021, CAST(d.address_count AS DOUBLE)), 3) AS r_since_2021
FROM z
JOIN dim_plow_zone d ON d.plow_zone = z.plow_zone
WHERE d.address_count IS NOT NULL;
SQL
```

判据：`zones = 22`、`events_since_2021 = 11`、`r_all ≈ +0.49`、`r_since_2021 ≈ +0.40`。
**方向为正是这条反证的全部意义**：后排分区户数**更多**，所以「后排是因为人少所以被
忽略」解释不掉顺位。符号若翻了，BO-2 的核心结论要重开。

---

**Q5 · 犁雪与降雪事件的对齐**（→ FIG-BO2-05）

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
SELECT e.plow_event_id,
       CAST(e.first_shift_start_utc AS DATE) AS first_shift_date,
       e.is_aligned,
       e.matched_snowfall_event_id,
       e.ban_id
FROM dim_plow_event e
ORDER BY e.first_shift_start_utc;
SQL
```

判据：19 行，`is_aligned = false` 恰好 2 行，日期是 **2021-01-07** 与 **2026-02-26**。
🔴 这个名单**被更正过一次**（旧名单取自 `--align-lag-days 3` 的运行）。
若跑出来是第三份名单，**先查 lag 参数，不要直接改文档**。

---

**Q6 · 面板完整性**

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
SELECT COUNT(*) AS row_count,
       COUNT(DISTINCT plow_event_id) AS events,
       COUNT(DISTINCT plow_zone) AS zones,
       COUNT(*) - COUNT(DISTINCT plow_event_id || '|' || plow_zone) AS duplicate_pairs,
       SUM(CASE WHEN matched_snowfall_event_id IS NULL THEN 1 ELSE 0 END) AS unmatched_cells
FROM fact_event_zone_rank;
SQL
```

判据：418 / 19 / 22 / **0** / 44（= 2 次未对齐 × 22）。

---

**Q7 · `shift_number` 的结构**（→ FIG-BO2-03 的色阶要按它定）

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
SELECT shift_number,
       COUNT(*) AS cells,
       COUNT(DISTINCT plow_zone) AS zones,
       ROUND(MIN(rank_factor), 2) AS min_rank_factor,
       ROUND(MAX(rank_factor), 2) AS max_rank_factor
FROM fact_event_zone_rank
GROUP BY shift_number
ORDER BY shift_number;
SQL
```

判据：`shift_number` ∈ 1..5、`rank_factor` ∈ [0.2, 1.0]、**没有 0**
（`rank_factor = 0` 会让 F3 掉行，L2 阶段 D 的门禁量的就是这个）。

---

**Q8 · 停车禁令匹配**

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
SELECT CASE WHEN matched_plow_event_id IS NULL THEN 'unmatched' ELSE 'matched' END AS match_state,
       COUNT(*) AS bans,
       COUNT(DISTINCT ban_type_id) AS ban_types,
       MIN(CAST(ban_start_utc AS DATE)) AS first_ban,
       MAX(CAST(ban_start_utc AS DATE)) AS last_ban
FROM fact_parking_ban
GROUP BY CASE WHEN matched_plow_event_id IS NULL THEN 'unmatched' ELSE 'matched' END
ORDER BY match_state;
SQL
```

判据：19 matched / 30 unmatched。
🔴 那 30 行**不是缺数据**——禁令与全市犁雪不是一一对应关系。图注必须说这件事，
否则「61% 未匹配」会被读成数据质量问题。

---

**Q10 · 有排班 / 无排班分区**（→ BO-4 的引子，也是 FIG-BO2-01 的分母说明）

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
SELECT has_plow_schedule,
       COUNT(*) AS zones,
       SUM(address_count) AS addresses,
       SUM(CASE WHEN geometry_repaired THEN 1 ELSE 0 END) AS repaired_geometries,
       MAX(address_count_snapshot_date) AS snapshot_date
FROM dim_plow_zone
GROUP BY has_plow_schedule
ORDER BY has_plow_schedule;
SQL
```

判据：22 true / 3 false；`repaired_geometries` 合计 8；无排班分区地址占比 ≈ 6.0%。
⚠️ **占比要自己除**（R3：比率不在 SQL 里跨组算），两个 `addresses` 相除即可。

---

## 4. 结果

每条查询的实测结果与判定逐条与 §2 的期望对照。**判据本身被推翻也记在这里**（§4.1）。

**阶段 0（2026-08-30 实测，节点本地 08-31 00:22）**

| # | 期望 | 实测 | 判定 |
|---|---|---|---|
| P0 | 返回 1 | `"1"` | ✅ heredoc → stdin 可用，无需临时文件 |
| P1 | `certified` / 0 error | 连续五天 daily 全部 `certified`；83 检查 / 0 error / 0 warn / **0 could-not-run**；最新 `dq-20260830T083000-154b32` @ `2026-08-30 08:30:29` | ✅ |
| P2 | 五行、行数 418/418/19/25/49 | 五行、行数逐张精确命中；`source_max_ingest_date` 五张全为 `2026-08-17`；但 `etl_run_id` **有两个**（见 §4.1） | ⚠️ 表没问题，**判据错了** |
| P3 | 有 Trino 数据源 | 有，且已存在看板 `Winter Ops Intelligence`（`grafana.huzhi.dev`）三块面板直读 Gold | ✅ 超出预期，见 §4.2 |

**阶段 1（2026-08-30 实测）—— 十条全绿，其中 Q4 的两个数逐位复现**

| # | 期望 | 实测 | 判定 |
|---|---|---|---|
| Q9 | 单一时长 | **单行**：`planned_hours=12`、418 班次 / 19 事件 | ✅ Q1 的 `× 12` 成立 |
| Q1 | 1.26 → 3.47，极差 ≈ 26 h | S **1.26** → C **3.47**，等待小时 3.2 → 29.7，**极差 26.5 h** | ✅ 两端逐位命中 |
| Q2 | ≥2 个分区位移 > 1 班 | 恰好 2 个：**V +1.31**（1.89→3.20）、**M +1.02**（1.78→2.80） | ✅ 台账的两个数逐位命中 |
| Q3 | ρ ≈ +0.59 | **0.668**（22 分区，`RANK()` 最小秩） | ⚠️ 数对不上是**口径差异**，见 §4.3 |
| Q4 | +0.49 / +0.40，方向为正 | `r_all=`**0.491**、`r_since_2021=`**0.403**（2021 年起 11 次事件） | ✅ 两个数逐位命中，方向为正 |
| Q5 | 17/2，名单 2021-01-07 · 2026-02-26 | 19 行、`is_aligned=false` 恰好两行：`26654962` **2021-01-07**、`37817514` **2026-02-26** | ✅ 名单与更正后的台账一致 |
| Q6 | 418 / 19 / 22 / 0 / 44 | **418 / 19 / 22 / 0 / 44**（= 2 × 22） | ✅ 五项全中 |
| Q7 | 1..5，`rank_factor` 无 0 | 1..5，`rank_factor = shift_number / 5`（0.2…1.0），**无 0** | ✅，但尾部很薄，见 §4.4 |
| Q8 | 19 / 30 | 19 / 30，且**匹配的 19 条只有 1 个 `ban_type_id`，未匹配的 30 条有 2 个** | ✅ 且拿到一条新证据，见 §4.5 |
| Q10 | 22 / 3，8 个修复几何 | 22 有排班 / 3 无排班；地址 223,544 / **14,314**；修复几何 6 + 2 = **8** | ✅，且跨层对上，见 §4.6 |

阶段 1 的完整逐分区数据（Q1 的 22 行、Q2 的 22 行）不复制进本篇——
它们是**图的数据**，落 `sql/presentation/`，结论落
[台账 §2](../requirements/bo-conclusions-and-figures.md)。本篇只记判定与偏差。

### 4.1 🔴 P2:「五张表 `etl_run_id` 一致」是一条问错问题的判据

实测：

```
dim_plow_event / dim_plow_zone                          l2-20260820T033431Z  03:34
fact_event_zone_rank / fact_plow_shift / fact_parking_ban  l2-20260820T153519Z  15:35
```

**表没有问题。** 15:35 那批是 O17 收口时那次 `make gold-build ONLY=facts` 复核
（L2 launch §4.13，「五张事实表行数逐张相同」）。`ONLY=` 部分重建是**受支持的正常
操作**，所以「所有表同一个 `etl_run_id`」在这个系统里根本不是一个可达状态——
按原判据，一次完全正确的部分重建会被判成故障。

判据改成三条，两条新的：

| 判据 | 为什么 | 实测 |
|---|---|---|
| 每张表恰好**一个** `etl_run_id` | 原判据里唯一有效的部分：验 R4 的 purge 生效、`INSERT` 没有叠加成两代 | ✅ 五行 |
| `source_max_ingest_date` 全等 | **这才是「这些数彼此可比」的判据**。构建批次可以不同，上游截止日不能不同 | ✅ 全为 `2026-08-17` |
| 事实表 `built_at` **不早于**它依赖的维表 | 危险的顺序是反过来——维表后建，事实表里留着上一代维度的 join 结果。而这恰恰是 `etl_run_id` 一致性检查**抓不到**的那个方向 | ✅ 15:35 > 03:34 |

这与 DQ 第二批首跑抓到的两个缺陷同类：**规则跑得动，但问错了问题**，且不产生噪音，
所以过松比过严更难发现。

### 4.2 P3:Grafana 上已有三块 BO 面板，但它们的 SQL 不在仓库里

看板 `Winter Ops Intelligence`（`grafana.huzhi.dev/d/bfw6fahi8terke/`），三块，全部直读 Gold：

| 现有面板 | 对应清单 | 现状 |
|---|---|---|
| Rank displacement distribution | FIG-BO8-01 | 命名已守住纪律 2（displacement,非 better/worse） |
| Zone load — latest snowfall event (scored zones only) | FIG-BO6-01 的单事件版 | 标签带 `[full_3factor]`,纪律 1 已守 |
| Snowfall event severity timeline (scheduling era) | FIG-BO3-01 | 按雪季分色,已限定排班期 |

🔴 **第一块疑似漏了 `GROUP BY model_version`**：各柱计数之和 = **748**，
正好是 `fact_recommendation` 全表行数（374 × **2 个版本**）。若属实，这张分布把 v1 与
**故意训坏的 `nomonth`** 混在了一条分布里。`talking_points.py` 的同名查询按
`model_version` 分组，原因正是这个。**待确认该面板的 SQL。**

🟡 三块面板的 SQL 都不在仓库里，即 design A2（没有孤儿图、也没有孤儿 SQL）当前不满足。
阶段 5 要把它们**反向落进** `sql/presentation/`，并补齐三件套头注。

🟢 顺带更正 design §3.3 的一处判断：Grafana 在这里不只是「运维自用」——
它已经承载了 BO 级图形，且经公网域名可达。台上仍用 ECharts 冻结导出（C7 的理由不变），
但 Grafana 从「OPS 三张图」升为**与 Superset 并列的可交互载体**。

### 4.3 ✅ Q3 的 ρ = 0.668 与探针的 +0.591:差异已定位到并列秩,**Q3 的口径是错的**

原先记为「两处都没错、是两套口径」。读了探针源码之后要收紧一格:
**前后期切法两边完全相同,只有并列秩的处理不同,而 Q3 那一种是错的。**

| | 探针 `rank_counterfactuals` | Q3(Trino) |
|---|---|---|
| 切法 | `half = len(order) // 2` → 前 9 / 后 10 | 前 9 / 后 10 |
| 并列 | `_probe_common.spearman` → **平均秩** | `RANK()` → **最小秩** |

`scripts/analysis/rank_counterfactuals.py:224` 的 `half = 19 // 2 = 9`,
与 Q3 逐字相同——**切法不是差异来源**。差异只剩并列秩,而并列在这份数据里
不是边角情况:Q1 的 `mean_shift` 有多组完全相等(F/N 都是 2.00,
J/L/I/R 都是 1.89)。Spearman 的定义要求并列取**平均秩**;`RANK()` 给最小秩,
把四个并列的分区当成 rank 1/1/1/1 而不是 2.5/2.5/2.5/2.5,相关系数因此虚高。

🔴 **结论:0.668 是一个算错的 Spearman,不是「另一个口径」。台上用 +0.591。**
Trino 里要复现平均秩得用 `AVG(rnk) OVER (PARTITION BY 秩值)` 或
`(RANK() + COUNT(*) OVER (...) - 1 + RANK()) / 2` 这类写法;
**但没必要**——这个数由探针负责,Gold 侧不需要第二份实现(design §1.1
「探针数据 ≠ 管道数据」的另一面:同一个数只该有一个权威口径)。

⏳ **仍待跑**:`zone_schedule_rank` 不产出 ρ,ρ 在**另一个探针**里。

```bash
TRINO_HOST=localhost TRINO_PORT=8090 uv run python -m scripts.analysis.rank_counterfactuals
```

输出里看 `spearman_early_vs_late`(期望 **+0.591**)与 `largest_moves`
(期望首两位是 **V** 与 **M**,与 Q2 的 +1.31 / +1.02 对得上)。
这条同时复核了本轮 C2-3 与 C2-4 两条结论。

### 4.4 Q7:班次 4 与 5 很薄，且首班与末班都不是「谁都能轮到」

| shift | 格数 | 涉及分区数 | rank_factor |
|---|---|---|---|
| 1 | 115 | **21** | 0.2 |
| 2 | 131 | 22 | 0.4 |
| 3 | 128 | **21** | 0.6 |
| 4 | **25** | **6** | 0.8 |
| 5 | **19** | **5** | 1.0 |

三件事：

1. **89.5% 的格落在 1–3 班**（374/418），4–5 班只有 44 格。BO-2 讲「顺位」时
   实际讲的是三档，不是五档。
2. **shift 1 只涉及 21 个分区** —— 有一个分区从未排在首班。与 Q1 独立对上：
   K 的 `min_shift = 2`，其余 21 个分区的 `min_shift` 全是 1。**两条查询互证。**
   这条对表述纪律 5（顺位差异 ≠ 不公平）是正面弹药：**连最慢的 C 也当过首班**
   （`min_shift = 1`），只有 K 没有。
3. shift 5 的 19 格 / 19 个事件 —— 平均每个事件恰好一格。是不是「每场雪的末班
   在 5 个分区里轮」，还是集中在少数几场，Q11 会给出答案（§8）。

### 4.5 🟢 Q8:未匹配的 30 条禁令是**另一种禁令**，不是缺数据

| | 条数 | `ban_type_id` 个数 | 日期跨度 |
|---|---|---|---|
| 匹配上犁雪事件 | 19 | **1** | 2015-12-20 → 2026-02-26 |
| 未匹配 | 30 | **2** | 2015-12-01 → 2025-12-03 |

匹配与否几乎完全由**禁令类型**决定，而不是由「有没有对上」决定。这改变了
BO-2 里 49→19 这个落差的讲法：它不是 61% 的数据丢了，是 `fact_parking_ban` 里
本来就装着两类不同的禁令，只有一类对应全市犁雪作业。CLAUDE.md 早已记着
「19 匹配 / 30 NULL，**语义不是缺数据**」，这里第一次拿到了那句话的证据。

🟡 需要 Q12 取到那三个 `ban_type_id` 的字面值才能写进图注——
现在只知道**个数**，不知道**是什么**。

### 4.6 🟢 Q10:地址总数 237,858 与 E0/E1 的空间匹配分母逐位相同

`223,544 + 14,314 = 237,858`，与 E0/E1 上线记录里的空间命中率分母
（237,858 / 237,867）**同一个数**；修复几何 `6 + 2 = 8`，与探针任务 2 记的
「25 个 plow zone 里 8 个含 OGC 非法几何」相同；无排班分区地址占比
`14,314 / 237,858 = ` **6.02%**，与 ADR 0008 的 6.0% 相符。

三条独立记录在三个不同时点、由三条不同路径量得，今天在 Gold 上同时复现。
这是本轮到目前为止**最强的一条跨层一致性证据**，值得作为 OPS 类图的素材
（台账 §8）。

### 4.7 🟢 `zone_schedule_rank` 复跑:探针与管道在 BO-2 上**逐位相同**

2026-08-30 实测,与 Q1 / Q4 / Q10 三条 Trino 查询对照:

| 项 | 探针(公开 API + pandas) | Gold(Trino) | 判定 |
|---|---|---|---|
| 22 个分区的 `avg_shift` | 3.47 / 3.32 / … / 1.26 | 同 | ✅ **22 行逐位相同** |
| 各分区的班次极差 | C `1-5` · K `2-3` · R `1-2` · V `1-4` | 同 | ✅ 逐行相同 |
| `r(avg_shift, addresses)` | **+0.491** | **0.491** | ✅ |
| 无排班分区的地址数 | `B/D` 11,150 + `X` 2,590 + `Downtown` 574 = **14,314** | **14,314** | ✅ 且探针给出了**三个分区的名字** |
| 有排班地址数 | 237,858 − 14,314 = **223,544** | **223,544** | ✅ |
| 空间匹配 | 237,858 / 237,867(9 个未归入) | E0/E1 同 | ✅ |

🔴 **这与 F1 的 908-vs-906 漂移形成对照,而对照本身是结论**:
BO-2 的两个输入(排班表、地址点)都是 `static` 源,不经 Open-Meteo,
所以探针与管道**没有漂移空间**;F1 会漂是因为降雪事件边界随存档回修而重切。
「哪些数会漂、为什么」由此有了一个可讲的判据,而不是逐个记忆。

🟡 探针打印的 `spatial hit rate: … = 100.0%` 是**显示层四舍五入**,真值
99.996%,有 9 个地址未归入任何分区。**图上不许出现 100%**——一个恒真的完美率
和一个坏掉的分母长得一样(与 B5 是同一条纪律)。

🟡 探针的 `share` 列分母是 **237,858(含无排班分区)**,不是 223,544。
引用「某分区占全市 9.4%」时必须说清分母,否则与 C2-11 的 6.02% 讲不到一起。

⏳ **Q13 因此多了一个用途**:探针的每分区地址数(C 22,399 · A 22,480 · …)
与 Gold `dim_plow_zone.address_count` 是否逐位相同,Q13 一跑就知道。

### 4.8 阶段 2(BO-4)结果:七条判据六条全中,一条**判据本身写错了**

**2026-08-30 实测(节点本地 08-31 00:39),同一批次 `dq-20260830T083000-154b32`**

| # | 期望 | 实测 | 判定 |
|---|---|---|---|
| B1 | 548 行 / 两个 `label_type` | 439 neighbourhood + 109 ward = **548** ✅;但 `zones = `**25** 不是 22,且 neighbourhood 只用到 **233** 个标签 | ⚠️ 行数对,两处需要解释,见下 |
| B2 | 归一,偏离 > 1e-6 的对数为 0 | **50** 个 `(zone, label_type)` 对、`not_normalised = 0`、`min_w = max_w = 1.0` | ✅ |
| B3 | 22 行,中位数明显低于 1.0 | **25** 行(含三个无排班分区);中位 **0.5402**(仅 22 个有排班分区则 **0.535**) | ✅ 结论成立,行数按 B1 更正 |
| B4 | 15 + 237 = 252 | **15 ward + 237 neighbourhood** | ✅ |
| B5 | ≈ 99.9%,带分母 | 近三天 **99.9%**,再往前四天 **100.0%**;分母 7,266–7,981;`>= 99.5` 全 `passed` | ✅ |
| B6 | 141,377 / 18 年 / 2 类 | **141,377 / 18 / 2**,2009-01-16 → 2026-08-17,`SUM(request_count) = `**410,650** | ✅ |
| B7 | 13,068 / 事件数分区数与 L3-0 一致 | **13,068 = 99 × 22 × 6**,恰好满笛卡尔积;6 个 category,`SUM = `45,980 | ✅ 但**判据写错了**,见 §4.9 |

**B1 的两处需要解释:**

1. 🟢 **`zones = 25` 是对的,不是错的。** crosswalk 覆盖全部 25 个分区,
   包括 `X` / `B/D` / `Downtown` 三个无排班分区——它们有边界、有行政标签,
   只是没有排班。B3 的 25 行印证。**凡是「按分区」的图都要先声明是 25 还是 22**,
   BO-2 的顺位图是 22(无排班就没有顺位),BO-4 的对齐图是 25(边界与标签都存在)。
2. 🟡 **237 个 neighbourhood 里有 4 个不出现在任何分区的 crosswalk 里。**
   `dim_admin_label` 有 237,crosswalk 只用到 233。四个孤儿标签是什么、
   为什么落不进任何分区(在市界外?面积为零?工单里出现过但几何对不上?),
   **本轮没有答案**,补测见 §11 的 B8。在答案出来之前,
   **不许把 237 讲成「全部纳入」**。

### 4.9 🔴 B7 的判据问错了列:WINDROW 的 `cells = 2,178` 而 `requests = 0`

| winter_category | cells | requests |
|---|---|---|
| SNOW | 2,178 | **33,639** |
| FROZEN | 2,178 | 4,293 |
| PLOW | 2,178 | 4,074 |
| SANDING | 2,178 | 3,127 |
| ICE_CONTROL | 2,178 | 847 |
| **WINDROW** | 2,178 | 🔴 **0** |

我写的判据是「若某个 category 的 `cells = 0`,说明 O4 的仲裁没生效」。
**这条判据永远不会触发**:F1 是 99 事件 × 22 分区 × 6 类的**满笛卡尔积**,
每一类都必然有 2,178 格,不管有没有工单落进去。要量的是 `requests`,不是 `cells`。

改成 `requests` 之后它立刻响了:**WINDROW 在全部 99 个事件、22 个分区上一条工单都没有**。
ICE_CONTROL 拿到 847(非零),所以 O4 的「最具体优先」至少对它生效了;
WINDROW 是**仍然为零**还是**本来就没有对应的 311 type**,这两件事在这张表上分不开——
`dim_service_type` 侧的补测在 §11(B9)。

🔴 **这是本轮第三个「规则跑得动但问错了问题」**(前两个:P2 的 `etl_run_id`、
DQ 第二批的 F1 漏 `is_scheduling_era`)。三次都有同一个形状:
**判据落在一个恒真的列上,于是它不产生噪音,也就没人发现它从没检查过任何东西。**
写判据时的自检问题是「这个数在最坏情况下会不会变」,`cells` 不会变,`requests` 会。

🟡 **B6 的 410,650 不是工单数。** 一条同时带 ward 和 neighbourhood 的工单在 F8 里
产生两行,`SUM(request_count)` 因此把它数了两次。凡是引用这个数的图注,
要么写「标签出现次数」,要么按 `label_type` 分开——**不许写成「41 万条冬季工单」**。

### 4.10 阶段 1/2 收尾五条:全部回答,其中两条把猜想变成了结论

**2026-08-30 实测(节点本地 08-31 00:43–00:47)**

#### Q11 🟢 **19 个事件全部排满 5 个班次**,所以「末班每事件恰好一格」不是巧合

```
max_shift=5 · events=19 · (全部 19 个 plow_event_id)
```

单行输出。结合 Q7(shift 5 共 19 格 / 5 个分区、shift 4 共 25 格 / 6 个分区):

- 每次全市犁雪都走满 5 个班次,**末班永远只有一个分区**(19 格 ÷ 19 事件)。
- 能排到末班的只有 **5 个分区**,查 Q1 的极差列正是 `1-5` 的那五个:
  **A · B · C · D · E**——也正是平均顺位最高的五个(3.47 / 3.32 / 3.21 / 2.53 / 2.47)。
- 第 4 班的 6 个分区 = 这五个 + **V**(极差 `1-4`)。

**三条查询(Q1 极差 · Q7 分布 · Q11 满班)彼此独立却完全自洽**,
这是本轮结构最干净的一条结论。§4.4 第 3 点的存疑到此消除。

#### Q12 ✅ 判据完全命中:匹配与否**完全**由禁令类型决定

| `ban_type_id` | 条数 | 匹配 | 首见 | 末见 |
|---|---|---|---|---|
| `4` | 19 | **19（100%）** | 2015-12-20 | 2026-02-26 |
| `2` | 19 | **0** | 2015-12-18 | 2024-01-12 |
| `1` | 11 | **0** | 2015-12-01 | 2025-12-03 |

没有任何一个类型是「一半匹配一半不匹配」,§4.5 的结论坐实:
**49 → 19 不是丢了 61% 的数据,是三类禁令里只有一类产生逐分区班次。**

🟡 **三个 id 的字面含义无法从 Gold 取到。** `description` /
`description_french` 在 Silver 就被丢掉了(`spark/jobs/etl_parking_ban.py`
的注释说明了理由:双语自由文本,而 `ban_type_id` 已承载同一个三值区分)。
契约 `contracts/api-contracts/winnipeg-parking-bans.yaml` 记着三个取值是
**ANNUAL SNOW ROUTE · EXTENDED SNOW ROUTE · RESIDENTIAL (KNOW YOUR ZONE)**,
但**没有记 id ↔ 名称的对应**。

强推断(**不是证据**):`1` 的 11 条恰好对应 2015-16 … 2025-26 **十一个雪季**、
每季一条,符合 ANNUAL 的季长声明形态;`4` 是唯一产生逐分区班次的,符合
「按分区」的 RESIDENTIAL (KNOW YOUR ZONE)。
🔴 **图注要写名字就必须先落实这个对应**,补测见 §12 的 B11。在那之前
FIG-BO2-05 只能写 id。

#### `rank_counterfactuals` ✅ ρ = **+0.591 逐位复现**,位移名单与 Q2 逐位相同

```
9 early vs 10 late events, 22 zones in both, spearman +0.591
  V: 1.89 -> 3.2 (+1.31) · M: 1.78 -> 2.8 (+1.02)
  E: 2.89 -> 2.2 (-0.69) · S: 1.56 -> 1.0 (-0.56) · G: 2.0 -> 1.6 (-0.40)
```

五个位移最大的分区、两位小数的早晚均值,**与 Q2 的 Trino 输出逐位相同**。
§4.3 由此完全闭合:切法相同、数据相同,0.668 与 0.591 的唯一差异是并列秩,
而 Q3 那一种是错的。**L1 关闭。**

同批复现的另外三条(都与台账相同):
- `shift_number` 语义:**19/19** 个事件的批次序与时间序一致,0 个不一致。
- 降雪反证:22 分区 **10 个不同的总量**、极差 20.6 cm = 均值的 **2.1%**、
  `r(顺位, 降雪) = +0.074`——分区之间几乎没有降雪差异,**无法解释顺位**。
- 连接率:**19/49 = 38.8%**,0 个班次指向不存在的禁令。

🟡 探针重取了 Open-Meteo 全量(`var/probe-cache/` 是未跟踪目录,删了就全量重取),
过程中三次 `rate limited; retrying`。**复核 ρ 不需要重取天气**——
下次只跑漂移那一条可以省十几分钟,但当前 CLI 没有分项开关。

#### B8 ✅ 4 个孤儿 neighbourhood 的名字

```
perrault · the mint · trappistes · west perimeter south
```

恰好 4 行,C4-9 的差额确认是「孤儿标签」而非别的东西。
🟡 **为什么落不进任何分区,本轮仍没有答案。** 四个都是市域边缘或极小的地名,
但「在犁雪分区覆盖范围之外」只是猜想——`dim_admin_label` 来自工单自报文本,
`dim_region_crosswalk` 来自几何相交,两者本就不保证同一个论域。
台账 §3.3 保留 3 **不消**,改写为已知名字、未知成因。

#### B9 🟢 WINDROW 是**数据事实,不是缺陷**——而且它证明了 O4 的仲裁生效了

| winter_category | types | `priority_weight` | F1 requests |
|---|---|---|---|
| SNOW | 109 | 1–3 | 33,639 |
| SANDING | 55 | 1–3 | 3,127 |
| FROZEN | 26 | 1–3 | 4,293 |
| PLOW | 10 | 🔴 **NULL** | 4,074 |
| ICE_CONTROL | 3 | 🔴 **NULL** | 847 |
| WINDROW | **1** | 🔴 **NULL** | 0 |

WINDROW 的 `types = 1 > 0`,按 §11 的判据表落在**「映射在,只是窗口内没有工单」**
一档:**数据事实,不重开 O4**。

🟢 而且它是 O4 那条修法**生效的证据**:唯一那个 type 叫
`Snow Removal Windrow Inquiry`——**名字里带 "Snow Removal"**,
按「SNOW 优先」它会被 SNOW 吞掉,正是 O4 当初担心的结果;
「最具体优先」把它判给了 WINDROW。ICE_CONTROL 的三个 type 全是
`Parked Vehicle Impeding Snow & Ice Control Ops` 的拼写变体,同理。

🔴 **但顺手发现一个没人问过的问题:三个 category 的 `priority_weight` 全是 NULL。**
PLOW(10 个 type / 4,074 条工单)、ICE_CONTROL(3 / 847)、WINDROW(1 / 0)
都没有优先级权重,而 SNOW / SANDING / FROZEN 有 1–3。
F1 的 `weighted_request_count` 是**按 `priority_weight` 加权**的,
BO-6 的需求项消费的正是它。**如果 NULL 被当成 0 或让整格变 NULL,
那 4,921 条 PLOW + ICE_CONTROL 工单在评分里的权重就是零**,
而 L3-c 的「全空列 0」查不出这件事——那一列整体不空,只是这三类为空。
补测 B10 在 §12。**在 B10 回来之前,不许说 BO-6 的需求项覆盖六类冬季工单。**

## 5. 与设计的偏差

见 §4.1（判据修正）与 §4.2（载体分工修正）。

阶段 1 新增一条**不是偏差但必须记住的口径分歧**：§4.3 的 ρ。设计 §3.2
写的是「结论与需求文档冲突时改需求文档，不改数」——这里两个数都没错，
冲突在口径，处理办法是**两个都留、各自标注口径**，不是二选一。

---

## 6. 遗留项

| # | 内容 | 归属 |
|---|---|---|
| ~~L1~~ | ~~跑探针复核 ρ~~ | ✅ **已关闭**（§4.10）：`rank_counterfactuals` 给出 +0.591，位移名单与 Q2 逐位相同 |
| L2 | Q11 / Q12 / Q13 三条补测（§8），补齐 BO-2 三张图的图注 | 阶段 1 收尾 |
| L3 | 取 Grafana 三块面板的 SQL 反向进仓（design O1 的反向任务，§4.2） | 阶段 5 |
| ~~L5~~ | ~~Q13 余下 19 行~~ | ✅ **已关闭**（§18.5）：`fig_bo2_04_rank_vs_addresses.sql` 自己产出那 22 行，不必单独再跑 |
| ~~L6~~ | ~~B8 · B9~~ | ✅ **已关闭**（§4.10），但各派生出一条新补测：B10（`priority_weight` NULL）· B11（`ban_type_id` 的名称对应） |
| ~~L7~~ | ~~B10~~ | ✅ **已关闭**（§14.1）：NULL 权重按 **1** 兜底，不是零。派生 L11 |
| ~~L8~~ | ~~B11~~ | ✅ **已关闭**（§14.2）：1=ANNUAL · 2=EXTENDED · 4=RESIDENTIAL (KNOW YOUR ZONE) |
| **L9** | CLAUDE.md 里 BO-3 的「必须再加滚动累积判据」写成了待办，**实际已交付**（`accum_flag` 8 个 true，§14.3）——下次改 CLAUDE.md 时改成完成态 | 阶段 5 |
| **L10** | 🔴 F8 里 2019 年起 ward 标签次数持续多于 neighbourhood（+16 峰值），2009–2018 逐年相等（§14.8）。成因未查 | 阶段 5（不属于 BO-6/BO-8，本轮不带） |
| **L11** | 🟡 PLOW / ICE_CONTROL 被默认按 1（量表下限）计权，没人做过这个决定（§14.1）。要改走种子 + F1/F6 重建 | H1 之后 |
| ~~L12~~ | ~~E5b · E4b~~ | ✅ **已关闭**（§15）：908 逐位复现；累积判据救回四次里的一次 |
| ~~L13~~ | ~~零工单事件 vs accum 事件~~ | ✅ **已关闭**（§17.8）：**不重合**，8 个 accum 事件里只有 1 个零工单；真正的聚集是「四月」(7/11) 与「2008–2009」(4/11) |
| **L15** | 🔴 `attribution_text` 把降雪量渲染成 `2.02E1`（科学计数法），面向读者的句子（§17.7）。修法 `format('%.1f', …)`，**要重建 F7 = 写操作** | 待用户决定 H1 前/后 |
| **L16** | 🔴 CLAUDE.md 与 L3 launch §8 对禁语 ② 的**理由**写错了（把经验事实写成结构不可能，§17.5）。禁语本身不动，理由要改 | 阶段 5 |
| ~~L14~~ | ~~§15.3 确认查询~~ | ✅ **已关闭**（2026-08-31）：两行逐字命中，且直接证实了 17/7 两个锚点是同一件事 |
| L4 | 确认 `dim_plow_event.matched_snowfall_event_id` 未匹配时存的是 NULL 还是空串——Q6 用 `IS NULL` 数到 44 说明 `fact_event_zone_rank` 侧是 NULL，dim 侧 CLI 显示为 `""` 无法区分。**只影响面板过滤条件怎么写**，不影响任何结论 | 阶段 5 |

---

## 7. 接手者从这里读

**当前状态：阶段 0–3 已跑完。** BO-2 → [台账 §2](../requirements/bo-conclusions-and-figures.md)
· BO-4 → §3 · BO-3 → §4 · BO-1 → §5，四个 BO 的结论与图注都已落台账。
阶段 4（BO-6 + BO-8）也已跑完（§17），结论落台账 §6 / §7。
**待执行：阶段 5（冻结 `sql/presentation/`、建三个载体、反向进仓 Grafana 三块面板）**，
外加两件不阻塞的欠账（§12.3 的 Q13 余下 19 行 · Grafana 位移面板的 SQL）。

1. 先读 [design](../design/20260827-bo-eda-and-presentation-sql.md) §2 的 C3
   （七条表述纪律）与 §3.2 的三条纪律。**图注是承重件**，不是最后补的文案。
2. 按 §3.1 → §3.2 顺序执行，**Q9 必须在 Q1 之前**。
3. 结果原样贴回，包括报错。填进 §4 的表。
4. 阶段 1 收口后，再展开阶段 2（BO-4）的 SQL。
5. §8 与 §9 可以**一次贴回**：§8 是 BO-2 的收尾补测，不改变 BO-4 要问什么，
   两者之间没有依赖。这是本轮唯一一次合并，之后仍是一轮一停。

**这一轮不产生任何写操作**：不建表、不改 schema、不重建 Gold，全部是 `SELECT`。

---

## 8. 阶段 1 收尾 —— BO-2 补测三条

三条都是**图注需要而现有结果给不出**的字面值，不改变已有结论，也不影响阶段 2
要问什么。可以和 §9 一起贴回。

### Q11 · 4/5 班是「每场雪一格」还是「集中在少数几场」

§4.4 观察到 shift 5 恰好 19 格、恰好 19 个事件。两种结构在图上讲法完全不同：
「每场雪的末班在 5 个分区里轮」是关于**轮换**的结论，
「少数几场雪排到了 5 班」是关于**事件规模**的结论。

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
SELECT max_shift, COUNT(*) AS events,
       array_join(array_agg(plow_event_id ORDER BY plow_event_id), ' ') AS ids
FROM (
  SELECT plow_event_id, MAX(shift_number) AS max_shift
  FROM fact_event_zone_rank GROUP BY plow_event_id
) GROUP BY max_shift ORDER BY max_shift;
SQL
```

判据：19 个事件按 `max_shift` 分布。若 `max_shift = 5` 只出现在少数事件上，
§4.4 第 3 点的「每事件一格」是巧合，讲法必须改成事件规模。

### Q12 · 三个 `ban_type_id` 的字面值

§4.5 只知道个数（匹配 1 个 / 未匹配 2 个），图注需要它们**叫什么**。

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
SELECT ban_type_id,
       COUNT(*) AS bans,
       SUM(CASE WHEN matched_plow_event_id IS NOT NULL THEN 1 ELSE 0 END) AS matched,
       MIN(ban_start_utc) AS first_seen,
       MAX(ban_start_utc) AS last_seen
FROM fact_parking_ban GROUP BY ban_type_id ORDER BY bans DESC;
SQL
```

判据：三个取值，其中恰好一个 `matched = bans`（全匹配），另两个 `matched = 0`。
🔴 若出现「同一个 `ban_type_id` 里一半匹配一半不匹配」，**§4.5 的结论作废**——
那说明匹配不是由类型决定的。

### Q13 · FIG-BO2-04 散点图的 22 行原始数据

Q4 只回了两个相关系数，画不出散点。

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
SELECT z.plow_zone, z.address_count,
       ROUND(AVG(CAST(r.shift_number AS DOUBLE)), 3) AS mean_shift_all,
       ROUND(AVG(CASE WHEN e.first_shift_start_utc >= TIMESTAMP '2021-01-01 00:00:00'
                      THEN CAST(r.shift_number AS DOUBLE) END), 3) AS mean_shift_since_2021
FROM fact_event_zone_rank AS r
JOIN dim_plow_zone AS z ON z.plow_zone = r.plow_zone
JOIN dim_plow_event AS e ON e.plow_event_id = r.plow_event_id
GROUP BY z.plow_zone, z.address_count
ORDER BY z.address_count DESC;
SQL
```

判据：22 行；`address_count` 与 `mean_shift_all` 方向为正（与 Q4 的 +0.491 一致）。

---

## 9. 阶段 2 —— BO-4（空间对齐与行政标签），七条判据

BO-4 的核心主张是**「作业分区与行政区不对齐」**，它同时是 ADR 0009
（评分统一到 `plow_zone`、不做 ward / neighbourhood 级评分）的依据。
所以这一阶段的图有两个任务：把不对齐**量出来**，以及**说明为什么不能按 ward 打分**。

判据先写在这里，跑之前不改。

| # | 问什么 | 期望 | 依据 |
|---|---|---|---|
| B1 | crosswalk 形状 | 548 行；两个 `label_type` | L2 阶段 C 实测 548 |
| B2 | 每个 `(zone, label_type)` 的 `SUM(weight)` | 全部 ≈ 1.0，偏离 > 1e-6 的对数为 0 | 权重是面积占比，必须归一 |
| B3 | 每个分区的 dominant ward 占比 | 22 行；**中位数明显低于 1.0**——这就是「不对齐」本身 | 台账记的 34.1% / 45.4% |
| B4 | `dim_admin_label` 构成 | 15 ward + 237 neighbourhood = 252 | L2 阶段 C 实测 |
| B5 | 空间命中率的**当前**值与分母 | ≈ 99.9%，分母是 `has_geo` 子集 | DQ 第二批：百分比不带分母 = 读不出来 |
| B6 | F8 形状 | 141,377 行 / 18 个年份 / 两个 `label_type` | L2 §4.9 更正后的数 |
| B7 | F1 形状 | 13,068 行 / 2,178 / 1,298 | L3-0 复核 |

### B1 + B2 + B4 —— 三条形状查询

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
SELECT label_type, COUNT(*) AS rows_, COUNT(DISTINCT plow_zone) AS zones,
       COUNT(DISTINCT label_id) AS labels,
       SUM(CASE WHEN is_dominant THEN 1 ELSE 0 END) AS dominant_rows
FROM dim_region_crosswalk GROUP BY label_type ORDER BY label_type;

SELECT COUNT(*) AS zone_label_type_pairs,
       SUM(CASE WHEN ABS(w - 1.0) > 1e-6 THEN 1 ELSE 0 END) AS pairs_not_normalised,
       ROUND(MIN(w), 6) AS min_w, ROUND(MAX(w), 6) AS max_w
FROM (SELECT plow_zone, label_type, SUM(weight) AS w
      FROM dim_region_crosswalk GROUP BY plow_zone, label_type);

SELECT label_type, COUNT(*) AS labels
FROM dim_admin_label GROUP BY label_type ORDER BY label_type;
SQL
```

🔴 B2 用 `ABS(w - 1.0) > 1e-6` 而不是 `w <> 1.0`：`weight` 是 `DOUBLE`，
求和后精确等于 1.0 是运气不是规格，等值判据会把全部行报成违规。

### B3 —— dominant ward 占比，22 行（FIG-BO4-01 的数据）

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
SELECT plow_zone, label_id AS dominant_ward, ROUND(weight, 4) AS dominant_share,
       (SELECT COUNT(*) FROM dim_region_crosswalk AS c2
        WHERE c2.plow_zone = c1.plow_zone AND c2.label_type = 'ward') AS wards_touched
FROM dim_region_crosswalk AS c1
WHERE label_type = 'ward' AND is_dominant
ORDER BY dominant_share;
SQL
```

判据：22 行（每个有排班的分区一行）。`dominant_share` 的**中位数**是
FIG-BO4-01 的核心数；`wards_touched` 的最大值回答「一个作业分区最多横跨几个 ward」。

🔴 **图注不能写「行政区划分得不好」**。不对齐是两套划分依据不同（一套按选举，
一套按作业），两边都没错。图注写的是**后果**：按 ward 打分会把同一个作业分区的
工作量拆到几个 ward 里，所以评分统一到 `plow_zone`（ADR 0009）。

### B5 —— 空间命中率的当前值，从 DQ 日志读

不重算。规则 `SILVER-BIZ-SPATIAL-HIT-RATE` 每天都在算，且**带分母**
（`rows_checked` = `has_geo` 子集），这正是 DQ 第二批修掉的那个缺陷。

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_meta <<'SQL'
SELECT run_id, ROUND(observed, 4) AS hit_rate_pct, rows_checked AS has_geo_denominator,
       expected, comparator, passed, checked_at
FROM dq_audit_log WHERE rule_id = 'SILVER-BIZ-SPATIAL-HIT-RATE'
ORDER BY checked_at DESC LIMIT 7;
SQL
```

判据：≥ 5 行、命中率 ≈ 99.9%、`passed = true`。
🔴 **图上只要出现命中率，分母必须同时出现**——「命中率完美」和
「窗口里只有三行带坐标」在没有分母时长得一模一样。

### B6 + B7 —— 两张事实表的形状

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
SELECT COUNT(*) AS rows_, COUNT(DISTINCT YEAR("date")) AS years,
       MIN("date") AS first_day, MAX("date") AS last_day,
       COUNT(DISTINCT label_type) AS label_types, SUM(request_count) AS requests
FROM fact_winter_request_daily_by_label;

SELECT COUNT(*) AS rows_, COUNT(DISTINCT snowfall_event_id) AS events,
       COUNT(DISTINCT plow_zone) AS zones, COUNT(DISTINCT winter_category) AS categories,
       SUM(request_count) AS requests
FROM fact_service_request_zone_event;

SELECT winter_category, COUNT(*) AS cells, SUM(request_count) AS requests
FROM fact_service_request_zone_event GROUP BY winter_category ORDER BY requests DESC;
SQL
```

判据：F8 = 141,377 行 / 18 个年份 / 2 个 `label_type`；F1 = 13,068 行，
事件数与分区数与 L3-0 一致；第三条给出六个 `winter_category` 的分布
（O4「最具体优先」的仲裁结果）。🔴 **若某个 category 的 `cells = 0`，
说明 O4 的仲裁在生产上没生效**，SNOW 吞掉了 WINDROW 或 ICE_CONTROL。

---

## 10. 这一次可以合并贴回

§8（三条）+ §9（四块）合成一次执行。**这是本轮唯一一次合并**，理由是
§8 是 BO-2 的收尾取值、不决定 BO-4 问什么，两者无依赖。之后恢复一轮一停：
阶段 3（BO-3 + BO-1）的提问单要等 BO-4 的结论落进台账之后才写。

全部仍是 `SELECT`，无写操作。

---

## 11. 阶段 2 收尾 —— 两条补测

### B8 · 4 个 neighbourhood 为什么落不进任何分区

`dim_admin_label` 有 237 个 neighbourhood，`dim_region_crosswalk` 只用到 233。

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
SELECT d.label_id
FROM dim_admin_label AS d
WHERE d.label_type = 'neighbourhood'
  AND NOT EXISTS (
    SELECT 1 FROM dim_region_crosswalk AS c
    WHERE c.label_type = 'neighbourhood' AND c.label_id = d.label_id)
ORDER BY d.label_id;
SQL
```

判据：恰好 **4** 行。拿到名字之后才能判断它是「市界外 / 面积为零 / 拼写变体」
中的哪一种——台账 §3.3 保留 3 在此之前不许消。
⚠️ 若返回的行数不是 4，说明 233 与 237 的差不是「孤儿标签」，而是别的东西
（例如同名不同 `label_type`），那台账 C4-9 要重写。

### B9 · `WINDROW` 是「没接上」还是「本来就没有」

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
SELECT winter_category, COUNT(*) AS types,
       MIN(priority_weight) AS min_w, MAX(priority_weight) AS max_w,
       array_join(array_agg("type" ORDER BY "type"), ' | ') AS type_values
FROM dim_service_type WHERE winter_category IS NOT NULL
GROUP BY winter_category ORDER BY types DESC;
SQL
```

判据分两种,**两种的处置完全不同**:

| 实测 | 含义 | 处置 |
|---|---|---|
| `WINDROW` 的 `types = 0` | 没有任何 311 type 映到它 —— 这正是 **O4 当初担心的事**,「最具体优先」的仲裁对 WINDROW 没生效 | 🔴 是**缺陷**,要回 `config/seeds/` 查映射,并重开 O4 |
| `WINDROW` 的 `types > 0` | 映射在,只是这些 type 在 99 个事件窗口内一条工单都没有 | 🟡 是**数据事实**,不是缺陷;图注写「该类别在观测期内无工单」,并把 `type_values` 里的字面值写进图注 |

`dim_service_type` 只有 `type` / `winter_category` / `priority_weight` 三个业务列
(已核 `sql/ddl/dim_service_type.sql`),没有 `is_effective`——「生效」是
`sql/dml/` 里的过滤条件不是列,所以这条只能分成上面两种情形。

### 另外两件

- **Q11 与 Q12 的输出没收到**（贴回从 Q13 中段开始）。这两条是 BO-2 的图注取值，
  FIG-BO2-05 在拿到 `ban_type_id` 字面值之前写不了。
- **Q13 只收到最后 3 行**（T / Q / M）。三行都对上了探针：地址数 2,104 / 1,499 /
  1,414 与探针**逐位相同**，`mean_shift_all` 2.526 / 2.842 / 2.316 与 Q1 一致。
  🟡 但 `mean_shift_since_2021` 是新的一列（T 2.455 · Q 3.000 · M 2.727），
  FIG-BO2-04 的第二条序列要用它，**需要完整 22 行**。

---

## 12. 阶段 2 的三条补测（B10 · B11 · Q13 余下）

> 都很短。B10 是**阻塞项**——它的答案决定 BO-6 的需求项能不能按六类讲。

### 12.1 🔴 B10：`weighted_request_count` 在权重为 NULL 的三类上是什么

**先写判据**（结果回来之前不改）：

| `SUM(weighted_request_count)` 在 PLOW/ICE_CONTROL/WINDROW 上 | 判定 |
|---|---|
| = 0 或 NULL | 🔴 **缺陷**：4,921 条工单在评分里权重为零，BO-6 需求项实际只覆盖三类。要回 `dim_service_type` 的种子补权重，且 F1/F6 要重建 |
| = `SUM(request_count)`（即按 1 兜底） | ⚠️ 可用，但**权重语义在这三类上是「未分级」不是「低优先级」**，图注要说 |
| 其他正值 | 🟡 有第三套兜底逻辑，回 `sql/dml/fact_service_request_zone_event.sql` 读实现 |

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
SELECT winter_category,
       COUNT(*)                                        AS cells,
       SUM(request_count)                              AS requests,
       SUM(weighted_request_count)                     AS weighted,
       COUNT(*) FILTER (WHERE weighted_request_count IS NULL) AS weighted_null_cells
FROM fact_service_request_zone_event
GROUP BY winter_category
ORDER BY requests DESC;
SQL
```

### 12.2 B11：`ban_type_id` 的 1 / 2 / 4 各叫什么

Gold 与 Silver 都取不到（`description` 在 Silver 就被丢了）。回上游一次即可，
**只读、不落盘**：

```bash
curl -s 'https://data.winnipeg.ca/resource/mfzv-893p.json?$select=ban_type_id,description&$group=ban_type_id,description&$order=ban_type_id'
```

判据：返回恰好 **3** 组，且 `ban_type_id` 的取值是 `1` / `2` / `4`。
若出现 `3` 或第 4 组，说明上游自 2026-08-02 采集以来变了，**这件事本身要记**。

### 12.3 Q13 余下的 19 行

上一轮只贴回了 T / Q / M 三行。FIG-BO2-04 的第二条序列需要完整 22 行——
原样重跑 §8 的 Q13 即可，**整段输出一起贴**。

---

## 13. 阶段 3 提问单：BO-3（降雪事件切分）+ BO-1（冬季需求与 M1）

**判据先写，结果后填。** 表：`dim_snowfall_event`(D4) · `dim_plow_event`(D5) ·
`fact_service_request_zone_event`(F1) · `fact_winter_request_daily_by_label`(F8) ·
`fact_request_forecast`(F5)。全部 Gold，不读 Silver，R1 不适用。

### 13.1 先写死的判据

| # | 问题 | 判据（**先写**） |
|---|---|---|
| E1 | D4 骨架：事件数 · 排班期 · 时长 · `accum_flag` | **N = 99、排班期 59** 必须复现（三轮不动的两个数）。`duration_days` 中位数 **= 1**。`accum_flag` 若**全 false 或全 NULL**，说明「阈下累积」那条判据只落了列没落逻辑——🔴 那是 CLAUDE.md 里 BO-3 唯一的未决项 |
| E2 | 事件按雪季分布 | 每个雪季 ≥ 1 个事件；若某季为 0，图上**不许画成连续折线**（与 §2.3 保留 3 的 2017/2023 同类问题） |
| E3 | `severity_score` 是否可用 | 无 NULL，且与 `total_snowfall_cm` 单调同向（Pearson ≥ +0.8）。**若不单调，它就不是「严重度」而是别的合成量**，图注不许叫严重度 |
| E4 | D5 对齐与 lag | `is_aligned` **17 true / 2 false**（已知）。lag = 首班时间 − 事件结束日，**最大值应达到 17 天**（CLAUDE.md 记的宽 lag）。若最大 lag 远小于 17，说明台账那句话与 Gold 不符 |
| E5 | F1 每事件的非零格数 | 全部 99 事件的非零格合计 ≥ **880**（下界，不是等值——§4.9 与 R4 的漂移机制）。**排班期 59 个事件单独也要 ≥ 大头**，若排班期占比极低，§4.9 那条塌陷仍在 |
| E6 | F8 的年月序列 | 覆盖 2009–2026 **18 个年份**（不是 19，见 CLAUDE.md L2 §4.9）。按 `label_type` 分开取数，**不许合计** |
| E7 | F5 面板完整性 | 每个 `model_version` **恰好 2,178 行**、`predicted_count` 零缺失。`model_version` 应有 **2** 个（v1 + 故意训坏的 `nomonth`） |
| E8 | F5 的 MAE 复算 | 全量 MAE 与留出季 MAE 分开算。留出季 **MAE ≈ 7.345 / 基线 23.628**。🔴 **无论结果多好，「模型优于基线」都不是可上台的结论**（禁语 ③）——这里只验数字复现，不改结论 |
| E9 | `actual_count` 的零膨胀 | 25 分位 **= 0**（L3 launch §3.3 的三条保留之一）。若不为 0，是留出季与全量的口径差，要说明 |

### 13.2 可执行 SQL（docker trino，逐段贴回）

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
-- E1 D4 骨架
SELECT COUNT(*) AS events,
       COUNT(*) FILTER (WHERE is_scheduling_era) AS scheduling_era,
       MIN(start_date) AS first_start, MAX(end_date) AS last_end,
       APPROX_PERCENTILE(CAST(duration_days AS DOUBLE), 0.5) AS median_days,
       MAX(duration_days) AS max_days,
       COUNT(*) FILTER (WHERE accum_flag) AS accum_true,
       COUNT(*) FILTER (WHERE accum_flag IS NULL) AS accum_null,
       COUNT(DISTINCT event_rule_version) AS rule_versions
FROM dim_snowfall_event;
SQL
```

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
-- E2 按雪季分布
SELECT snow_season,
       COUNT(*) AS events,
       ROUND(SUM(total_snowfall_cm), 1) AS season_cm,
       ROUND(MAX(peak_daily_snowfall_cm), 1) AS peak_cm,
       COUNT(*) FILTER (WHERE is_scheduling_era) AS in_era
FROM dim_snowfall_event
GROUP BY snow_season
ORDER BY snow_season;
SQL
```

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
-- E3 severity 是否单调同向
SELECT COUNT(*) FILTER (WHERE severity_score IS NULL) AS severity_null,
       ROUND(CORR(severity_score, total_snowfall_cm), 3)     AS r_total,
       ROUND(CORR(severity_score, peak_daily_snowfall_cm), 3) AS r_peak,
       ROUND(CORR(severity_score, CAST(duration_days AS DOUBLE)), 3) AS r_days,
       ROUND(MIN(severity_score), 2) AS min_s, ROUND(MAX(severity_score), 2) AS max_s
FROM dim_snowfall_event;
SQL
```

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
-- E4 D5 对齐与 lag
SELECT p.plow_event_id, p.is_aligned,
       CAST(p.first_shift_start_utc AS DATE) AS shift_day,
       e.end_date AS event_end,
       DATE_DIFF('day', e.end_date, CAST(p.first_shift_start_utc AS DATE)) AS lag_days
FROM dim_plow_event p
LEFT JOIN dim_snowfall_event e ON e.snowfall_event_id = p.matched_snowfall_event_id
ORDER BY p.first_shift_start_utc;
SQL
```

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
-- E5 F1 非零格：总量、分排班期、以及每事件分布的两端
WITH nz AS (
  SELECT f.snowfall_event_id, e.is_scheduling_era,
         COUNT(*) FILTER (WHERE f.request_count > 0) AS nonzero_cells
  FROM fact_service_request_zone_event f
  JOIN dim_snowfall_event e ON e.snowfall_event_id = f.snowfall_event_id
  GROUP BY f.snowfall_event_id, e.is_scheduling_era
)
SELECT is_scheduling_era,
       COUNT(*) AS events,
       SUM(nonzero_cells) AS nonzero_cells,
       MIN(nonzero_cells) AS min_per_event,
       MAX(nonzero_cells) AS max_per_event,
       COUNT(*) FILTER (WHERE nonzero_cells = 0) AS empty_events
FROM nz GROUP BY is_scheduling_era ORDER BY is_scheduling_era;
SQL
```

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
-- E6 F8 年份覆盖（按 label_type 分开，绝不合计）
SELECT label_type, YEAR("date") AS yr,
       COUNT(DISTINCT label_id) AS labels,
       SUM(request_count) AS label_hits
FROM fact_winter_request_daily_by_label
GROUP BY label_type, YEAR("date")
ORDER BY label_type, yr;
SQL
```

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
-- E7 + E8 + E9 F5 面板完整性、MAE、零膨胀
SELECT model_version,
       COUNT(*) AS rows_,
       COUNT(*) FILTER (WHERE predicted_count IS NULL) AS pred_null,
       COUNT(*) FILTER (WHERE actual_count IS NULL)    AS actual_null,
       ROUND(AVG(ABS(predicted_count - actual_count)), 3) AS mae_model,
       ROUND(AVG(ABS(baseline_count  - actual_count)), 3) AS mae_baseline,
       APPROX_PERCENTILE(CAST(actual_count AS DOUBLE), 0.25) AS p25_actual,
       APPROX_PERCENTILE(CAST(actual_count AS DOUBLE), 0.50) AS p50_actual,
       MAX(actual_count) AS max_actual
FROM fact_request_forecast
GROUP BY model_version ORDER BY model_version;
SQL
```

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
-- E8b 留出季单独复算（雪季来自 D4，不硬编码日期）
SELECT f.model_version, e.snow_season,
       COUNT(*) AS cells,
       ROUND(AVG(ABS(f.predicted_count - f.actual_count)), 3) AS mae_model,
       ROUND(AVG(ABS(f.baseline_count  - f.actual_count)), 3) AS mae_baseline
FROM fact_request_forecast f
JOIN dim_snowfall_event e ON e.snowfall_event_id = f.snowfall_event_id
WHERE e.snow_season = (SELECT MAX(snow_season) FROM dim_snowfall_event)
GROUP BY f.model_version, e.snow_season
ORDER BY f.model_version;
SQL
```

### 13.3 交接

`make lint` 未跑（本轮只改 `docs/`，无 SQL/Python 变更）。
接手顺序：**先 §12.1（B10，阻塞 BO-6 口径）**，再 §12.2 / §12.3，最后 §13.2 的
八段。结果贴回后我按 §13.1 的判据逐条判、写进台账 §4（BO-3）与 §5（BO-1），
然后才写阶段 4（BO-6 + BO-8）的提问单——**恢复一轮一停**。

---

## 14. 阶段 2 补测 + 阶段 3 结果（2026-08-31 实测）

> 🔴 **先说一件不好看的事：这一批九条判据里,数据一条没坏,而我写的判据错了四条。**
> E3 的阈值在数学上不可能达到 · E4 量在错的锚点上 · E5 与 E7 的期望值取自
> **另一个粒度**。四条都不是「数据不符合预期」,是**判据本身问错了问题**——
> 与 §4.9(F1 恒真的 `cells`)、P2(`etl_run_id`)、DQ 第二批(F1 漏
> `is_scheduling_era`)是同一个病。§4.9 那条自检问句要扩一条:
> **「这个判据的数,是在跟我现在这条查询同一个粒度上量的吗?」**

### 14.1 ✅ B10：NULL 权重是**按 1 兜底**,不是零

| winter_category | cells | requests | weighted | 加权比 |
|---|---|---|---|---|
| SNOW | 2,178 | 33,639 | 43,314.0 | 1.288 |
| FROZEN | 2,178 | 4,293 | 5,389.0 | 1.255 |
| **PLOW** | 2,178 | 4,074 | **4,074.0** | **1.000** |
| SANDING | 2,178 | 3,127 | 6,038.0 | **1.931** |
| **ICE_CONTROL** | 2,178 | 847 | **847.0** | **1.000** |
| **WINDROW** | 2,178 | 0 | 0.0 | — |

`weighted_null_cells = 0` 逐类为零,三个 NULL 权重类的 `weighted` **精确等于**
`requests`。按 §12.1 的判据表落在第二档:**不是缺陷,BO-6 的需求项确实覆盖六类。**
台账 §3.3 保留 5 的红色警告撤销。

🔴 **但撤销的是「归零」,不是「没问题」。** 兜底值 1 是 1–3 量表的**下限**,
于是 PLOW(4,074 条,第二大类)和 ICE_CONTROL 被**默认按最低优先级**计入,
而 SANDING 的加权比接近 2、SNOW 1.29。**没有人做过这个决定**——它是
`dim_service_type` 种子里三类没填权重的副作用。正确的表述是
**「三类未分级,按 1 计」**,不是「三类优先级低」。要改得走种子 + F1/F6 重建,
本轮不动;**图注必须写这句**。

### 14.2 ✅ B11：三个 id 的名称已落实(上游只读)

| `ban_type_id` | description | 条数 | 匹配犁雪班次 |
|---|---|---|---|
| `1` | **ANNUAL SNOW ROUTE** | 11 | 0 |
| `2` | **EXTENDED SNOW ROUTE** | 19 | 0 |
| `4` | **RESIDENTIAL (KNOW YOUR ZONE)** | 19 | **19（100%）** |

恰好 3 组、取值 1/2/4、无 `3`,与契约逐字一致,上游自 2026-08-02 采集以来未变。
§4.10 的推断**由此升级为证据**:唯一产生逐分区班次的是**按分区**的
KNOW YOUR ZONE;ANNUAL 的 11 条恰好是 2015-16 … 2025-26 十一个雪季、
每季一条的季长声明。C2-10 定稿,FIG-BO2-05 可以写名字。

### 14.3 E1 ✅ 骨架三个数全中,而且 **`accum_flag` 有 8 个 true**

```
events 99 · scheduling_era 59 · 2008-11-06 → 2026-04-17
median_days 1.08 · max_days 14 · accum_true 8 · accum_null 0 · rule_versions 1
```

🟢 **CLAUDE.md 里 BO-3 那条未决项已经落地了,只是台账没记。** 读
`spark/transforms/weather_archive.py:150-220`:切分**已经是**「单日阈值
**或** 滚动累积阈值」的并集,`accum_flag` 的定义是
`peak_daily_snowfall_cm < threshold`——即**该事件只因滚动累积判据才存在**,
没有任何一天单独过线。8 个这样的事件在库里。
所以「BO-3 必须在单日阈值之外再加一条滚动累积判据」**不是待办,是已交付**;
CLAUDE.md 那段该改成完成态(不在本轮改,记在 §6 的 L9)。

⚠️ 但**不要**顺势说「4 次无降雪犁雪已被这条判据救回」——E4 显示仍有
**2** 次(2021-01-07 / 2026-02-26)`is_aligned = false`。8 个 accum 事件
和那 4 次的关系没有量过,是 BO-3 图注的一个具体待办。

### 14.4 E2 ✅ 18 个雪季无空档,`in_era` 与 59 自洽

每个雪季 ≥ 2 个事件,**没有一季为 0**,时间线可以画成连续序列(与 §2.3
保留 3 的 2017/2023 犁雪空档是两回事——**空的是犁雪事件,不是降雪事件**)。
`in_era` 逐季相加 = 8+5+3+5+3+3+11+7+5+2+7 = **59** ✅,且 2015-2016 的 8 个
**全部**在期内,印证纪元边界取 2015-11-01(雪季起点)而非 2015-12-01。

两个可上台的极值:**2021-2022 是十八冬最重的一季**(11 个事件 / 106.2 cm,
第二名 2016-2017 才 65.2);**2024-2025 最轻**(2 个事件 / 19.3 cm)。

### 14.5 E3 ⚠️ 判据不达标,但**错的是判据**——`severity_score` 里根本没有降雪时长

```
severity_null 0 · r(total) 0.760 · r(peak) 0.586 · r(duration) 0.828 · 范围 0.06–0.90
```

我写的判据是「与 `total_snowfall_cm` 的 Pearson ≥ +0.8」。实现是
`sql/dml/dim_snowfall_event.sql:57-65`:

```
severity = 0.5 × minmax(total_snowfall_cm) + 0.5 × minmax(cold)
```

**一个 50/50 的二元混合量,与其中一半的相关系数在另一半独立时数学上到不了 0.8。**
判据不可能达成,与列本身无关。

🔴 **真正要记的是另一件事:`severity_score` 不含时长,却与时长的相关(0.828)
高于与它自己那一半降雪量的相关(0.760)。** 读者看图会以为「严重度=下得久」,
而定义里是「下得多 + 冷」。**图注必须写出定义**(降雪量与低温各半,在 99 个
事件上各自 min-max 归一),否则这张图会让人「确认」一个错的因果。
🟡 上限 0.90 而非 1.0:没有哪个事件同时是最大降雪和最低温。

### 14.6 E4 🔴 **lag 有两个锚点,17 天和 7 天是同一件事的两个量**

19 行全回。`is_aligned` **17 true / 2 false** ✅,两个 false 是
`2021-01-07` 与 `2026-02-26`,正是 CLAUDE.md 那份四条名单在宽 lag 下剩下的两条。

我的判据写「最大 lag 应达到 17 天」,实测最大 **+7**。差异不是数据不符:

- CLAUDE.md 的 17 天量的是 **事件开始 → 首班**(`2021-11-10` → `2021-11-27`);
- 我这条查询量的是 **事件结束 → 首班**(`2021-11-20` → `2021-11-27` = **7**)。

两个都对。**但一个「延迟」有两个相差 10 天的数,就是台账里最容易出事的形状。**
规则:**凡引用 lag,必须写明锚点**,BO-3 的图注固定用「事件结束起算」并注明
起算点,因为「雪停之后多久开始犁」才是运营问题。

🔴 **更值得上台的是判据没问的那件事:17 次里有 11 次 lag 为负**
(−1 ×2 / −4 / −6 ×5 / −7),即**犁雪在降雪事件结束前就开始了**。
配上 `max_days = 14`,这说明多日事件里边下边犁是常态。
**「响应延迟」这个框架本身对多日事件不成立**,时间轴图不能画成
「事件 → 箭头 → 犁雪」的单向序列。

### 14.7 E5 ⚠️ 我把 880 的粒度记错了;数据本身给出三条新事实

实测(**事件 × 分区 × 类别** 粒度):

| `is_scheduling_era` | events | 非零格 | 每事件最少 | 最多 | 空事件 |
|---|---|---|---|---|---|
| false | 40 | 1,084 | 0 | 69 | **9** |
| true | 59 | 2,060 | 0 | 85 | **2** |

880 那个下界是在 **事件 × 分区**(每事件 22 格,上限 2,178)上量的,
而这条查询是 **事件 × 分区 × 类别**(每事件 132 格,上限 13,068)。
**两个数没有可比性**,判据作废,不是门禁失效。等值口径的复核见 §14.10 的 E5b。

数据给出的三条(都可上台):

1. **11 个事件在任何分区、任何类别上都没有一条冬季工单**(9 非排班期 + 2 排班期)。
   非排班期集中在 311 数据稀薄的早年——**事件时间线上这 11 个点不能和其他点同色**。
2. 每事件最多 85 / 132 格,**没有任何一个事件填满**,与 WINDROW 恒零一致(≤110)。
3. 排班期 59 个事件占了 2,060 / 3,144 = **65.5%** 的非零格,而事件数只占 59.6%——
   排班期的事件确实更「满」,但差距不大。

### 14.8 E6 ✅ 18 个年份;🔴 ward 与 neighbourhood 的标签次数**不相等**

年份 **2009–2026 共 18 个**(不是 19)✅,`ward` 逐年恰好 15 个 ✅,
`neighbourhood` 每年 218–235 个。

🔴 判据没问、但数据自己说出来的:**2019 年起 ward 的标签次数持续多于
neighbourhood**——2019 +3 · 2020 +16 · 2021 +15 · 2022 +11 · 2023 +3 ·
2024 +1 · 2025 +1 · 2026 0;2009–2018 **逐年完全相等**。

量级很小(最多 16/年,占该年万分之十几),但**是系统性的、且有明确起点**:
2019 年之后出现了**带 ward 标签却没有 neighbourhood 标签**的工单。
这与 §3.3 保留 3 的 4 个孤儿 neighbourhood 是**不同方向**的缺口
(那条是标签进不了 crosswalk,这条是工单拿不到 neighbourhood 标签)。
**两条 F8 的序列不能画在同一根 y 轴上而不说明**;⏳ 成因未查,记 L10。

### 14.9 E7/E8/E9 —— 面板是 **1,298 不是 2,178**,而三个已发表的数逐位复现

```
m1-poisson-20260822-df31d954        1298 行 · pred_null 0 · actual_null 0
                                    MAE 10.961 · baseline 27.764
m1-poisson-nomonth-20260822-30af82f4 1298 行 · MAE 11.314 · baseline 27.764
p25(actual) ≈ 0.10 · p50 2.90 · max 381
留出季 2025-2026(154 格 = 7 事件 × 22):
  v1      MAE 7.345 · baseline 23.628
  nomonth MAE 7.919 · baseline 23.628
```

- **E7 判据的 2,178 是我抄错的数**:2,178 = 99 事件 × 22 分区,那是**训练面板**;
  F5 只覆盖排班期,**59 × 22 = 1,298**,精确吻合,且两版本合计 2,596 与
  L3 launch 记的 A3b 数一致。零缺失 ✅,版本数 2 ✅。
- **E8 ✅ 留出季 7.345 / 23.628 逐位复现**,与 L3 launch §3.3 完全相同。
  🔴 **仍然不许说「模型优于基线」**(禁语 ③)。
- **E9 ✅ 零膨胀确认**:p25 ≈ 0.1(近似分位数在整数上的插值),p50 才 2.9,
  而 max 381。**分布极偏,任何用均值描述这个面板的图都会骗人。**

🔴 **本批新增的一条保留,比上面几条都硬:故意训坏的 `nomonth` 只差 0.574 个 MAE**
(留出季 7.919 vs 7.345;全量 11.314 vs 10.961)。把月份特征整个拿掉,
误差只涨 7.8%——**这说明月份特征几乎没起作用,而不是模型稳健**。
它同时削弱了「模型比基线好」的论证:两个模型(其中一个是对照坏样本)与基线的
差距(≈16)远大于彼此的差距(0.57),更像**基线本身太弱**。
**FIG-BO1-03 必须三条线同框(v1 / nomonth / baseline),不能只画 v1 和 baseline。**

### 14.10 阶段 3 收尾的两条补测

```bash
# E5b 把非零格拉回 880 那个粒度（事件 × 分区，跨类别求和）
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
WITH z AS (
  SELECT f.snowfall_event_id, f.plow_zone, e.is_scheduling_era,
         SUM(f.request_count) AS reqs
  FROM fact_service_request_zone_event f
  JOIN dim_snowfall_event e ON e.snowfall_event_id = f.snowfall_event_id
  GROUP BY f.snowfall_event_id, f.plow_zone, e.is_scheduling_era
)
SELECT is_scheduling_era, COUNT(*) AS cells,
       COUNT(*) FILTER (WHERE reqs > 0) AS nonzero_cells
FROM z GROUP BY is_scheduling_era ORDER BY is_scheduling_era;
SQL
```

判据:`is_scheduling_era = true` 一行的 `nonzero_cells` 应 **≥ 880**
(§4.9 的下界,门禁与 DQ 规则量的都是这一个数,实测 908)。
两行 `cells` 应分别是 40×22 = 880 与 59×22 = 1,298。

```bash
# E4b 八个 accum_flag 事件是哪些，与两次未对齐犁雪什么关系
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
SELECT snowfall_event_id, start_date, end_date, duration_days,
       ROUND(total_snowfall_cm, 1) AS total_cm,
       ROUND(peak_daily_snowfall_cm, 1) AS peak_cm,
       is_scheduling_era
FROM dim_snowfall_event
WHERE accum_flag
ORDER BY start_date;
SQL
```

判据:8 行,每行 `peak_cm < 3.0`(定义如此,是自检不是发现)。
真正要看的是**有没有一个落在 2021-01-07 或 2026-02-26 前后两周内**——
若有,那两次「无降雪犁雪」其实已被累积判据覆盖、只是没连上;若没有,
CLAUDE.md 里那条 BO-3 遗留仍然是开的。

---

## 15. 阶段 3 收尾（2026-08-31）——两条补测都给出了硬结论

### 15.1 ✅ E5b：**908 逐位复现**，而且顺带印证了 DQ 第二批那个缺陷的算术

| `is_scheduling_era` | cells | 非零格 |
|---|---|---|
| false | **880** = 40 × 22 | 528 |
| true | **1,298** = 59 × 22 | **908** |

- 排班期 **908**，与 §4.9 的门禁数、L3 的记录**逐位相同**，下界 ≥880 成立。
- 两行 `cells` 恰好是 880 与 1,298 ✅，粒度确认无误。§14.7 的判据作废是我记错了
  粒度，不是门禁失效。
- 🟢 **528 + 908 = 1,436**——正是 DQ 第二批那条漏了 `is_scheduling_era` 的规则
  数出来的值（CLAUDE.md「管道外 DQ 审计」§首跑缺陷 ①）。当时记的是「规则数成
  1,436 而门禁量的是 908」，**今天两个数在同一次查询里对上了**，那条复盘的算术
  到此有了直接证据。

### 15.2 🟢 E4b：滚动累积判据**救回了四次里的一次**，两次仍然开着

八个 `accum_flag` 事件（`peak_cm < 3.0` 逐行成立，是定义的自检不是发现）：

| event_id | 起—讫 | 天 | total_cm | peak_cm | 排班期 |
|---|---|---|---|---|---|
| SNOW-20130115 | 2013-01-15 → 01-20 | 6 | 5.4 | 2.0 | false |
| SNOW-20140123 | 2014-01-23 → 01-26 | 4 | 5.2 | 2.6 | false |
| SNOW-20160410 | 2016-04-10 | 1 | 1.5 | 1.5 | true |
| SNOW-20220227 | 2022-02-27 | 1 | 1.0 | 1.0 | true |
| SNOW-20221115 | 2022-11-15 | 1 | **0.2** | 0.2 | true |
| **SNOW-20221118** | 2022-11-18 → **11-19** | 2 | 1.8 | 1.0 | true |
| SNOW-20260312 | 2026-03-12 → 03-15 | 4 | 2.0 | 1.8 | true |
| SNOW-20260331 | 2026-03-31 → 04-01 | 2 | 1.4 | 1.4 | true |

**切分规则是 `v1-3cm-or-10d10cm`**（`etl_weather_archive.py:62-66`）：单日 ≥ 3 cm
**或** trailing 10 日累计 ≥ 10 cm，`gap_days = 1`。

🟢 **CLAUDE.md 那份「四次无降雪犁雪」的名单现在可以逐条结账了**
（2021-01-07 / 2021-11-27 / 2022-11-24 / 2026-02-26）：

| 犁雪日 | 现状 | 是什么救回来的 |
|---|---|---|
| 2021-11-27 | ✅ 已对齐（事件讫 2021-11-20，lag +7） | **宽 lag**——匹配的是普通事件 |
| **2022-11-24** | ✅ 已对齐（事件讫 **2022-11-19**，lag +5） | 🟢 **滚动累积判据**——`SNOW-20221118` 的 `accum_flag = true`，峰值只有 1.0 cm |
| 2021-01-07 | 🔴 仍未对齐 | 2021 年**没有任何** accum 事件 |
| 2026-02-26 | 🔴 仍未对齐 | 最近的 accum 事件是 2026-03-12，在犁雪**之后** 14 天，方向不对 |

**四分之一是累积判据救的,四分之一是宽 lag 救的,一半仍然开着。**
这是本轮对 BO-3 最实的一条:那条判据**有效但不充分**。

✅ **§15.3 的确认已跑（2026-08-31），两行逐字命中判据**：

```
28163597 · 2021-11-27 → SNOW-20211110 (2021-11-10 → 11-20) accum=false · 26.2 cm
30063633 · 2022-11-24 → SNOW-20221118 (2022-11-18 → 11-19) accum=true  ·  1.8 cm
```

归因不再是反推。🟢 **而且第一行把 §14.6 的锚点解释也变成了直接证据**：
`SNOW-20211110` 起 2021-11-10、讫 11-20，首班 11-27——
**从起算 17 天、从讫算 7 天**，CLAUDE.md 那个 17 与 E4 那个 7 是同一件事的两个锚点，
一字不差。两个数都不用改，**要改的是引用时必须写锚点**。
🟡 顺带一条对比很有说服力：救回 2022-11-24 的事件全期只有 **1.8 cm**，
而 2021-11-27 那次是 **26.2 cm** ——同样是「犁雪」，触发它的降雪量差 14 倍。

🔴 **同时暴露一个切分假象:三个 accum 事件的全期降雪量小到没有运营意义**
(0.2 cm · 1.0 cm · 1.4 cm)。机制是清楚的:trailing 10 日窗口在一场大雪之后
**还会继续命中十天**,而 `gap_days = 1` 又把这条尾巴**切成独立事件**。
`SNOW-20221115`(0.2 cm)与 `SNOW-20221118`(1.8 cm)相隔 3 天被切开,
两者都是同一场雪的尾巴,不是两场新雪。

后果有三层,**都不改口径、只约束表述**:

1. **N = 99 里混着「事件」与「事件的尾巴」。** 讲 N 时不要讲成「99 场雪」。
2. **面板里那 11 个零工单事件**(§14.7)与这几个微量事件**高度重合的可能性很大**——
   一场 0.2 cm 的「事件」当然没有冬季工单。⏳ 未量,记 L13。
3. 🟢 **但 2022-11-24 那次救回仍然成立**:CLAUDE.md 查清的成因本来就是
   **阈下累积**(21 日累计保留对照组 76%,单日峰值只保留 26%),
   雪确实下了、只是从没有单日过线。**匹配到一段尾巴,恰恰是这条机制的正确表现。**

### 15.3 上台前的一条确认（很短，不阻塞阶段 4）

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
SELECT p.plow_event_id, CAST(p.first_shift_start_utc AS DATE) AS shift_day,
       p.matched_snowfall_event_id, e.start_date, e.end_date,
       e.accum_flag, ROUND(e.total_snowfall_cm, 1) AS total_cm
FROM dim_plow_event p
LEFT JOIN dim_snowfall_event e ON e.snowfall_event_id = p.matched_snowfall_event_id
WHERE CAST(p.first_shift_start_utc AS DATE)
      IN (DATE '2021-11-27', DATE '2022-11-24')
ORDER BY shift_day;
SQL
```

判据：`2022-11-24` 那行的 `matched_snowfall_event_id` = **`SNOW-20221118`** 且
`accum_flag = true`；`2021-11-27` 那行 `accum_flag = false`。
若不符，§15.2 的救回归因要改写。

---

## 16. 阶段 4 提问单：BO-6（负荷评分）+ BO-8（推荐与归因）

三张表：**F6** `fact_winter_event_zone_load`（1,298）· **F7** `fact_recommendation`
（748）· **D7** `dim_recommendation_rules`（6 条种子）。

这是四条禁语全部落地的一轮——前三轮的禁语是「讲的时候别说错」，这一轮是
**「先量出禁语说的那件事到底是不是真的」**。§14 的教训（判据的数取自另一个粒度）
在这一轮有一个现成的复发点，我把它写在 G5，**并且预告我预期判据会不成立**。

### 16.1 先写死的判据

| # | 问题 | 判据（先写） | 若不成立意味着 |
|---|---|---|---|
| **G1** | F6 骨架 | 1,298 = 374 `scored` + 924 `partial_no_rank`，`no_schedule_era` **0** 行；59 事件 × 22 分区 | 面板不完整，BO-6 的「全面板」表述要撤 |
| **G2** | status ↔ profile 是否 1:1 | 四条违例计数**全为 0**，`rank_factor = 0` 也是 0 | 两列有一列是装饰性的 |
| **G3** | `rank_factor` 值域 | 非空 374 行、取值恰 5 个（0.2/0.4/0.6/0.8/1.0），最小 **0.2 不是 0**；NULL 924 | 「NULL 不是 0」这条 ADR 0008 的主张失去证据 |
| **G4** | 天气因子是不是事件级常量 | 每个事件的 `COUNT(DISTINCT weather_severity_factor)` **最大值 = 1** | H1 降级（launch 20260813 A2）没落实，或落实成了别的东西 |
| **G5** | 🔴 924 格到底能不能到 `CRITICAL` | **我预期能**，且 `partial_no_rank` 的 CRITICAL 起点是 **52.5**（`0.75 × 70`）不是 75 | 见下方 🔴 |
| **G6** | F7 骨架与排列性 | 748 = 2 版本 × 374；每 (事件, 版本) 的 `rank_model` 与 `rank_baseline` 都是 **1..22 的排列**（n=22, distinct=22, min=1, max=22），违例 0 | 排名不是排列，`rank_delta` 的一切解读作废 |
| **G7** | 🔴 `rank_delta` 的位移性质 | 每个 (事件, 版本) 内 **`SUM(rank_delta) = 0` 恒成立**，违例 0 条；两个版本各 374 行 | 禁语 ① 失去它唯一的可执行证据 |
| **G8** | 六条规则用掉几条 | **只用掉 4 条**，`RULE-NO-SCHEDULE` 与 `RULE-FALLBACK` 各 0 次 | 见下方 🟡 |
| **G9** | 🔴 `attribution_text` 残留占位符 | `LIKE '%{%'` 与 `LIKE '%}%'` 命中数**都为 0** | 模板没替换干净，而这条门禁**从建立起没执行过一次** |
| **G10** | L13：11 个零工单事件与 8 个 `accum_flag` 事件重合吗 | 无预期，纯测量 | —— |

🔴 **G5 是我故意跟 CLAUDE.md 顶起来的一条。** CLAUDE.md 与 L3 launch §8 把禁语 ②
的理由写成「`demand_weather_only` 天花板 70 而 CRITICAL 门槛 75，那 924 格
**永远不可能到 CRITICAL**」。但 `sql/intelligence/fact_winter_event_zone_load.sql`
的分级是**按各自 profile 的 ceiling 缩放**的：

```sql
CASE WHEN rank_factor IS NULL THEN 70.0 ELSE 100.0 END AS score_ceiling
...
WHEN load_score < 0.75 * score_ceiling THEN 'HIGH' ELSE 'CRITICAL'
```

所以 partial 行的 CRITICAL 起点是 **52.5**，它**能**到 CRITICAL。
如果 G5 证实这一点，那么：

- **禁语 ② 本身仍然成立**，但**它现在的理由是错的**——不是「够不着」，而是
  **两个 CRITICAL 不是同一个量**：一个是「满分 100 里拿到 75」，另一个是
  「满分 70 里拿到 52.5」。同名不同尺，比「短三成的尺子」更难被读者察觉。
- 这跟 §14 是同一种错：**判据的数取自另一个版本的实现**（固定阈值 75），
  而生产跑的是缩放阈值。CLAUDE.md 那句要改。

🟡 **G8 预期只用掉 4 条**，因为 `sql/intelligence/fact_recommendation.sql` 的
`CASE` 只发得出四个 id，而 `RULE-NO-SCHEDULE` 的措辞（"has no plow schedule"）
正是给那 924 个 `partial_no_rank` 格准备的——**而 F7 明确不覆盖它们**。
两条种子规则不是死代码，是**一个没做的功能留下的接口**。这条要写进台账，
别让下一个人以为它坏了。

### 16.2 可执行 SQL（docker trino，逐段贴回）

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
-- G1 F6 骨架
SELECT score_status,
       score_weight_profile,
       count(*) AS cells,
       count(DISTINCT snowfall_event_id) AS events,
       count(DISTINCT plow_zone) AS zones
FROM fact_winter_event_zone_load
GROUP BY 1, 2
ORDER BY 1, 2;

SELECT count(*) AS total_cells,
       count(DISTINCT snowfall_event_id) AS events,
       count(DISTINCT plow_zone) AS zones,
       count(DISTINCT forecast_model_version) AS serving_versions,
       min(forecast_model_version) AS serving_version
FROM fact_winter_event_zone_load;
SQL
```

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
-- G2 status/profile 一致性（四条违例 + rank_factor=0）
SELECT count(*) FILTER (WHERE score_status = 'scored'
                          AND score_weight_profile <> 'full_3factor') AS bad_scored_profile,
       count(*) FILTER (WHERE score_status = 'partial_no_rank'
                          AND score_weight_profile <> 'demand_weather_only') AS bad_partial_profile,
       count(*) FILTER (WHERE score_status = 'scored'
                          AND rank_factor IS NULL) AS scored_but_null_rank,
       count(*) FILTER (WHERE score_status = 'partial_no_rank'
                          AND rank_factor IS NOT NULL) AS partial_but_has_rank,
       count(*) FILTER (WHERE score_status = 'no_schedule_era') AS no_schedule_era_rows,
       count(*) FILTER (WHERE rank_factor = 0) AS zero_rank_factor
FROM fact_winter_event_zone_load;
SQL
```

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
-- G3 rank_factor 值域（NULL 会单独成一组）
SELECT rank_factor,
       count(*) AS cells,
       count(DISTINCT snowfall_event_id) AS events
FROM fact_winter_event_zone_load
GROUP BY 1
ORDER BY 1;
SQL
```

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
-- G4 天气因子是否事件级常量
SELECT max(k) AS max_distinct_per_event,
       min(k) AS min_distinct_per_event,
       count(*) AS events
FROM (
    SELECT snowfall_event_id, count(DISTINCT weather_severity_factor) AS k
    FROM fact_winter_event_zone_load
    GROUP BY 1
);

SELECT count(DISTINCT weather_severity_factor) AS distinct_values_whole_panel,
       round(min(weather_severity_factor), 4) AS lo,
       round(max(weather_severity_factor), 4) AS hi
FROM fact_winter_event_zone_load;
SQL
```

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
-- G5 🔴 load_level × profile 交叉表 + 各 profile 的分数区间
SELECT score_weight_profile,
       load_level,
       count(*) AS cells,
       round(min(load_score), 2) AS lo,
       round(max(load_score), 2) AS hi
FROM fact_winter_event_zone_load
GROUP BY 1, 2
ORDER BY 1, min(load_score);
SQL
```

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
-- G6 F7 骨架 + 排列性
SELECT model_version,
       count(*) AS rows_,
       count(DISTINCT snowfall_event_id) AS events,
       count(DISTINCT plow_zone) AS zones
FROM fact_recommendation
GROUP BY 1
ORDER BY 1;

SELECT count(*) FILTER (WHERE NOT (n = 22 AND d_model = 22 AND lo_model = 1 AND hi_model = 22))
           AS bad_rank_model,
       count(*) FILTER (WHERE NOT (n = 22 AND d_base = 22 AND lo_base = 1 AND hi_base = 22))
           AS bad_rank_baseline,
       count(*) AS event_version_groups
FROM (
    SELECT snowfall_event_id,
           model_version,
           count(*) AS n,
           count(DISTINCT rank_model) AS d_model,
           min(rank_model) AS lo_model,
           max(rank_model) AS hi_model,
           count(DISTINCT rank_baseline) AS d_base,
           min(rank_baseline) AS lo_base,
           max(rank_baseline) AS hi_base
    FROM fact_recommendation
    GROUP BY 1, 2
);
SQL
```

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
-- G7 🔴 rank_delta 的位移性质（禁语 ① 的可执行证据）
SELECT model_version,
       count(*) AS rows_,
       sum(rank_delta) AS sum_delta,
       count(*) FILTER (WHERE rank_delta > 0) AS moved_up,
       count(*) FILTER (WHERE rank_delta = 0) AS unchanged,
       count(*) FILTER (WHERE rank_delta < 0) AS moved_down,
       max(rank_delta) AS biggest_up,
       min(rank_delta) AS biggest_down
FROM fact_recommendation
GROUP BY 1
ORDER BY 1;

SELECT count(*) AS event_versions_with_nonzero_sum
FROM (
    SELECT snowfall_event_id, model_version, sum(rank_delta) AS s
    FROM fact_recommendation
    GROUP BY 1, 2
)
WHERE s <> 0;
SQL
```

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
-- G8 六条规则用掉几条 + 按版本的分布
SELECT d.rule_id,
       d.is_fallback,
       coalesce(f.uses, 0) AS uses
FROM dim_recommendation_rules AS d
LEFT JOIN (
    SELECT attribution_rule_id, count(*) AS uses
    FROM fact_recommendation
    GROUP BY 1
) AS f ON f.attribution_rule_id = d.rule_id
ORDER BY uses DESC, d.rule_id;

SELECT model_version,
       attribution_rule_id,
       count(*) AS cells
FROM fact_recommendation
GROUP BY 1, 2
ORDER BY 1, 3 DESC;
SQL
```

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
-- G9 🔴 占位符残留（这条门禁的第一次真实执行）
SELECT count(*) AS rows_total,
       count(*) FILTER (WHERE attribution_text LIKE '%{%') AS left_brace,
       count(*) FILTER (WHERE attribution_text LIKE '%}%') AS right_brace,
       count(*) FILTER (WHERE attribution_text LIKE '%n/a%') AS na_shift,
       count(DISTINCT attribution_text) AS distinct_texts
FROM fact_recommendation;

SELECT attribution_rule_id, min(attribution_text) AS sample_text
FROM fact_recommendation
GROUP BY 1
ORDER BY 1;
SQL
```

```bash
sudo docker exec -i trino trino --catalog hive --schema uoip_gold <<'SQL'
-- G10 L13：零工单事件 vs accum 事件
WITH per_event AS (
    SELECT e.snowfall_event_id,
           e.accum_flag,
           e.is_scheduling_era,
           e.total_snowfall_cm,
           coalesce(sum(f.request_count), 0) AS reqs
    FROM dim_snowfall_event AS e
    LEFT JOIN fact_service_request_zone_event AS f
        ON f.snowfall_event_id = e.snowfall_event_id
    GROUP BY 1, 2, 3, 4
)

SELECT accum_flag,
       count(*) AS events,
       count(*) FILTER (WHERE reqs = 0) AS zero_request_events,
       round(min(total_snowfall_cm), 1) AS min_cm,
       round(max(total_snowfall_cm), 1) AS max_cm
FROM per_event
GROUP BY 1
ORDER BY 1;

WITH per_event AS (
    SELECT e.snowfall_event_id,
           e.accum_flag,
           e.is_scheduling_era,
           e.total_snowfall_cm,
           coalesce(sum(f.request_count), 0) AS reqs
    FROM dim_snowfall_event AS e
    LEFT JOIN fact_service_request_zone_event AS f
        ON f.snowfall_event_id = e.snowfall_event_id
    GROUP BY 1, 2, 3, 4
)

SELECT snowfall_event_id,
       accum_flag,
       is_scheduling_era,
       round(total_snowfall_cm, 1) AS total_cm
FROM per_event
WHERE reqs = 0
ORDER BY 1;
SQL
```

### 16.3 交接

十段可以**一次全部贴回**——它们之间没有依赖，G5 的结论也不改变 G6–G10 问什么。

另外仍然欠两件（不阻塞本轮）：

1. **Q13 余下的 19 行**（§12.3），FIG-BO2-04 的第二条序列要完整 22 行。
2. **Grafana `Rank displacement distribution` 面板的 SQL**。柱子加总恰好 748
   而不是 374——G7 会给出每个版本各 374 行，若面板确实是 748，那它把 v1 与
   **故意训坏的 `nomonth`** 混在了一根柱子里，图注和查询都要改。

本轮同样**不产生任何写操作**，十段全是 `SELECT`。

---

## 17. 阶段 4 结果（2026-08-31 实测）

十段全部跑通，**数据一条没坏**。十条判据里 **九条成立、一条不成立——而不成立的
那条是我的预期错了，不是数据错了**（G5，见 §17.5）。另有**一个新缺陷**：
面向读者的归因句子里把降雪量渲染成了 `2.02E1`（§17.9）。

### 17.1 G1/G2 ✅ 面板骨架与两列的一致性，逐个命中

| status | profile | cells | events | zones |
|---|---|---|---|---|
| `partial_no_rank` | `demand_weather_only` | **924** | **42** | 22 |
| `scored` | `full_3factor` | **374** | **17** | 22 |
| 合计 | | **1,298** | **59** | 22 |

42 + 17 = 59，374 = 17 × 22，924 = 42 × 22。六条违例计数（含 `no_schedule_era`
与 `rank_factor = 0`）**全为 0**。

🟢 `forecast_model_version` **只有一个值** `m1-poisson-20260822-df31d954` ——
即服务版本是显式传进去的那个好版本，**不是故意训坏的 `nomonth`**。
L3 launch §4.6「F6 必须显式传 `FORECAST_VERSION=`」在生产数据上得到了确认。

### 17.2 G3 ✅ 五档齐全、最小 0.2；🔴 但分布高度倾斜

| `rank_factor` | = 班次 | cells | 每事件平均格数 |
|---|---|---|---|
| 0.2 | 1 | 105 | 6.2 |
| 0.4 | 2 | 115 | 6.8 |
| 0.6 | 3 | 115 | 6.8 |
| **0.8** | 4 | **22** | **1.3** |
| **1.0** | 5 | **17** | **1.0** |
| NULL | 无排班 | 924 | —— |

五档合计 374 ✔，最小值 **0.2 不是 0** ✔（ADR 0008「NULL 不是 0」在生产数据上
有据）。

🔴 **但前三班占 335/374 = 89.6%，第五班在每个事件里恰好只有 1 个分区**
（17 格 / 17 事件）。这与阶段 1 的 Q11 是同一件事，现在在评分表上再次出现：
**顺位因子的实际展幅主要是 {0.2, 0.4, 0.6}**，加权后 `contribution_rank` 大多落在
**0.06–0.18**，只有 39 格能到 0.24 以上。图注不能把它画成一个均匀的五档量表。

### 17.3 G4 ✅ 天气因子是事件级常量；🔴 而且它在面板内够不到 1.0

每事件 `COUNT(DISTINCT weather_severity_factor)` 的**最大值与最小值都是 1**，
59 个事件对应 **59 个互不相同的值**——H1 降级（launch 20260813 A2）如实落地。

🔴 **面板内的取值范围是 0.0682 – 0.8978，上限不是 1.0。** 原因是
`severity_score` 的 min-max 归一化跑在 **99 个事件**上，而 F6 只覆盖排班期的
59 个——**最严重的那场雪不在排班期**。连带一个后果：`full_3factor` 的
**实际可达上限不是 100 而是约 96.9**（`0.40×1 + 0.30×1.0 + 0.30×0.8978`）。
上台讲「满分 100」时要知道这一点。

### 17.4 G6/G7 ✅ 排列性无违例；🔴 位移和恒为 0，且两个版本**都是 188 上移**

| model_version | rows | `SUM(rank_delta)` | 上移 | 不动 | 下移 | 最大上移 | 最大下移 |
|---|---|---|---|---|---|---|---|
| `m1-poisson-20260822-df31d954` | 374 | **0** | **188** | 19 | 167 | +13 | −17 |
| `m1-poisson-nomonth-…-30af82f4` | 374 | **0** | **188** | 18 | 168 | +14 | −17 |

- 748 = 2 × 374 ✔；34 个 (事件, 版本) 组的 `rank_model` 与 `rank_baseline`
  **全都是 1..22 的排列**，两列违例各 **0**。
- **`event_versions_with_nonzero_sum = 0`** —— 34 组无一例外。

🔴 **这两行就是禁语 ① 的全部证明，而且比预想的强。** 位移和恒为 0 是结构性的
（两个排名都是同一组 1..22 的排列），所以「188 格上移」必然对应「167 格下移」；
**而故意训坏的 `nomonth` 给出的上移格数与 v1 一模一样，都是 188**。
CLAUDE.md 早就写过这句话，现在它有了可复现的两行数字。

### 17.5 G5 🔴 我预期错了：924 格**确实**没有 CRITICAL——但理由和 CLAUDE.md 说的不是一回事

| profile | level | cells | lo | hi | 该 profile 的分段边界 |
|---|---|---|---|---|---|
| `demand_weather_only` | LOW | **814** | 2.13 | 16.37 | < 17.5 |
| | MED | 88 | 19.23 | 26.26 | 17.5 – 35 |
| | HIGH | **22** | 46.74 | **50.27** | 35 – **52.5** |
| | CRITICAL | **0** | —— | —— | ≥ 52.5 |
| `full_3factor` | LOW | 46 | 13.64 | 24.60 | < 25 |
| | MED | 210 | 25.61 | 49.98 | 25 – 50 |
| | HIGH | 105 | 50.22 | 74.85 | 50 – 75 |
| | CRITICAL | **13** | 75.62 | 90.51 | ≥ 75 |

三件事一起成立：

1. ✅ **我的代码判读是对的**：阈值**确实**按各自 ceiling 缩放。八段的实测边界与
   `0.25 / 0.50 / 0.75 × ceiling` 逐条吻合（partial 的 HIGH 上界 50.27 < 52.5，
   MED 上界 26.26 < 35，LOW 上界 16.37 < 17.5）。**partial 行的 CRITICAL 门槛是
   52.5，不是 75。**
2. ❌ **我的数据预期是错的**：我说「我预期能到 CRITICAL」，实测 **0 格**。
3. 🔴 **所以 CLAUDE.md 的结论对、理由错**，而错的方向最危险：它把一个
   **经验事实**（最高分 50.27，**差 2.23 分**）写成了**结构上不可能**
   （「天花板 70 而门槛 75」）。下一次重建——多一个事件、换一个 M1 版本、
   或者 `severity_score` 的归一化基准变了——完全可能冒出一个 partial 的
   CRITICAL，而按现在的措辞没人会去查。

🔴 **禁语 ② 本身不动，但它的理由要换成「同名不同尺 + 分布形状不同」**：
`partial_no_rank` 有 **88.1%** 的格子是 LOW，`full_3factor` 只有 **12.3%**。
两个 CRITICAL 也不是同一个量——一个是满分 100 拿 75，一个是满分 70 拿 52.5。

🟢 顺带一条读起来很清楚的结构：partial 的分数几乎就是
**30 × severity**（LOW 下限 2.13 ≈ 30 × 0.0682），而那 22 个 HIGH 格
**恰好是同一个事件的全部 22 个分区**，带宽只有 3.53 分。
也就是说 **924 格的事件内排序几乎只由需求因子决定，而它的实际展幅很小**——
这跟 BO-6 早先量到的「天气方差 99.4% 在事件之间」是同一件事的另一面。

### 17.6 G8 ✅ 只用掉 4 条规则，两条接口规则 0 命中

| rule_id | is_fallback | uses | v1 | nomonth |
|---|---|---|---|---|
| `RULE-BALANCED` | false | **431** | 200 | **231** |
| `RULE-WEATHER-DOMINANT` | false | 123 | **77** | 46 |
| `RULE-RANK-DOMINANT` | false | 108 | **54** | **54** |
| `RULE-REQUESTS-DOMINANT` | false | 86 | **43** | **43** |
| `RULE-NO-SCHEDULE` | false | **0** | 0 | 0 |
| `RULE-FALLBACK` | **true** | **0** | 0 | 0 |

- 两条 0 命中的规则**不是坏件**：`fact_recommendation.sql` 的 `CASE` 只发得出
  四个 id，而 `RULE-NO-SCHEDULE` 的措辞正是给那 924 个 `partial_no_rank` 格
  准备的——**F7 明确不覆盖它们**。这是一个没做的功能留下的接口。
- 🔴 **57.6% 的格子归因是「没有单一主导因素」**。上台讲归因时这是第一句话，
  不是脚注：一多半的格子，三个驱动因素的加权贡献差不到 0.05。
- 🟢 **换 M1 版本只在 WEATHER-DOMINANT ↔ BALANCED 之间搬了 31 格**，
  `RANK-DOMINANT`(54) 与 `REQUESTS-DOMINANT`(43) 在两个版本上**逐个相同**——
  因为顺位与天气两项是从 F6 读的、与版本无关，只有需求项换了。

### 17.7 G9 ✅ 占位符门禁首次真实执行：0 残留；🔴 但发现一个新的文本缺陷

`rows_total = 748`，`left_brace = 0`，`right_brace = 0`，`n/a = 0`，
`distinct_texts = 176`。**这条门禁从建立起没执行过一次（`check_gates` 会对
`LIKE '%{%'` 里孤立的 `{` 抛 `ValueError`），今天第一次真跑，是绿的。**

🔴 **但样本句子露出一个门禁问不到的问题**：

> Zone B ranks high mainly on event severity (**2.02E1** cm); …

`CAST(ROUND(total_snowfall_cm, 1) AS VARCHAR)` 对 DOUBLE 在 Trino 上渲染成
**科学计数法**，20.2 cm 变成 `2.02E1`。**这是面向读者的句子，现在表里就是这样**。
又是一次「门禁跑得动但问错了问题」：它检查花括号，不检查可读性。
修法是 `format('%.1f', total_snowfall_cm)`，但要**重建 F7**（写操作），
列为 **L15**，由用户决定 H1 之前还是之后做。

### 17.8 G10 ✅ L13 答案是「不重合」

| `accum_flag` | events | 零工单事件 | 总降雪范围 |
|---|---|---|---|
| false | 91 | **10** | 3.0 – 29.1 cm |
| **true** | **8** | **1** | 0.2 – 5.4 cm |

11 个零工单事件里**只有 1 个**是累积判据救进来的微量事件（`SNOW-20160410`，
1.5 cm）。**L13 的猜想不成立**——零工单不是「微量 accum 事件」造成的。

真正的聚集在另外两个维度上：

- **7/11 是四月的事件**（0403 / 0415 / 0415 / 0412 / 0419 / 0405 / 0410）——雪季末尾；
- **4/11 落在 2008-11 – 2009-01**，即 311 冬季工单最早的那几个月。

🟢 而且**只有 2 个落在排班期内**（`SNOW-20160405` / `SNOW-20160410`），
所以 59 个面板事件里零工单的只有 2 个，F5/F6 的面板不受影响。


---

## 18. 阶段 5 —— 定稿与落地（2026-08-31 开工）

阶段 5 与前五轮**性质不同**：0–4 每轮的产出是「问题 + 数 + 结论」，写进文档；
阶段 5 的产出是**仓库里的文件**。所以这一节没有提问单，只有交付物与验收命令。

🔴 **它仍然不产生写操作。** `sql/presentation/` 全是 `SELECT`，
`scripts/eda/run.py` 不建表、不改 schema、不重建 Gold。唯一会落盘的是
`var/presentation/*.json`（未跟踪产物）。真正的写操作只有一个——L15 的 F7 重建
——那件事**没有做，等决定**（见 §18.5）。

### 18.1 拆成两批的理由

一批把 15 张图一次做完，会在同一个 PR 里混进两类风险：BO-2/BO-4 的数早已定稿、
图注也在台账里逐字写好了，而 BO-3/BO-1/BO-6/BO-8 的四张图各自还挂着一条纪律待处理
（锚点 · 三条保留 · 分面 · 位移）。所以：

| 批 | 覆盖 | 状态 |
|---|---|---|
| **5a** | 骨架（`scripts/eda/` · 两个 make target · 单测）+ **BO-2 五张 + BO-4 三张** | ✅ **本次完成** |
| **5b** | BO-3 三张 + BO-1 三张 + BO-6 三张 + BO-8 两张 · Grafana 三块面板反向进仓（L3）· 三个载体搭起来 | 待 5a 验收 |

选 BO-2/BO-4 打头的理由与阶段 1/2 相同（design §3.5）：它们是 P0−，
数最硬，且**台账里已经有逐字的图注**——头注可以照抄而不用现编。

### 18.2 交付物（已落仓）

| 文件 | 是什么 |
|---|---|
| `sql/presentation/README.md` | 三条硬约束 + 头注键表 |
| `sql/presentation/fig_bo2_0{1..5}_*.sql` | BO-2 五张图的唯一数据源 |
| `sql/presentation/fig_bo4_0{1..3}_*.sql` | BO-4 三张图 |
| `scripts/eda/run.py` | 批量执行 + markdown 输出 + `--json` 冻结 |
| `tests/unit/test_presentation_figures.py` | 60 项，见 §18.4 |
| `make eda-run` / `make eda-export` | 入口 |

**头注即目录，不另存第二份清单。** 每个 `fig_*.sql` 自带
`fig_id` / `bo` / `carrier` / `schema` / `criterion` / `caption` / `must_not_say`，
`run.py` 扫目录建表。手工维护的清单正是 design A2 说的孤儿图 / 孤儿 SQL 的来源
——两份清单一定会分叉，而分叉那天没有任何东西会响。

🔴 **`schema:` 是承重键，不是元信息。** 执行器按它选连接时注入的会话 schema。
裸表名在错的 schema 下**不会报「没这张表」，而是解析到另一张同名表**——
这正是 `.claude/rules/gold-sql.md` R6 记的那个失败模式，只是方向相反
（R6 是 Gold 会话读 Silver 表名）。FIG-BO4-03 读 `uoip_meta.dq_audit_log`，
是目前唯一一个不在 `gold` 下的图。

### 18.3 图注全部照抄台账，不重写

八份头注的 `caption:` 是台账 §2.2 / §3.2 那两张表里的**逐字原文**。
理由与「不另存第二份清单」同源：图注在两个地方各写一遍，就会有两个版本，
而 slide 上出现的是哪一个没人说得清。台账是常青文档，是权威；
`sql/presentation/` 里的是它的拷贝，改要一起改。

单测 `test_every_figure_is_named_in_the_ledger` 只保证 **fig_id 在台账里出现**，
它拦得住孤儿图，拦不住图注漂移——后者没有便宜的自动化办法，只能靠这条约定。

### 18.4 单测拦的是 sqlfluff 看不见的东西

`make lint` 证明一个图文件是可解析的 Trino。它证明不了下面这些，
所以有 `tests/unit/test_presentation_figures.py`（60 项）：

| 检查 | 拦住的失败 |
|---|---|
| 七个头注键齐全 | 没有图注的图（台账规则 3：图注栏为空的图不许上台） |
| 文件名与 `fig_id` 对应 | 台账里的图 ID 在仓库里搜不到 |
| `fig_id` 出现在台账里 | 孤儿 SQL |
| **每个表都在声明的 schema 的 DDL 目录里** | 🔴 裸表名解析到另一个 schema |
| 无 `$__` / 无 jinja | 载体模板语法混进来，sqlfluff 判 unparsable 后**连带停掉该文件其余全部检查** |
| 一个文件一条语句 | 一份 SQL 对不上一张图 |
| 无 `SELECT *` | AGENTS.md 的禁止项，Gold 层 schema 漂移 |

CTE 别名靠**表名前缀**排除（`dim_` / `fact_` / `silver_` 加两张 meta 表）——
这个仓库里每张表都带层前缀，所以这个过滤是安全的而不是碰运气。

### 18.5 顺带关掉与新开的

- ✅ **L5 关闭**：Q13 欠的 19 行不必再单独跑——`fig_bo2_04_rank_vs_addresses.sql`
  产出的就是那 22 行完整数据，跑一次 `make eda-run ONLY=FIG-BO2-04` 就有了。
- ⚠️ **三个文件名与台账预写的不一致，已按台账改名**（`zone_mean_shift` →
  `zone_rank_spread` 等）。台账先写、SQL 后建时，权威是台账。
- 🔴 **L15 仍未决**，且它是本轮唯一的写操作：改 `fact_recommendation.sql` 的
  `format('%.1f', …)` 要重建 F7。**5b 不会顺手做掉它**——重建 Gold 表不该夹在
  一个「建图」的 PR 里，两者的回滚粒度不同（R4）。请单独决定 H1 前还是 H1 后。

### 18.6 验收：三条命令（宿主机）

⚠️ `.env` 里的 `trino:8080` 是容器视角，宿主机必须带前缀。

```bash
cd /opt/uoip/urban-ops-intelligence-platform && git pull
make lint && uv run --extra dev python -m pytest tests/unit/test_presentation_figures.py -q
```

判据：lint 全绿；单测 **60 passed**。这两条不连线上，先跑。

```bash
cd /opt/uoip/urban-ops-intelligence-platform && \
  TRINO_HOST=localhost TRINO_PORT=8090 make eda-run 2>&1 | tail -160
```

判据（每张图的行数，全部取自已发表的数，**跑之前写死在这里**）：

| 图 | 行数 | 出处 |
|---|---|---|
| FIG-BO2-01 | **22** | Q1 |
| FIG-BO2-02 | **22** | Q2 |
| FIG-BO2-03 | **418** | Q6 |
| FIG-BO2-04 | **22** | Q13 |
| FIG-BO2-05 | **49** | Q8（19 matched / 30 unmatched） |
| FIG-BO4-01 | **≥ 22**，`label_type = 'ward'` 的全部行 | B1 的 crosswalk 548 行里的 ward 部分 |
| FIG-BO4-02 | ~~22~~ → **25**（实测；crosswalk 是几何关系，覆盖全部 25 个分区，含 3 个无排班分区） | B3 |
| FIG-BO4-03 | **≤ 7** 行，命中率 ≈ 99.9%，分母非空 | B5 |

🔴 **任何一行数与上表不符，先看是不是 Gold 重建过**（对一下
`make dq-certify` 最近一行的 `certified_at`），不要直接改 SQL。前五轮已经出现过
三次「数变了但表没坏」（F1 的 908 vs 916 · F8 的 18 个年份 · 命中率的 69.8%）。

```bash
cd /opt/uoip/urban-ops-intelligence-platform && \
  TRINO_HOST=localhost TRINO_PORT=8090 make eda-export 2>&1 | tail -20
```

判据：`frozen 8 figures into var/presentation`，且末行的
`certification at freeze time:` 是 **`certified`**。
🔴 **若它是 `unknown`，那不是「没查出问题」而是「没能查」**（第三批 ADR 0012 的三态），
此时冻结出来的 JSON 不该拿去渲染上台图——先跑一次 `make dq-audit` 再冻。

### 18.7 交接

5a 验收通过后展开 **5b**：四个 BO 的十一张图 + Grafana 三块面板反向进仓
（位移那块必须补 `GROUP BY model_version`，G6 已证实各柱之和 748 = 两个版本混着画）
+ 三个载体搭起来。**5b 的四条纪律各挂一张图**，进仓时逐条对台账 §1：
FIG-BO3-03 的锚点 · FIG-BO1-03 的三条保留 · FIG-BO6-03 的按 profile 分面 ·
FIG-BO8-01 的位移不是优劣。

不阻塞 5b 的三条：L9（改 CLAUDE.md 的 BO-3 待办为完成态）·
L16（改禁语 ② 的理由）· L4（`matched_snowfall_event_id` 是 NULL 还是空串）。

### 18.8 阶段 5a 验收结果（2026-08-31 实测）

三条命令全部跑通：`make lint` 全绿 · `test_presentation_figures.py` **60 passed** ·
`make eda-run` 八张图**全部执行成功**（`make eda-export` 写出
`frozen 8 figures into var/presentation`，而 `export` 会跳过任何 `error` 的图，
所以「八张都冻上了」等价于「八张都跑通了」）· 冻结时的认证状态 **certified**。

三个已核对的行数与 §18.6 一致或已就地更正：

| 图 | 预写 | 实测 | 处置 |
|---|---|---|---|
| FIG-BO4-01 | ≥ 22 | **109** | ✅ |
| FIG-BO4-02 | 22 | **25** | 🔴 预写的数错了，见下 |
| FIG-BO4-03 | ≤ 7，命中率 ≈ 99.9%，分母非空 | **7** 行，99.9%–100.0%，分母 7,266–7,598 | ✅ |

🔴 **FIG-BO4-02 的 25 不是缺陷，是预写判据自己取错了口径。**
`dim_region_crosswalk` 是**几何关系**，覆盖全部 25 个作业分区；22 是**有排班**
的那一批（BO-2 的评分面板口径）。台账 C4-2 本来就同时记了两个数
（22 个分区中位 53.5% / 全部 25 个中位 54.0%），是图注只抄了前一个。
已把 SQL 与台账的图注一并改成 **中位 54.0%、10/25 不到一半**，与这张 SQL 实际
返回的集合对齐。

教训与 §18.3 是同一条的反面：**图注要抄台账，但要抄台账里与这张 SQL 同口径的那个数。**
台账把两个口径都写下来，恰恰是因为它们会被搞混——搞混的代价是图上的数与图下的
文字说的不是同一批分区，而两边都「有出处」。

✅ 剩下五张已补核（2026-08-31）：**22 / 22 / 418 / 22 / 49**，与 §18.6 逐张相同。
**阶段 5a 到此完全收口。** 补核用的命令：

```bash
TRINO_HOST=localhost TRINO_PORT=8090 make eda-run 2>&1 | grep -E "^## FIG|^[0-9]+ rows"
```

判据仍是 §18.6 那张表：22 / 22 / 418 / 22 / 49。

### 18.9 收口时抓到的第二个缺陷：台账替不存在的 SQL 打了勾

台账里有 **12 行**写着「✅ SQL 已进仓」，而 `sql/presentation/` 里只有 **8 个**文件。
多出来的四行是 FIG-BO3-01 / BO3-02 / BO1-01 / BO1-02 —— 它们属于 5b，一行 SQL 都还没写。

🔴 **单测拦不住它，是因为它只走了一个方向。**
`test_every_figure_is_named_in_the_ledger` 查的是「有 SQL 的图在台账里有行」，
即 SQL → 台账。反过来「台账说进仓了的图真的有文件」从来没人查——
而设计 A2 禁的是**两种**孤儿。四行假状态因此穿过了整个 5a。

处置：四行改回 🚧 待建（5b），并补
`test_no_ledger_row_claims_sql_that_is_not_in_the_repo` 钉死反方向。
判据从 60 passed 变成 **61 passed**。

教训与 §18.8 的那条同源：**状态是对仓库的断言，没被执行过的断言比没有状态更糟**——
台账正是别人引用数字的地方。

---

## 19. 阶段 5b —— 余下 11 张图（2026-08-31 落仓，待验收）

### 19.1 交付物

`sql/presentation/` 从 8 个文件涨到 **19 个**，四个 BO 各自补齐：

| BO | 文件 | 载体 |
|---|---|---|
| BO-3 | `fig_bo3_01_event_timeline.sql` · `fig_bo3_02_season_totals.sql` · `fig_bo3_03_plow_lag.sql` | ECharts / Superset / ECharts |
| BO-1 | `fig_bo1_01_label_trend.sql` · `fig_bo1_02_actual_distribution.sql` · `fig_bo1_03_forecast_vs_actual.sql` | Superset / ECharts / ECharts |
| BO-6 | `fig_bo6_01_load_panel.sql` · `fig_bo6_02_factor_spread.sql` · `fig_bo6_03_load_level_by_profile.sql` | ECharts ×2 / Superset |
| BO-8 | `fig_bo8_01_rank_displacement.sql` · `fig_bo8_02_attribution_rules.sql` | ECharts / Superset |

台账 §4.2 / §5.2 / §6.2 / §7.2 的 SQL 一列已全部链上，状态改 ✅。
单测由 61 涨到 **138 passed**（每张图 7 项）。

### 19.2 四条纪律各自钉在哪一行

纪律写在 `must_not_say:` 头注里，跟着 SQL 走——写在文档里的纪律，做图的人不一定读；
写在 SQL 头注里的，`make eda-run` 每次都会连着数一起打出来。

| 纪律 | 落在 | 头注怎么写的 |
|---|---|---|
| **锚点** | FIG-BO3-03 | 「不得把这根轴叫『响应延迟』……锚点换成 `start_date` 会得到一组完全不同的数，所以图上必须写出锚点是哪一天」。SQL 同时返回 `days_from_event_end` 与 `days_from_event_start` 两列——**把两个锚点摆在一起，比在图注里说一句更难被忽略** |
| **三条保留** | FIG-BO1-03 | 留出季只有 7 个事件 · 目标零膨胀 · 对照模型只差 7.8%。并明写「不得只画 v1 与基线」 |
| **按 profile 分面** | FIG-BO6-03 | 不得并轴、不得跨 profile 比较 `load_level`；且**「924 格没有 CRITICAL」是经验事实不是结构保证**（离门槛只有 2.23 分），不得写成「永远不可能」——这同时把 §6.3 保留 1 要求的**禁语 ② 换理由**落到了实处 |
| **位移不是优劣** | FIG-BO8-01 | 标题用「位移」不用「改进」；两句写死：事件内位移和恒为 0，故意训坏的 `nomonth` 同样 188 格上移 |

### 19.3 两处实现上的取舍

1. **FIG-BO1-02 要取一个版本，但不能挑模型。** 面板每个 `model_version` 各一份
   1,298 格，而 `actual_count` 是实测值、逐版本相同；直接聚合会把每一格数两次
   （2,596）。取 `MIN(model_version)` 并在 SQL 里注明**「取一个版本是为了不把同一格
   数两次，不是在挑模型」**——与 F6 那条「服务版本必须显式传」不是同一件事，
   那里选错版本会改变结论，这里不会。
2. **FIG-BO6-02 的加权系数写在 SQL 里。** 0.40/0.30/0.30 是名义权重，图要画的是
   **加权后的实测展幅**。这不违反「业务语义不落库代码」——它落在 `sql/` 里，
   而 `sql/` 本来就是每城一套的载体（城市无关护栏例外条）。

### 19.4 验收：一条命令（宿主机）

```bash
cd /opt/uoip/urban-ops-intelligence-platform && git pull
make lint && uv run --extra dev python -m pytest tests/unit/test_presentation_figures.py -q
```

判据：lint 全绿 · 单测 **138 passed**。

```bash
TRINO_HOST=localhost TRINO_PORT=8090 make eda-run 2>&1 | grep -E "^## FIG|^[0-9]+ rows|could not run"
```

判据（**跑之前写死在这里**，前 8 张仍是 5a 那组）：

| 图 | 行数 | 出处 |
|---|---|---|
| FIG-BO1-01 | **36** = 18 年 × 2 个 `label_type` | C1-1 + C1-2 |
| FIG-BO1-02 | **1**（单行汇总） | — |
| FIG-BO1-03 | **308** = 154 格 × 2 个版本 | C1-5 |
| FIG-BO3-01 | **99** | C3-1 |
| FIG-BO3-02 | **18** | C3-4 |
| FIG-BO3-03 | **19** | C3-7 |
| FIG-BO6-01 | **1,298** | C6-1 |
| FIG-BO6-02 | **3** | — |
| FIG-BO6-03 | **≤ 8**（2 profile × 4 档，partial 没有 CRITICAL 则是 7） | C6-8/9/10 |
| FIG-BO8-01 | **≤ 70**（(版本, 位移) 去重对），且**全表 `SUM(rank_delta) = 0`** | C8-3/C8-4 |
| FIG-BO8-02 | **4**（六条规则里两条 0 命中） | C8-5 |

🔴 **FIG-BO6-03 与 FIG-BO8-01 是唯一两条给区间不给等值的**，理由与 §18.8 同：
它们数的是**取值组合数**，不是实体数。给等值就是把一个会随重建变的数当成判据。

🔴 **FIG-BO3-01 的 `has_no_winter_request` 应有 11 个 true**（C3-9）。不是行数判据，
但它是这张图唯一能被读错的那一列——空心点是 311 覆盖稀薄，不是「那场雪没引发问题」。

### 19.5 5b 还欠一件：L3 反向导回 Grafana 面板

三块线上 Grafana 面板的 SQL **还在 Grafana 里，不在仓库里**，这正是设计 A2 禁的
孤儿图（有图无 SQL）。我拿不到面板 JSON，导出要在节点上做：

```bash
sudo docker exec uoip-grafana grafana cli --help >/dev/null 2>&1; \
  curl -s -u admin:$GRAFANA_ADMIN_PASSWORD http://localhost:3000/api/search?type=dash-db
```

拿到 dashboard uid 后 `curl .../api/dashboards/uid/<uid>`，把每个 panel 的 `rawSql`
贴回来，我按同样的头注格式落进 `sql/presentation/`。

🔴 **位移那块面板导回来时必须补 `GROUP BY model_version`**：G6 证过每个版本各 374 行，
而面板的柱子加起来是 **748**——它现在把两个版本叠在一根柱子上，其中一个是**故意训坏的
对照**。这不是美化，是这块面板当前在说一件不成立的事。

### 19.6 没有并进来的

**L15**（`attribution_text` 把 20.2 cm 渲染成 `2.02E1`）仍未动。修它要重建 F7，
是本工作流的**第一个写操作**，回滚粒度与「重跑一条 SELECT」根本不同（R4：整表重建
四步、非原子）。等 H1 前后的取舍由人定，不顺手并进 5b。
