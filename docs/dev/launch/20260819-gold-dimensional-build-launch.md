# Gold 维表与事实表（L2）上线记录

> **Status**: 执行中（阶段 A 完成） · **Date**: 2026-08-19
> **design**: [20260817-gold-dimensional-build.md](../design/20260817-gold-dimensional-build.md)
> **前一次**: [L1 Silver 全链路跑通](20260817-silver-etl-runnable-launch.md)（**硬前置，已完成**）
>
> 判据、口径、被否决选项一律以 design 篇为准，本篇只记**怎么做、做了什么、
> 实际数字是多少**。空的复选框是还没做，不是做了没记。

---

## 0. 一页纸：这次要做的事

把 13 张 Gold 表从零行填满：9 张维表 + 5 张描述性/直通事实表。

**与 L1 最大的不同：这次全部可回滚。** Gold 整表重建是秒级的，跑错了重跑就是。
本篇**没有一步是不可逆的**——除了阶段 A 的 smoke prefix teardown（那本来就是
设计成可丢弃的命名空间）。

三条最容易翻车的，先说：

1. 🔴 **Trino 没有 `INSERT OVERWRITE` 语法**，非分区表上 `INSERT` 是追加。
   照 `sql/dml/README.md` 现在的字面规则写，第二次跑行数翻倍且不报错。
   整表重建走 **`DROP` → 清 prefix → `CREATE` → `INSERT INTO ... SELECT`** 四步
   （design §4.3 第 1 条，2026-08-19 实测后改写）。
2. 🔴 **`dim_snowfall_event` 必须过滤到探针口径**（`start_date >= 2008-11-01`
   且月份 ∈ 11–4），Silver 有 159 行，Gold 要 99。漏了过滤面板变 20,988，
   **不报错**，一路长进 M1 训练集（design §6.6）。
3. ✅ **O12 已实测（阶段 A，见 §2）**，结论**推翻了原定写法**：Hive 连接器
   不支持 `CREATE OR REPLACE` / `TRUNCATE` / `DELETE`，且外部表 `DROP` 之后
   文件残留——重建出来的表一行 INSERT 没跑就已经能读到上一代数据。
   **清 prefix 是构建路径上的一步，不是收尾。**
4. 🔴 **Trino 全表扫 `silver_service_request` 会超时**（O13，2026-08-19 实测）：
   读真实列跨全部 4,878 个分区 → `Read timed out`；单年 365 分区秒级。
   **墙在分区数，不在数据量。** 每一条读 Silver 的 DML 都必须带
   `open_date_local` 谓词；唯一真需要全历史的 F8 走分片 + staging 一次性 swap。
   四条规则是 binding 的，写 SQL 前先读 [`.claude/rules/gold-sql.md`](../../../.claude/rules/gold-sql.md)。

---

## 1. 前置检查（开跑前逐条填）

- [x] P1 Trino 连得上（2026-08-19：**451**）。
      🔴 **光有版本号不够**——阶段 A 的教训是版本对了但**连接器**不支持，
      能力必须一条条实测，不能从版本号推。
      ⚠️ 宿主机 shell 必须覆盖连接参数：`.env` 里的 `TRINO_HOST=trino:8080`
      是**容器视角**（`18375d0` 起），宿主机走发布端口：
      `TRINO_HOST=localhost TRINO_PORT=8090 make ...`

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

### 阶段 A · O12 实测：外部表怎么重建（无生产影响）✅ 2026-08-19 完成

**先于任何 DML。** 它的结论决定 DML 要不要自己管清理。**结论是要。**
而且它顺带推翻了 design 原定的核心写法——这一步排在第一位的价值就在这里：
推翻发生时，还没有一行 SQL 按旧写法写出来。

```bash
make ddl-create PREFIX=smoke-20260819
make ddl-smoke  PREFIX=smoke-20260819    # 每表 2 行
```

- [x] A1 记下 smoke prefix 下某张 Gold 表的对象清单与 `external_location`
      （`dim_plow_zone`：2 个对象，其中 1 个是 0 B 的目录占位）

```bash
aws --endpoint-url "$S3_ENDPOINT_URL" s3 ls --recursive \
  "s3://$S3_BUCKET_NAME/smoke-20260819/gold/dim_plow_zone/"
```

- [x] A2 四种重建写法逐条实测（Trino 451，Hive 连接器，外部表）：

