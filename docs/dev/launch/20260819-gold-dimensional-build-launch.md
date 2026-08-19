# Gold 维表与事实表（L2）上线记录

> **Status**: 待执行 · **Date**: 2026-08-19
> **design**: [20260817-gold-dimensional-build.md](../design/20260817-gold-dimensional-build.md)
> **前一次**: [L1 Silver 全链路跑通](20260817-silver-etl-runnable-launch.md)（**硬前置，已完成**）
>
> 判据、口径、被否决选项一律以 design 篇为准，本篇只记**怎么做、做了什么、
> 实际数字是多少**。空的复选框是还没做，不是做了没记。

---

## 0. 一页纸：这次要做的事

把 13 张 Gold 表从零行填满：9 张维表 + 5 张描述性/直通事实表。

**与 L1 最大的不同：这次全部可回滚。** Gold 是 `CREATE OR REPLACE TABLE ... AS
SELECT`，秒级，跑错了重跑就是。本篇**没有一步是不可逆的**——除了阶段 B 的
smoke prefix teardown（那本来就是设计成可丢弃的命名空间）。

三条最容易翻车的，先说：

1. 🔴 **Trino 没有 `INSERT OVERWRITE` 语法**，非分区表上 `INSERT` 是追加。
   照 `sql/dml/README.md` 现在的字面规则写，第二次跑行数翻倍且不报错。
   全部用 `CREATE OR REPLACE TABLE ... AS SELECT`（design §4.2/§4.3）。
2. 🔴 **`dim_snowfall_event` 必须过滤到探针口径**（`start_date >= 2008-11-01`
   且月份 ∈ 11–4），Silver 有 159 行，Gold 要 99。漏了过滤面板变 20,988，
   **不报错**，一路长进 M1 训练集（design §6.6）。
3. ⚠️ **`CREATE OR REPLACE` 在带 `external_location` 的外部表上行为未实测**——
   旧对象是覆盖还是残留成孤儿文件？残留的话下次全表扫会读到两代数据。
   **阶段 A 在 smoke prefix 上先试，这是本次的第一个执行步骤**（O12）。
4. 🔴 **Trino 全表扫 `silver_service_request` 会超时**（O13，2026-08-19 实测）：
   读真实列跨全部 4,878 个分区 → `Read timed out`；单年 365 分区秒级。
   **墙在分区数，不在数据量。** 每一条读 Silver 的 DML 都必须带
   `open_date_local` 谓词；唯一真需要全历史的 F8 走分片 + staging 一次性 swap。
   四条规则是 binding 的，写 SQL 前先读 [`.claude/rules/gold-sql.md`](../../../.claude/rules/gold-sql.md)。

---

## 1. 前置检查（开跑前逐条填）

- [ ] P1 Trino 连得上，版本 ≥ 438（`CREATE OR REPLACE TABLE` 的门槛）

```bash
uv run python -c "
from scripts.ddl.apply_ddl import load_trino_settings, _connect
s = load_trino_settings(); print(s)
with _connect(s, 'uoip_gold') as c:
    cur = c.cursor(); cur.execute('SELECT version()'); print(cur.fetchall())
"
```

结果：`version() = ____`（2026-08-19 记录为 451）

- [ ] P2 25 张表都在，Gold 17 张确认为**零行**（不是「以为是零行」）

```sql
SHOW TABLES FROM hive.uoip_gold;   -- 17
SHOW TABLES FROM hive.uoip_silver; -- 8
```

- [ ] P3 8 张 Silver 表的行数，抄进下表。**这是 Gold 每一个门禁数字的分母**

| 表 | 期望 | 实测 |
|---|---|---|
| `silver_service_request` | 12,474,313 / 4,878 分区 | |
| `silver_snowfall_event` | 159（探针口径过滤后 99） | |
| `silver_plow_shift` | 418 | |
| `silver_parking_ban` | 49 | |
| `silver_plow_zone_boundary` | 82 行 → 25 个 `plow_zone` | |
| `silver_snow_clearing_address` | 25，且 `COUNT(DISTINCT snapshot_date) = 1` | |
| `silver_weather_archive` | — | |
| `silver_weather_forecast` | （无 DAG，可能为空，正常） | |

- [ ] P4 O7 的前置核验手工先跑一次：`silver_service_request` 的分区数
      对得上 Bronze 实际覆盖天数（**不是日历天数**——2008–2016 只有冬季有数据）
- [x] P5 三处签字齐了（2026-08-19）：
      **O10** = `label_id` 存 casefold 值，显示形态在 Superset 侧处理；
      **O11** = C6 改分层表述，三处同步改（执行在 E3）；
      **O9** 记录在案（本次按 99 执行，L3 M1 训练前复议）
- [ ] P6 计算节点内存：Spark 侧没有在跑的作业（7 GB 与 Trino 共用）

---

## 2. 执行清单

### 阶段 A · O12 实测：`CREATE OR REPLACE` + 外部表（无生产影响）

**先于任何 DML。** 它的结论决定 DML 要不要自己管清理。

```bash
make ddl-create PREFIX=smoke-20260819
make ddl-smoke  PREFIX=smoke-20260819    # 每表 2 行
```

- [ ] A1 记下 smoke prefix 下某张 Gold 表的对象清单与 `external_location`

```bash
aws --endpoint-url "$S3_ENDPOINT_URL" s3 ls --recursive \
  "s3://$S3_BUCKET_NAME/smoke-20260819/gold/dim_plow_zone/"
```

- [ ] A2 在同一张表上跑一次 `CREATE OR REPLACE TABLE ... AS SELECT`（造 3 行假数据），
      然后回答三个问题：

| 问题 | 结果 |
|---|---|
| `SHOW CREATE TABLE` 里 `external_location` 还在吗？指向原路径吗？ | |
| 旧的 2 行对象文件还在吗（孤儿）？ | |
| `SELECT COUNT(*)` 读到 3 还是 5？ | |

🔴 **读到 5 就是孤儿文件问题**：说明 `CREATE OR REPLACE` 只换了元数据、
没清存储，DML 必须在建表前显式清 prefix（执行器加一步 `_purge_storage`，
`apply_ddl.py` 已有这个函数，复用不重写）。读到 3 则本条关闭。

- [ ] A3 再跑一次同样的语句，行数仍是 3（幂等）
- [ ] A4 `make ddl-teardown PREFIX=smoke-20260819`，确认对象清空

**结论**（A2 之后填）：`____________`

### 阶段 B · 代码落地（无生产影响，可随时回退）

- [ ] B1 `config/seeds/` 四份 CSV：`winter_category.csv`（7）·
      `service_type_keywords.csv` · `channel.csv`（15）· `recommendation_rules.csv`
- [ ] B2 `scripts/gold/build_gold.py` + `scripts/gold/__init__.py`（design §7）
- [ ] B3 `tests/unit/test_build_gold.py`（design §9），含
      **拓扑序自检**与 `sql/dml/*.sql` 的静态检查（禁 `SELECT *`、必须
      `CREATE OR REPLACE TABLE`、必须带三列血缘、**凡读 `silver_service_request`
      者必须出现 `open_date_local` 谓词**——R1 靠这条自动化，不靠自觉）
- [ ] B4 Makefile 加 `gold-build`
- [ ] B5 `dags/dag_gold_build.py`（`schedule=None`，`max_active_runs=1`，
      **不写 `on_failure_callback`**）
- [ ] B6 `make lint` 干净 · `make test-unit-offline` 全绿（记录 passed 数：____）
- [ ] B7 `DRY_RUN=1 make gold-build` 打印全部 SQL，人眼过一遍再连生产

### 阶段 C · 种子与维表（9 张）

顺序照 design §5 的依赖图，**执行器自己排，不要手工逐条跑**。

```bash
make gold-build ONLY=seeds        # dim_winter_category / dim_channel / dim_recommendation_rules
make gold-build ONLY=dims
```