| 写法 | 结果 |
|---|---|
| `CREATE OR REPLACE TABLE ... AS SELECT` | 🔴 `NOT_SUPPORTED: This connector does not support replacing tables` |
| `TRUNCATE TABLE` | 🔴 `NOT_SUPPORTED: This connector does not support truncating tables` |
| `DELETE FROM t`（无 `WHERE`） | 🔴 `NOT_SUPPORTED: Cannot delete from non-managed Hive table` |
| `DROP` + 按 `sql/ddl` `CREATE` + `INSERT` | ✅ 唯一可行 |

- [x] A3 🔴 **孤儿文件确实存在**：`DROP TABLE` 之后 2 个对象仍在；
      用同一份 DDL 重建，**一行 `INSERT` 都还没跑**就已经 `COUNT(*) = 2`。
      → 执行器必须在 `CREATE` 之前调 `apply_ddl._purge_storage`（复用，不重写）
- [x] A4 `ALTER TABLE ... RENAME TO` **支持**，但改名后 `external_location`
      仍指向 `.../dim_plow_zone__staging`。**换不回原子性**——那是把表的物理
      位置搬了家。staging + swap 这条路作废
- [x] A5 `make ddl-teardown PREFIX=smoke-20260819` —— 两个 schema 整个 drop（有残留表就会失败），**purged 54 object(s)**，`__staging` 路径一并收走

**结论**：整表重建 = `DROP` → `_purge_storage` → `CREATE`（`sql/ddl`）→
`INSERT INTO (<显式列>) SELECT`。**非原子**，有一个以秒计的窗口表不存在或为空；
接受（手动触发、秒级、唯一读者 Superset）。因为 `INSERT` 是追加，
**精确行数门禁从「复核」升级为承重件**。design §4.3 / §10 / O12 与
`.claude/rules/gold-sql.md` R4 已同批改完。

### 阶段 B · 代码落地（无生产影响，可随时回退）

- [x] B1 `config/seeds/` 四份 CSV：`winter_category.csv`（7）·
      `service_type_keywords.csv` · `channel.csv`（15）· `recommendation_rules.csv`
- [x] B2 `scripts/gold/build_gold.py` + `scripts/gold/gates.py` + `scripts/gold/__init__.py`（design §7）
- [x] B3 `tests/unit/test_build_gold.py`（33 项）（design §9），含
      **拓扑序自检**与 `sql/dml/*.sql` 的静态检查（禁 `SELECT *`、必须
      **必须 `INSERT INTO` 且不含 `CREATE TABLE`**（schema 只有 `sql/ddl/` 一份）、
      必须带三列血缘、**凡读 `silver_service_request`
      者必须出现 `open_date_local` 谓词**——R1 靠这条自动化，不靠自觉）
- [x] B4 Makefile 加 `gold-build`
- [x] B5 `dags/dag_gold_build.py`（`schedule=None`，`max_active_runs=1`，
      **不写 `on_failure_callback`**）
- [x] B6 `make lint` 干净 · `make test-unit-offline` = **828 passed, 2 skipped**
- [~] B7 `DRY_RUN=1 make gold-build` —— 种子段已可离线打印并人眼过（`--dry-run`
      **不需要能连 Trino**，这是有意的：审 SQL 的机器通常连不上）。
      维表/事实表段要等 `sql/dml/*.sql` 写完才能打印

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

      多关键词命中清单（O4）：条数 **13**，裁决结论见 §4.4——**最具体优先**，
      不是 design §6.2 写的 SNOW 优先
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
- [x] D7 冬季子集行数 = **256,077**，占 12,477,414 的 **2.05%**（2026-08-19 实测，
      §4.2 解释了为什么 design 写的 275,282 / 1.5% 是两套口径拼出来的）。
      ⚠️ 若实测 ~10.4%，是关键词松匹配（`%ICE%` 命中 Serv**ice**）
- [ ] D8 🔴 F8 开跑**之前**先重算行数期望（O14）：`≈1.6 M` 是「6,600 天 ×
      252 标签」的稠密假设，而 `ward_raw` / `neighbourhood_raw` 的 NULL 率实测
      **77.22%**，只有约 23% 的行带得动标签。先跑一年样本推全量，把期望写进
      本篇再建表——**不改 schema，只改门禁数字**。重算后的期望：____
- [ ] D9 `fact_winter_request_daily_by_label`（F8）——**最后跑**，且是本次唯一
      一张**不能一条语句建完**的表（O13/R2/R4）：
      ① 按日历年分片，年份只出现在每片的 `WHERE`，**不进列、不进分区键、
      不建 `dim_year`**；② 各片 `INSERT` 进 staging 表；③ 全部成功后一次性
      swap。分片建立在四步序列之上：**drop / purge / create 各做一次**，
      然后每片一条 `INSERT` 进同一张表，最后一片成功后才跑门禁。
      **绝不逐片 drop-重建**——那会留下一张只含最后一片、看着合理只是小了
      很多的表，且没有任何东西会报错。
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
| `fact_winter_request_daily_by_label` | **141,377**（O14 实测） | | |