- [ ] C1 `dim_winter_category` = 7（`is_effective=true` → 6）
- [ ] C2 🔴 `dim_service_type`：首次构建**预计报出一大批未覆盖的 `type` 值**（C13）。
      这是设计行为不是故障——把清单存下来，人工补字典，再跑。
      **anti-join 必须 = 0 才算过**，别用「先让它跑通」的心态放宽这条。

      多关键词命中清单（O4）：条数 ____，人工裁决结论记这里：`________`
- [ ] C3 `dim_channel` = 15，且 Silver 的 `channel_raw` 取值集 ⊆ 本表（anti-join = 0）
- [ ] C4 `dim_plow_zone` = 25，`geometry_repaired=true` = **8**，
      `has_plow_schedule=false` = **3**（且这 3 个是**派生出来**的，
      不是 SQL 里写死的名字），`address_count IS NULL` = 0
- [ ] C5 `dim_admin_label` = 252（ward 15 + neighbourhood **237**）。
      🔴 242 → 237 的折叠必须发生在**入表之前**。若实测是 242，
      说明 casefold 没生效，McMillan 被拆成了两个报告单元
- [ ] C6 🔴 `dim_snowfall_event` = **99**（不是 159），`is_scheduling_era=true` = **59**。
      过滤条件在 SQL 里显式可见
- [ ] C7 `dim_plow_event` = 19，`is_aligned` 17/2，**扇出守卫**通过
      （`COUNT(DISTINCT matched_snowfall_event_id)` = 非空行数）
- [ ] C8 `dim_region_crosswalk`：`SUM(weight)` 按 `(plow_zone, label_type)`
      全部 ≈ 1.0；每组 `is_dominant` 恰好 1 行；`calibration_window` 列里
      是**近期窗口**（O2），不是十年。并列组条数：____
- [ ] C9 `dim_recommendation_rules` 装载，`is_fallback=true` ≥ 1
- [ ] C10 **连跑两次** `make gold-build ONLY=dims`，9 张表行数完全相同
      （这条查的是 §0 的第 1 个坑）

### 阶段 D · 事实表（4 张 + F8）

```bash
make gold-build ONLY=facts
```

- [ ] D1 `fact_plow_shift` = **418**，FK 到 `dim_plow_event` anti-join = 0
- [ ] D2 `fact_parking_ban` = **49**，`matched_plow_event_id IS NULL` = **30**
      （语义，不是缺数据；确认 SQL 里是 LEFT JOIN）
- [ ] D3 `fact_event_zone_rank` = **418**，`rank_factor = 0` 的行数 = **0**，
      值域 ⊆ [0.2, 1]
- [ ] D4 🔴 `fact_service_request_zone_event` = **13,068**，
      `COUNT(DISTINCT (snowfall_event_id, plow_zone))` = **2,178**。
      事件来源是 `JOIN dim_snowfall_event`，**不是重新查 Silver**（口径单一来源）
- [ ] D5 排班期子集 **1,298** 格，非零 **916** 格（70.57%）
- [ ] D6 空间命中率按探针口径精确复现：**134,123 / 134,258 = 99.9%**
      （分母 = 排班期 × 冬季 × 带几何）。对不上**信探针**
      `scripts.analysis.request_point_in_zone`
- [ ] D7 冬季子集行数 ≈ **275,282**，占 12,474,313 的 ≈**1.5%**。
      ⚠️ 若实测 ~10.4%，是关键词松匹配（`%ICE%` 命中 Serv**ice**）
- [ ] D8 🔴 F8 开跑**之前**先重算行数期望（O14）：`≈1.6 M` 是「6,600 天 ×
      252 标签」的稠密假设，而 `ward_raw` / `neighbourhood_raw` 的 NULL 率实测
      **77.22%**，只有约 23% 的行带得动标签。先跑一年样本推全量，把期望写进
      本篇再建表——**不改 schema，只改门禁数字**。重算后的期望：____
- [ ] D9 `fact_winter_request_daily_by_label`（F8）——**最后跑**，且是本次唯一
      一张**不能一条语句建完**的表（O13/R2/R4）：
      ① 按日历年分片，年份只出现在每片的 `WHERE`，**不进列、不进分区键、
      不建 `dim_year`**；② 各片 `INSERT` 进 staging 表；③ 全部成功后一次性
      swap。**绝不逐片 `CREATE OR REPLACE`**——那会留下一张只含最后一片、
      看着合理只是小了很多的表，且没有任何东西会报错。
      文件头注按 R2 写 `-- chunked_by:` 与 `-- combine:` 两行。
      分片数 ____ · 总耗时 ____ 秒
- [ ] D10 连跑两次 `ONLY=facts`，行数完全相同

### 阶段 E · 收口

- [ ] E1 DQ 基线：13 张表逐张记行数 · 各列空值率 · 构建耗时（§3 表）。
      **L3 的 E6 只做汇总，基线在这里产生**
- [ ] E2 `dag_gold_build` 在 Airflow UI 里能 import、能手动触发跑通一次
      （L1 的教训：`test_dag_imports` 本地会 skip，**只 `py_compile` 过不算验证过**）
- [ ] E3 O11 的 C6 修订文本同步三处：`CLAUDE.md` · 伞篇
      `20260817-etl-implementation.md` · `sql/dml/README.md`。
      🔴 三处必须同一批改完——留一处旧文本，下一个人就按旧的写 SQL
- [ ] E4 `CHANGELOG.md` 记一条（`[Unreleased]`）
- [ ] E5 分支 push + PR
- [ ] E6 O8 单独开一个 PR：两个 Bronze 探针搬进 `dag_audit_bronze`。
      **不合进本次**——Bronze 审计是日频的，Gold 构建不是

---

## 3. 门禁的实际结果

（执行时填。空表格比漏填的表格诚实。）

| 表 | 期望 | 实测 | 耗时 |
|---|---|---|---|
| `dim_winter_category` | 7 | | |
| `dim_service_type` | ≤ 3,563，anti-join 0 | | |
| `dim_channel` | 15 | | |
| `dim_plow_zone` | 25 / 8 / 3 | | |
| `dim_admin_label` | 252 (15+237) | | |
| `dim_snowfall_event` | 99 / 59 | | |
| `dim_plow_event` | 19 / 17 / 2 | | |
| `dim_region_crosswalk` | Σw≈1 | | |
| `dim_recommendation_rules` | — | | |
| `fact_plow_shift` | 418 | | |
| `fact_parking_ban` | 49 (30 NULL) | | |
| `fact_event_zone_rank` | 418 (rank=0 → 0) | | |
| `fact_service_request_zone_event` | 13,068 / 2,178 | | |
| `fact_winter_request_daily_by_label` | ⚠️ O14 重算后填 | | |

三个探针数字：面板 **1,298** ____ · 非零 **916** ____ · 空间命中
**134,123 / 134,258** ____

---

## 4. 与设计的偏差

（执行中出现的，逐条记。design 篇不追记实况，实况只在这里。）

## 5. 遗留项

- O9：`dim_snowfall_event` 的 99 vs 159 口径，L3 M1 训练前复议
- O12 若实测出孤儿文件问题，执行器的清理步骤要写进 design §7
- O13：Trino 的全表扫墙是平台级共享服务的属性，**本仓库不调它的连接参数**
  （ADR 0006 §9）。L3 的评分链按事件窗口读 Silver，天然满足 R1，但每加一条
  新 DML 都要重核一次
- O14：F8 的行数期望在本次重算后写进本篇 §3，design 篇的 `≈1.6 M` 不追改

## 6. 上线后需要观察的

Gold 是手动触发，没有「连续观察 3 天」这回事。真正要盯的是**下一次 Silver
增量跑完之后**：`dim_snowfall_event` 与 F1 的行数会不会跟着变。
按当前口径（2008-11 起 + 冬季月份）不该变——除非真的下了一场新的雪。