三个探针数字：面板 **1,298** ____ · 非零 **916** ____ · 空间命中
**134,123 / 134,258** ____

---

## 4. 与设计的偏差

（执行中出现的，逐条记。design 篇不追记实况，实况只在这里。）

### 4.1 🔴 阶段 A：`CREATE OR REPLACE TABLE` 在 Hive 连接器上不存在

design §4.3 第 1 条的定案**建立在一个没核到底的前提上**：Trino 版本 451 ≥ 438
是对的，但那条语法在 **Hive 连接器**上没实现。2026-08-19 实测四条路：

| 语句 | 结果 |
|---|---|
| `CREATE OR REPLACE TABLE` | 🔴 `NOT_SUPPORTED: This connector does not support replacing tables` |
| `TRUNCATE TABLE` | 🔴 `NOT_SUPPORTED: This connector does not support truncating tables` |
| `DELETE FROM t`（无 `WHERE`） | 🔴 `NOT_SUPPORTED: Cannot delete from non-managed Hive table` |
| `DROP` + `CREATE` + `INSERT` | ✅ 唯一可行 |

**`DROP` 不删外部表的文件**：drop 之后 2 个对象仍在，按同一份 DDL 重建出来的表
**立刻读到 `COUNT(*) = 2`**，一次 `INSERT` 都还没跑。所以重建是**四步**，
清 prefix 是第二步且不可省——省了就是新旧两代数据并集，且没有任何东西会报错。

`ALTER TABLE ... RENAME TO` 支持，但**换不回原子性**：改名后
`external_location` 仍指向 `__staging` 路径，等于把表的物理位置搬了家。
故整表重建**不是原子的**，接受而非解决（手动触发、秒级、唯一读者是 Superset）。

规则已改：[`.claude/rules/gold-sql.md`](../../../.claude/rules/gold-sql.md) R4。

### 4.2 🔴 design §6.11 / D7 的冬季子集门禁是错的，不是数据不对

design 写「冬季子集 ≈ **275,282** 行，占 12,474,313 的 ≈**1.5%**」。
这句话**把上游口径的分子配上了 Silver 口径的分母**，两个数不是一套里的：

| 口径 | 分子 | 分母 | 占比 |
|---|---|---|---|
| 上游整表（`winnipeg-data-sources.md` §3.4） | 275,243 | 18,346,621 | 1.50% |
| **Silver 实测（2026-08-19）** | **256,077** | **12,477,414** | **2.05%** |

差的 19,166 行是 Bronze **有意不采**的部分：2016-08 之前只采冬季，而冬季类
工单一年四季都有人报（`Frozen Pipe FAQ` / `Accessibility Snow Clearing
Application` 之类）。两个数都对，只是不能混着用。
**D7 的门禁数字改为 256,077 / 2.05%**，`1.5%` 那条留作上游口径的注脚。

⚠️ 另：全表行数已是 **12,477,414**（日增量在跑），不再是 L1 落地时的 12,474,313。

### 4.3 O14 结案：F8 是 141,377 行，不是 ≈1.6 M

按 `(日期, 标签)` 实际去重：ward 34,499 + neighbourhood 106,878 = **141,377**。
稠密假设（6,600 天 × 252 标签）高估了 **11 倍**。
冬季子集里 ward / neighbourhood 的填充率是 **80.19% / 80.17%**——注意这与
design 记的全表 77.22% NULL 率不矛盾：**冬季工单带坐标的比例远高于全表**
（契约 `fill_rate_winter_subset: 0.801` 早就写着，正是这个数）。

⚠️ **行数小不等于不用分片**：R2 的分片是为了**扫描**，不是为了输出。F8 仍要
跨 4,878 个分区读 Silver，照样撞 O13 的墙。本次发现扫描实测 ~85 秒/年
（7 条查询），单条查询约 12 秒，故 F8 分 19 片预计 **5 分钟**量级。

### 4.4 🔴 O4 定案：改成「最具体优先」，design §6.2 的顺序被推翻

实测 13 个 `type` 多命中，**全部含 SNOW**。按 design 写的
`SNOW > FROZEN > PLOW > SANDING > WINDROW > ICE_CONTROL > PLOUGH`，这 13 个
一律归 SNOW，后果是：

- `WINDROW` 全库只有 **1 个** distinct type，且它是多命中的 → 拿到 **0 个**；
- `ICE_CONTROL` 全库只有 **3 个**，**三个全在**多命中清单里 → 也拿到 **0 个**。

六个生效类里两个被仲裁规则吃空。面板 13,068 格照样建得出来（骨架是笛卡尔积），
但那两类**全零**——看起来像数据事实，实际是规则后果。

✅ **2026-08-19 签字：顺序改为最具体优先**
`WINDROW > ICE_CONTROL > PLOW > PLOUGH > SANDING > FROZEN > SNOW`。
SNOW 退为兜底类——`Snow Removal ...` 是几乎所有冬季工单的通用前缀，让通用前缀
优先等于让它吃掉专有语义。13 个多命中因此落到：窗口垄堆问询 → WINDROW，
铲雪车损坏 → PLOW，占道妨碍除冰作业 → ICE_CONTROL。

⚠️ 顺序的**唯一载体是 `config/seeds/winter_category.csv` 的行序**（schema 冻结，
表里没有 priority 列）。单测把期望顺序显式钉死：改行序会挂测试，不会静默换语义。

### 4.5 `_vof` 不是优先级标记，design §6.2 把它列错了

design 写「从 `type` 串里解析 `Pr 2` / `Priority 2` / `P2` / `_vof` 等后缀变体」。
实测 **48 个** winter type 带 `_VOF`，但 VOF 是**渠道**（`dim_channel` 里
`Self Service + Mobile + SMS In → VOF` 那个 VOF），与 P1/P2/P3 无关。
`config/seeds/service_type_keywords.csv` **只收 9 条真优先级模式**，
`_vof` 不在其中——收了就是给 48 个 type 编出一个不存在的优先级。

实测变体分布：`PRIORITY n` 49 个 type · 裸 `Pn` 46 个 · `PR n` 6 个 ·
**无可解析标记 55 个**（`priority_weight` 留 NULL，DDL 允许）。

### D-1 · 整表重建的写法被实测推翻（阶段 A，2026-08-19）

design §4.3 原定 `CREATE OR REPLACE TABLE ... AS SELECT`，依据是「该语法需
Trino ≥ 438，计算节点实测 451」。**版本号没错，错在只核了引擎版本没核连接器**
——`CREATE OR REPLACE` 在 Hive 连接器上根本没实现。同一轮实测里 `TRUNCATE`
与 `DELETE` 也不支持，`DROP` + `CREATE` + `INSERT` 是唯一可行的。

三处已同批改完（不留旧说法）：design §4.3/§4.5/§6/§7/§9/§10/§12 ·
`.claude/rules/gold-sql.md` R4 · 本篇 §0/§1/§2。

**可复用的教训**：「某版本支持某语法」对 Trino 是不充分的判断——能力由
**连接器**决定。凡是没在这套栈上跑过的语句，都按未验证对待。

### D-2 · 宿主机 shell 连 Trino 要覆盖 `TRINO_HOST`（阶段 A）

`make ddl-create` 直接报 `Failed to resolve 'trino'`，五层异常堆栈底下才是
真因。`.env` 的 `TRINO_HOST=trino` / `8080` 是**容器视角**（`18375d0` 之后），
宿主机要 `TRINO_HOST=localhost TRINO_PORT=8090`。`.env.example` 第 44 行写了
这个覆盖写法，但 `make ddl-*` 不带。
→ `build_gold.py` 连接失败时必须**直接打印这条覆盖命令**，不要甩堆栈。

## 5. 遗留项

- O9：`dim_snowfall_event` 的 99 vs 159 口径，L3 M1 训练前复议
- ✅ O12 已关闭（阶段 A）。孤儿文件确实存在，`_purge_storage` 已写进
  design §4.3 的四步序列；执行器实现时**这一步不能只写在注释里**
- O13：Trino 的全表扫墙是平台级共享服务的属性，**本仓库不调它的连接参数**
  （ADR 0006 §9）。L3 的评分链按事件窗口读 Silver，天然满足 R1，但每加一条
  新 DML 都要重核一次
- O14：F8 的行数期望在本次重算后写进本篇 §3，design 篇的 `≈1.6 M` 不追改

## 6. 上线后需要观察的

Gold 是手动触发，没有「连续观察 3 天」这回事。真正要盯的是**下一次 Silver
增量跑完之后**：`dim_snowfall_event` 与 F1 的行数会不会跟着变。
按当前口径（2008-11 起 + 冬季月份）不该变——除非真的下了一场新的雪。
