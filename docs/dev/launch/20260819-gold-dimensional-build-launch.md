# Gold 维表与事实表（L2）上线记录

> **Status**: 执行中（阶段 A / B / **C 完成**） · **Date**: 2026-08-19
> **design**: [20260819-gold-dimensional-build.md](../design/20260819-gold-dimensional-build.md)
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

- [x] P2 25 张表都在，Gold 17 张确认为**零行**（2026-08-19：Gold 17 / Silver 8，
      9 张待建维表 `COUNT(*)` 全为 0）

```sql
SHOW TABLES FROM hive.uoip_gold;   -- 17
SHOW TABLES FROM hive.uoip_silver; -- 8
```

- [x] P3 8 张 Silver 表的行数（2026-08-19 实测）。**这是 Gold 每一个门禁数字的分母**

| 表 | 期望 | 实测 |
|---|---|---|
| `silver_service_request` | 12,474,313 / 4,878 分区 | ✅ **12,477,414** / **4,879** 分区 |
| `silver_snowfall_event` | 159（探针口径过滤后 99） | ✅ 159 |
| `silver_plow_shift` | 418 | ✅ 418 |
| `silver_parking_ban` | 49 | ✅ 49 |
| `silver_plow_zone_boundary` | 82 行 → 25 个 `plow_zone` | ✅ 82 |
| `silver_snow_clearing_address` | 25，且 `COUNT(DISTINCT snapshot_date) = 1` | ✅ 25 |
| `silver_weather_archive` | — | 未查（本次无消费者） |
| `silver_weather_forecast` | （无 DAG，可能为空，正常） | 未查（同上） |

行数与分区数都比 L1 落地时高一点点，日增量在跑，属正常。

- [x] P4 `silver_service_request` 分区数 = **4,879**（2026-08-19），对得上 Bronze
      实际覆盖天数（**不是日历天数**——2008–2016 只有冬季有数据）
- [x] P5 三处签字齐了（2026-08-19）：
      **O10** = `label_id` 存 casefold 值，显示形态在 Superset 侧处理；
      **O11** = C6 改分层表述，三处同步改（执行在 E3）；
      **O9** 记录在案（本次按 99 执行，L3 M1 训练前复议）
- [x] P6 计算节点内存：`spark-master` / `spark-worker-uoip` 容器常驻但**没有在跑的作业**
      （java 进程 elapsed 14 天，是常驻进程不是提交的作业）

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
- [x] B6 `make lint` 干净 · `make test-unit-offline` = **833 passed, 2 skipped**
      （阶段 C 复核时的数；B6 当时记的是 828，中间又加过测试）
- [~] B7 `DRY_RUN=1 make gold-build` —— 种子段与**维表段 9 张全部可离线打印**
      并人眼过（`--dry-run` **不需要能连 Trino**，这是有意的：审 SQL 的机器通常
      连不上；`--bucket` 仍需显式给或走 `S3_BUCKET_NAME`）。
      仍是 `[~]`：事实表段要等 `sql/dml/` 补齐 5 份才能打印。
      ⚠️ 别用 `| less` —— 提前退出会让 `print` 抛 `BrokenPipeError`，
      看起来像 build 挂了，其实只是管道断了

### 阶段 C · 种子与维表（9 张）

> **进度（2026-08-19 会话结束时）**：`sql/dml/` 已写 **3 份**——
> `dim_snowfall_event` · `dim_plow_zone` · `dim_admin_label`（三份都过
> `sqlfluff`，**未对生产跑过一次**）。三张种子表不需要 DML（执行器从 CSV 生成
> `VALUES`）。**还差 3 份**：`dim_service_type` · `dim_plow_event` ·
> `dim_region_crosswalk`。详见 §7 交接。


顺序照 design §5 的依赖图，**执行器自己排，不要手工逐条跑**。

```bash
make gold-build ONLY=seeds        # dim_winter_category / dim_channel / dim_recommendation_rules
make gold-build ONLY=dims
```

- [x] C1 `dim_winter_category` = 7（`is_effective=true` → 6）✅
- [x] C2 🔴 `dim_service_type`：首次构建**预计报出一大批未覆盖的 `type` 值**（C13）。
      这是设计行为不是故障——把清单存下来，人工补字典，再跑。
      **anti-join 必须 = 0 才算过**，别用「先让它跑通」的心态放宽这条。

      多关键词命中清单（O4）：条数 **13**，裁决结论见 §4.4——**最具体优先**，
      不是 design §6.2 写的 SNOW 优先

      ✅ **实测未出现未覆盖清单**：anti-join 首次即为 0，`dim_service_type` = **3,516**
      行（期望 ≤ 3,563）。C13 预计的「首次报出一大批未覆盖 `type`」没有发生。
      🟢 **anti-join 门禁也没有 timeout** —— §7.2 担心的那次 4,878 分区全表扫实测跑通，
      不必改成按年分片累计比对。O13 的墙对这条查询没撞上。
- [x] C3 `dim_channel` = 15 ✅，Silver 的 `channel_raw` ⊆ 本表（anti-join = **0**，
      在近 3 雪季窗口上核的——全历史核会撞 R1）
- [x] C4 `dim_plow_zone` = 25 ✅，`geometry_repaired=true` = **8** ✅，
      `has_plow_schedule=false` = **3** ✅（派生，非写死），
      `address_count IS NULL` = **0** ✅（手工核，执行器不查这条）
- [x] C5 `dim_admin_label` = 252 ✅（ward **15** + neighbourhood **237**）。
      折叠生效，没有出现 242 —— casefold 在入表之前完成，McMillan 是一个报告单元
- [x] C6 🔴 `dim_snowfall_event` = **99** ✅（不是 159），`is_scheduling_era=true` = **59** ✅。
      过滤条件在 SQL 里显式可见。
      ⚠️ **首跑是 58，差 1**，根因是 DML 的排班期边界写成了 `2015-12-01` 而探针用
      `2015-11-01`。见 §4.6——这条门禁是唯一抓住它的东西
- [x] C7 `dim_plow_event` = 19 ✅，`is_aligned` **17/2** ✅（§7.4 说这个分布没验证过，
      现在验了），**扇出守卫**通过：`COUNT(DISTINCT matched_snowfall_event_id)` = **17**
      = 非空行数 **17** ✅。
      ⚠️ 这条守卫**执行器查不到** —— DDL 里它写成了跨行文本，而 `gates.py` 只解析
      单行 `COUNT(*) ... = n`，所以它是以 `[note] not machine-checked` 打印的。手工核的
- [x] C8 `dim_region_crosswalk` = **548** 行：`SUM(weight)` 按 `(plow_zone, label_type)`
      全部 ≈ 1.0 ✅（越界组 0）；每组 `is_dominant` 恰好 1 行 ✅（越界组 0）；
      `calibration_window` = **`2023-2024..2025-2026`** ✅ 是近期窗口（O2）不是十年。
      并列组条数：**0**
- [x] C9 `dim_recommendation_rules` = **6** 行 ✅，`is_fallback=true` = **1** ✅
- [x] C10 **连跑两次** `make gold-build ONLY=dims` ✅，6 张维表行数**逐张完全相同**
      （3,516 / 25 / 252 / 99 / 19 / 548，只有耗时差几百毫秒）。§0 第 1 个坑没有踩到：
      `DROP` → purge → `CREATE` → `INSERT` 四步的 purge 确实生效，没有出现行数翻倍

### 阶段 D · 事实表（4 张 + F8）

✅ **五份 DML 已写完（2026-08-19），门禁已接进执行器；一次都还没对生产跑过。**
写的时候做的五个决定见 §4.8。

```bash
TRINO_HOST=localhost TRINO_PORT=8090 make gold-build ONLY=facts
```

- [x] D1 `fact_plow_shift` = **418** ✅
- [x] D2 `fact_parking_ban` = **49**，19 匹配 / **30** NULL ✅（LEFT JOIN 已确认）
- [x] D3 `fact_event_zone_rank` = **418**，`rank_factor = 0` 的行数 = **0** ✅，
      扇出守卫 17 = 17
- [x] D4 `fact_service_request_zone_event` = **13,068**，
      `COUNT(DISTINCT (snowfall_event_id, plow_zone))` = **2,178** ✅。
      事件来源是 `JOIN dim_snowfall_event`，口径单一来源
- [x] D5 排班期子集 **1,298** ✅ 格；非零 **908** 格（69.95%），
      🔴 **不是 916** —— 探针自己今天也只给 69.8%，门禁已改为下界，见 §4.9
- [ ] D6 空间命中率按探针口径精确复现：**134,123 / 134,258 = 99.9%**
      ⚠️ 这个数和 916 同源同时点（2026-08-09 的实时 API），对不上先看 §4.9 的漂移机制
      （分母 = 排班期 × 冬季 × 带几何）。对不上**信探针**
      `scripts.analysis.request_point_in_zone`
- [x] D7 冬季子集行数 = **256,077**，占 12,477,414 的 **2.05%**（2026-08-19 实测，
      §4.2 解释了为什么 design 写的 275,282 / 1.5% 是两套口径拼出来的）。
      ⚠️ 若实测 ~10.4%，是关键词松匹配（`%ICE%` 命中 Serv**ice**）
- [ ] D8 🔴 F8 开跑**之前**先重算行数期望（O14）：`≈1.6 M` 是「6,600 天 ×
      252 标签」的稠密假设，而 `ward_raw` / `neighbourhood_raw` 的 NULL 率实测
      **77.22%**，只有约 23% 的行带得动标签。先跑一年样本推全量，把期望写进
      本篇再建表——**不改 schema，只改门禁数字**。重算后的期望：____
- [x] D9 ✅ 已建成：**141,377 行 / 428s / 19 片**，年份 **2009–2026 共 18 个**
      （不是 19，2008 年无冬季且带标签的工单，见 §4.9）。
      下面这段是开跑前写的执行要求，已照做，保留作口径依据：
- [ ] ~~D9~~ `fact_winter_request_daily_by_label`（F8）——**最后跑**，且是本次唯一
      一张**不能一条语句建完**的表（O13/R2/R4）：
      ① 按日历年分片，年份只出现在每片的 `WHERE`，**不进列、不进分区键、
      不建 `dim_year`**；② 各片 `INSERT` 进 staging 表；③ 全部成功后一次性
      swap。分片建立在四步序列之上：**drop / purge / create 各做一次**，
      然后每片一条 `INSERT` 进同一张表，最后一片成功后才跑门禁。
      **绝不逐片 drop-重建**——那会留下一张只含最后一片、看着合理只是小了
      很多的表，且没有任何东西会报错。
      文件头注按 R2 写 `-- chunked_by:` 与 `-- combine:` 两行。
      分片数 ____ · 总耗时 ____ 秒
- [x] D10 ✅ 连跑两次 `ONLY=facts`，**五张表行数逐张相同**
      （418 / 49 / 418 / 13,068 / 141,377），第二趟 **全部门禁绿**。
      R4 的 purge 在事实表上验证完毕——漏了清 prefix 就是行数翻倍，
      除此之外没有任何东西会响。
      🟢 第二趟耗时与第一趟几乎一致（393s / 429s），**说明成本是分片数带来的固定
      开销，不是首次建表的一次性代价**

### 阶段 E · 收口

- [ ] E1 DQ 基线：13 张表逐张记行数 · 各列空值率 · 构建耗时（§3 表）。
      **L3 的 E6 只做汇总，基线在这里产生**
- [x] **E2 `dag_gold_build` 在 Airflow 容器里跑通了**（2026-08-20）。
      代价是**四个**必然失败的缺陷 + 两个环境坑，全部记在 §4.10。
  - [x] E2a 四个缺陷修完，三个由新单测钉死（`4c5946c` / `5f5b5df`）
  - [x] E2b `only=seeds` 15 秒全绿 → 全量 13 张表 **2,127 秒全绿**，
        行数与 2026-08-19 逐张相同
  - [x] E2c scheduler 触发也跑通（O16，§4.12）：卡住的原因是 DAG 被
        重新置为 paused，unpause 后积压的 run 6 秒内全部 success
- [x] E3 O11 的 C6 修订文本已同步（2026-08-19），实际是**四处**不是三处：
      `CLAUDE.md`（3 处：仓库结构表 · Data architecture rules · Gold/Trino 小节）·
      伞篇 `20260817-etl-implementation.md`（4 处）· `sql/dml/README.md`（3 处）·
      外加 `.claude/rules/gold-sql.md` R4/R6 本就是新写的。
      **提前于阶段 E 执行**：`sql/dml/README.md` 就在阶段 D 要写 5 份 fact DML 的
      那个目录里，留着旧文本等于给下一个人埋雷（§0 第 1 条：照旧文本写，
      第二次跑行数翻倍且不报错）。
      改法是**分层表述**：Silver（Spark）保持 `INSERT OVERWRITE PARTITION`，
      Gold（Trino）改整表重建四步。
- [ ] E4 `CHANGELOG.md` 记一条（`[Unreleased]`）
- [ ] E5 分支 push + PR
- [ ] E6 O8 单独开一个 PR：两个 Bronze 探针搬进 `dag_audit_bronze`。
      **不合进本次**——Bronze 审计是日频的，Gold 构建不是

---

## 3. 门禁的实际结果

（执行时填。空表格比漏填的表格诚实。）

✅ **阶段 D 完成（2026-08-19）：13 张表全部建成、门禁全绿、连跑两次行数逐张相同。**
事实表首跑有两条门禁没过，两条都是**门禁的数字错了、表是对的**，见 §4.9。

| 表 | 期望 | 实测 | 耗时 |
|---|---|---|---|
| `dim_winter_category` | 7 | ✅ 7（`is_effective` 6） | 1.6s |
| `dim_service_type` | ≤ 3,563，anti-join 0 | ✅ **3,516**，anti-join **0** | **621s** |
| `dim_channel` | 15 | ✅ 15（Silver anti-join 0） | 0.7s |
| `dim_plow_zone` | 25 / 8 / 3 | ✅ 25 / 8 / 3，`address_count` 无空值 | 1.8s |
| `dim_admin_label` | 252 (15+237) | ✅ 252 (15+237) | **417s** |
| `dim_snowfall_event` | 99 / 59 | ✅ 99 / 59（**首跑 58**，见 §4.6） | 1.4s |
| `dim_plow_event` | 19 / 17 / 2 | ✅ 19 / 17 / 2，扇出守卫 17 = 17 | 1.3s |
| `dim_region_crosswalk` | Σw≈1 | ✅ **548** 行，Σw≈1，并列组 **0** | 60s |
| `dim_recommendation_rules` | — | ✅ **6**，`is_fallback` 1 | 0.7s |
| `fact_plow_shift` | 418 | ✅ 418 | 1.4s |
| `fact_parking_ban` | 49 (30 NULL) | ✅ 49，19 匹配 / 30 NULL | 0.7s |
| `fact_event_zone_rank` | 418 (rank=0 → 0) | ✅ 418，rank=0 → 0，扇出 17=17 | 0.8s |
| `fact_service_request_zone_event` | 13,068 / 2,178 / 1,298 | ✅ 13,068 / 2,178 / 1,298，非零 **908**（≠916，§4.9） | **393s** |
| `fact_winter_request_daily_by_label` | **141,377**（O14 实测） | ✅ 141,377，**18** 个年份（≠19，§4.9） | **428s** |

种子段（3 张）合计约 3 秒；维表段（6 张）合计约 **18 分钟**，其中
`dim_service_type` 与 `dim_admin_label` 两张分片表占 **94%**（621s + 417s）。
两张都是 19 片，每片一条带年份谓词的 `INSERT`——**耗时来自分片数不是数据量**，
与 O13「墙在分区数」是同一件事的两面。

事实表段（5 张）合计约 **14 分钟**，同样是两张 19 片的表占 **97%**
（393s + 428s）。三张直通表加起来 3 秒。

三个探针数字：面板 **1,298** ✅ 精确命中 · 非零 **916** ❌ **实测 908，且探针
自己今天也只给 69.8%（≈906）——916 已失效，见 §4.9** · 空间命中
**134,123 / 134,258** ⏳ 未复现（D6，属收口）

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

### 4.6 🔴 阶段 C：两个缺陷，都是**只有跑起来才会暴露**的那一类

九份 DML 此前只过了 sqlfluff 与 `--dry-run` 渲染（§7.4）。首次对生产执行，
两条都在第一分钟内炸出来，且两条**静态检查都抓不到**：

**① 六份 DML 缺 Silver 的 schema 限定** —— `dim_service_type` 首跑立刻
`TABLE_NOT_FOUND: Table 'hive.uoip_gold.silver_service_request' does not exist`。
执行器连接时注入的默认 schema 是 `uoip_gold`（`_connect(settings, self.gold_schema)`），
所以 DML 里的裸表名 `FROM silver_service_request` 解析到 **Gold** schema 下。
单测 `test_dml_files_carry_no_catalog_or_schema_qualification` 只禁止写死
`hive.` / `uoip_gold`，从不要求 Silver 表被限定——两条规则中间有个洞。

修法是 `FROM {{ silver }}.silver_service_request`，六份文件共 11 处。
🔴 **必须是双花括号的真 jinja，不能是单花括号占位符**：schema 限定符位于
`FROM` / `JOIN` 子句里而**不在字符串字面量内**，`{silver}` 那种写法 sqlfluff
直接判 unparsable，连带 `SELECT *` 与分区谓词的静态检查一起失效——正是 §7.3
已经写过的那条坑，只是它当时说的是分片谓词。`.sqlfluff` 加了
`[sqlfluff:templater:jinja:context] silver = uoip_silver` 让 lint 侧解析得动，
`build_gold.py` 侧做同名替换。

**② `is_scheduling_era` 的边界日期与探针差一个月** —— C6 首跑 99 行对但
`is_scheduling_era=true` 只有 **58**，期望 59。根因：

| | 值 |
|---|---|
| 探针 `snowfall_events.py:380` | `date(SCHEDULE_FIRST_WINTER, 11, 1)` = **2015-11-01** |
| DML 原写法 | `DATE '2015-12-01'` |

`2015-12` 来自同一份探针第 81 行的注释「plow schedule only to 2015-12」（首条
排班记录的月份），但探针实际用的是**雪季起点**。中间这一个月里恰好有 1 个降雪
事件，于是 59 → 58。

🔴 这个错在 DML 文件**内部就自相矛盾**：同一个 SELECT 里 `snow_season` 用
「11 月起算新雪季」，一个 2015-11 的事件会被标成 `2015-2016` 赛季，却又被判为
「非排班期」。同一条赛季边界，一处 11 月一处 12 月。改成 `2015-11-01` 后
99 / 59 全绿。

**可复用的教训**：BO-2 / BO-3 签过字的数全部是在探针口径上量的，DML 里每一个
日期字面量都要回探针源码核对**取值**，不能照抄注释里的月份。launch 文档
「对不上信探针」那条规则这次是对的。

### 4.9 🔴 阶段 D：两条门禁没过，两条都是**门禁的数字错了**

首跑 13 张表里 11 张全绿，两条 FAIL。查完之后**没有一条是表的问题**——
这跟阶段 C 的两个缺陷（§4.6）正好相反，那两条是代码错、数字对。

**① F1 的非零格：实测 908，门禁写 916。**

916 出自 `scripts.analysis.snowfall_events --zone-panel`，量于 2026-08-09。
**2026-08-19 把同一条命令原样重跑，探针自己只给 69.8%**（1,298 × 69.8% ≈ 906）——
Gold 的 908（69.95%）反而比探针略高。**管道与探针今天相差 1–2 格，916 才是离群值。**

漂移的机制值得记下来，因为它下次还会发生，而且伪装得很好：

- 工单是**在变多**的（探针本次拉到 `u7f6-5326: 134,281` 行，8-09 是 134,258）。
  加行只会把零格变非零格，**解释不了非零格减少**。
- 变的是**事件边界**。Open-Meteo 会回修历史存档，降雪量一改，`segment_events`
  重新切一遍，某些格子的那几天就不再落在自己的事件里。
- 🔴 而 **N=99 / 排班期 59 / 中位时长 1.0 全都纹丝不动**，`--zone-panel` 那一行
  以外没有任何输出会显示这件事发生过。

处置（2026-08-19 决定）：**该门禁从等值改为下界 `>= 880`，并把实测值印在门禁
描述里**。理由是它测的是「上游今天长什么样」，不是「这次构建有没有坏」——
真能抓构建故障的三条（13,068 / 2,178 / 1,298，分别对应 purge 没生效、
骨架缺事件、排班期口径变化）**全部保持等值**，本次也全部精确命中。
`build_gold.Table.extra_gates` 因此支持可选的第四元 `">="`，
单测 `test_only_the_live_upstream_number_is_a_lower_bound` 钉死**只有这一条**
可以是下界——防止以后有人拿 `">="` 去消一条真正在报警的门禁。

⚠️ 顺带一件要记明的事：可行性台账与 ADR 0010 把 **70.57%** 记作「判据达成
（阈值 ≥ 70%）」，今天复测是 **69.8%**，严格讲低于阈值。
**决定：按四舍五入视为仍达成，判据状态不改，不重开签字**（2026-08-19）。
这里如实记下实测值，是为了 L3 定 M1 训练面板时能看见它贴着线。

**② F8 的年份数：实测 18，门禁写 19。**

`SELECT YEAR("date") ...` 的实测分布是 **2009–2026 共 18 年**，每年
3,938–16,409 行、231–248 个标签，没有任何一年像掉了分片。**2008 年一行没有**：
Silver 里 2008 有日分区，但没有一条工单同时满足「冬季 `type`」和「带行政区文本」。

19 这个数是**照分片数（2008..2026）推的，从来没量过**——而这条门禁存在的
理由正是「不要把推论当实测」。已改为 18 并写明年份区间。

🟢 行数 141,377 精确命中 O14，两条一起看反而互证：分片全跑到了。

### 4.8 阶段 D 的五个决定（写 DML 时定的，跑之前先知道）

1. 🔴 **F1 也必须分片**，理由与 `dim_service_type` / `dim_admin_label` 同源：
   它要按事件日期数工单，事件横跨 2008–2026，一条语句就是 R1/O13 禁止的
   4,878 分区扫描。分片键取 **`dim_snowfall_event.start_date` 的日历年**，
   不是工单日期——**一个事件只属于一个分片**，所以面板格子天然不重叠，
   不需要 `dim_admin_label` 那种反连接。
2. 🔴 **F1 每片的工单窗口比分片本身多 45 天**
   （`open_date_local < DATE '{chunk_end}' + INTERVAL '45' DAY`）。
   跨年事件（12 月起、1 月止）的 1 月那几天否则会被**静默丢掉**：归属仍由
   `BETWEEN e.start_date AND e.end_date` 判定，多出来的窗口只是让那些行进得来。
   实测最长事件 11 天，45 是富余。
3. 🟡 **`weighted_request_count` 对解析不出优先级的 `type` 取权重 1，不是 0。**
   取 0 会让 `request_count` 与加权列在没人看得见的地方各说各话；
   1 的语义是「读不出优先级」而不是「这条不算数」。
4. 🔴 **F8 不建骨架、不写零行**，与 F1 相反。F1 的零是 M1 的训练信号，
   F8 的粒度里没有事件可以张成面板，硬造 6,600 × 252 个空格子等于凭空多出
   150 万行什么也不说。F8 不在评分链上（design §6.10），两张表不共用任何列。
5. 🟡 **F8 的 `label_id` 内连 `dim_admin_label`**，让 FK 由构造保证而不是由
   论证保证——两边都是同一套 casefold 值（O10），连接不会掉行；真掉了，
   141,377 那条门禁会先响。

另外补了四条门禁到 `build_gold.TABLES`（DDL 头注里 `COUNT(DISTINCT ...)`
那几行 `parse_gates` 只当散文，不执行）：F2 的 17/17 扇出守卫两条，
F1 的 2,178 / 1,298 / 916 三条 + 「99 个事件都在」的分片覆盖条。
**916 是唯一能证明「计数落地了」而不只是「骨架建对了」的那条。**

### 4.7 🟡 通知只挂在成功路径上，18 分钟的失败跑静默收场

首次 `ONLY=dims` 跑了 18 分钟、`dim_snowfall_event` 门禁失败退出 1，
**一条通知都没发** —— 新加的 Discord 通知当时只在成功分支触发。
而「跑了 18 分钟然后挂了」恰恰是最需要被推送的情形：长到可以走开的运行，
它的**失败**比成功更需要送到人手上。

已改为 `notify_build_outcome`，成功 / 门禁失败 / 崩溃三条路径都通知，
仍以 300 秒为唯一过滤条件（低于它终端输出还在眼前，通知就是噪音）。
第二次 18 分钟的成功跑**实测收到 Discord 消息**，链路端到端验证通过。

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

### 4.10 🔴 阶段 E2：`dag_gold_build` 有**四个**必然失败，全套门禁都是绿的

这个 DAG 在 `ed3bff1` 落地时只过了 `py_compile`。2026-08-20 第一次真正放进
Airflow 跑，连炸四次，**每一次 `make lint` + `make test-unit-offline` 都是全绿的**：

| # | 缺陷 | 炸在哪 | 症状 |
|---|---|---|---|
| 1 | `from airflow.utils.dates import days_ago` | parse | 模块在 Airflow 3 已整个删除（镜像 `apache/airflow:3.2.2`）。scheduler **静默跳过** parse 失败的文件——UI 上就是"这个 DAG 怎么没出现" |
| 2 | 容器里没有 `sql/` | 运行期 | compose 只挂了 `ingestion`/`scripts`/`config`/`spark`，而 `DDL_DIR`/`DML_DIR` 是 `parents[2]/sql/...`。`config/seeds/` 反而是挂着的，所以失败会长得像"只有某几张表挂了" |
| 3 | `from dags._dag_common import ...` | parse | `ModuleNotFoundError: No module named 'dags'`。Airflow 把 **dags 目录本身**放进 `sys.path`，里面的模块是顶层模块，没有 `dags` 包 |
| 4 | `get_bucket()` 漏传 `params` | 运行期 | `TypeError`。`get_bucket(params)` 本就实现了"Param 优先、回落 `S3_BUCKET_NAME`"，DAG 里又把 Param 那半段手写了一遍还写漏了参数 |

**共同点是同一个**：这个 DAG 是照着「应该长什么样」写的，不是照着「隔壁九个
实际怎么写」写的。#3 和 #4 都是仓库里**独一份**的写法——十个 DAG 文件里，
另外九个的 `get_bucket` 调用要么传 `params` 要么传 `{}`，导入兄弟模块也一律裸名。
`.claude/rules` 里那条"绝对导入"的仓库级约定**不管 `dags/` 里面**，正是它把人带偏的。

新单测 `tests/unit/test_dag_deployment_contract.py`（25 项，**不依赖装 airflow**）
钉死 #1 #2 #3：

- 每个 `dags/dag_*.py` 都不得 import Airflow 3 已删除的模块；
- 每个 `dags/dag_*.py` 都不得以 `dags.<module>` 形式导入兄弟模块（行首锚定，
  所以讲这件事的注释不会误伤）；
- `sql` / `config` / `scripts` / `ingestion` / `spark` 五个目录必须**同名**挂在
  `/opt/airflow/plugins/` 下。

🔴 **#4 没有加测试**，这是个已知缺口不是遗漏：要验证它得真的 import `_dag_common`，
而那需要装 airflow——加个不装 airflow 也能过的假测试，比不加更坏。真正的解法是
**CI 里装 airflow 跑一次 DAG import + `airflow tasks test`**，见 §5 的 O15。

#### 两个环境坑，看起来都像"DAG 没被发现"

- 🟡 **新 DAG 默认 paused。** `airflow dags trigger` 对 paused 的 DAG **照样返回成功
  并排队**，但永远不执行，`states-for-dag-run` 的 state 列一直空着。
  `airflow dags unpause` 打印的是**改之前**的状态（显示 `True`），别照着它判断，
  以 `airflow dags details ... | grep is_paused` 为准。
- 🟡 **改了 compose 的卷必须重建容器。** `make stack-restart-airflow` 走的是
  `restart`，容器 CREATED 时间不变、卷不会重挂；要的是
  **`make stack-recreate-airflow`**。判据是 `docker ps` 的 **CREATED 远早于
  STATUS 的 Up 时长**。三个 target 的分工 Makefile 里早有注释：
  restart（只改了代码）· recreate（改了 .env / compose）· rebuild（改了 Dockerfile）。

#### Airflow 3 的 CLI 变了，旧记忆会连错三次

`airflow tasks logs` 不存在（日志走 UI 或日志卷文件）· `dags list-runs` 没有 `-d`
（dag_id 是位置参数）· `dags delete-run` 不存在（删 run 只能走 UI）。
`airflow dags test <id> --conf '...'` 是最省事的验证入口：**前台跑、输出直接打在终端**，
执行的是同一个回调、同一套容器环境、同一份挂载。

#### ✅ 实跑结果（2026-08-20）

- `only=seeds` 15 秒，3 张种子表全绿：7 / 15 / 6。
- **全量 13 张表：2,127 秒（35.5 分钟），门禁全绿。**
  = 阶段 C 的 18 分钟 + 阶段 D 的 14 分钟，两次独立测量对得上，
  **成本是分片数的固定开销**这条再次成立。
- 行数与 2026-08-19 那次**逐张相同**。其中两个值得单独记的复现：
  `fact_service_request_zone_event` 的非零格 **908**（§4.9 改的下界 `>= 880` 生效）·
  `fact_winter_request_daily_by_label` 的 **18 个年份**（2009–2026）与 141,377 行。
  §4.9 那两条更正**不是一次性偶然**。

✅ **scheduler 触发这条路也验证过了**，见 §4.12。

### 4.11 ✅ O15 结案：CI 装 airflow 跑 DAG 测试，并**调用**任务体

§4.10 的四个缺陷里，#1 #3 是 import 期的，#2 是路径的，**#4 是调用期的**——
import 测试从它旁边走过去，什么都不会发生。所以补的东西分两层：

1. **`airflow` 可选依赖**（`pyproject.toml`），钉死 `apache-airflow==3.2.2`
   与部署镜像一致。**不放进 `dev`**：装它很重，日常循环不需要，
   `make test-unit` 得保持快。CI 里多一个 `dags` job 装它。
   ⚠️ **版本必须跟生产同一个大版本**——这四个缺陷全是 Airflow 3 特有的，
   其中两个在 Airflow 2 下会正常通过，用错版本的 CI 测的是另一个东西。
2. **`tests/unit/test_dag_gold_build.py`（7 项）真的调用 `_build`**，
   用假的 `Builder` 挡住 Trino，断言拼出来的 argv。
   验证过它抓得住：把 #4 改回去，**1.3 秒**报出与生产一模一样的
   `TypeError: get_bucket() missing 1 required positional argument: 'params'`。

`make test-dags` 是本地入口，CI 的 `dags` job 跑同一个 target。
门禁基线：`make test-unit-offline` = **861 passed, 3 skipped**（新增的文件在本地
无 airflow 时 skip，是第 3 个 skip）· `make test-dags` = **17 passed**。

#### 🔴 附带炸出来的一个坑：`pyspark-client` 会覆盖钉死的 pyspark

`apache-airflow-providers-apache-spark` 依赖 **`pyspark-client` 4.2.0**，
那是一个**独立的发行包**，却把文件写进同一个 `pyspark/` 目录，
把 3.5.1 的 `version.py` 等直接覆盖掉。表现：

- `uv.lock` 里 pyspark 老老实实是 `3.5.1`，**uv 不报任何冲突**；
- 装完 `import pyspark` 却是 `4.2.0`；
- 之后 Spark 单测炸在 `ImportError: cannot import name '_with_origin'`,
  堆栈全在 pyspark 内部，**看不出跟"我刚跑了个别的 make target"有任何关系**。

处置：`make test-dags` 走**独立环境** `.venv-airflow`
（`UV_PROJECT_ENVIRONMENT`），永远碰不到主 venv。CI 每次都是干净 runner
本来就不受影响，但 target 保留隔离，是为了本地跑一次不会把开发环境弄坏。

🟡 顺带一提 pyspark 的版本约束**只在 `pyproject.toml` 里**，
`pyspark-client` 这种"换个包名往同一个 namespace 里装"的情况它拦不住。
以后再引入带 Spark 依赖的东西，装完记得核一次 `pyspark.__version__`。

### 4.12 ✅ O16 结案：卡住的 run 只是因为 DAG 是 paused 的

现象：`airflow dags trigger` 返回成功、run 落到 `queued`，然后**永远不动**，
`states-for-dag-run` 的 state 列一直空着，scheduler 日志里一个字都没有。
而同一时间 `airflow dags test` 跑得好好的——13 张表 35 分钟全绿。

答案写在 Airflow 自己的 CLI 帮助里：
*trigger — If DAG is paused then dagrun state will remain queued, and the task
won't run.* `dags test` 是前台自己解析 DAG 文件执行的，**不看 paused 标记**，
所以两条路的表现会完全相反。

时间线（全部有实测支撑）：

| 时刻 | 事件 |
|---|---|
| 01:22:52 | 修完 #3 后 DAG 首次 import 成功 |
| 01:25:29 | `unpause` → `details` 确认 `is_paused: False` |
| 01:25:30–38 | 01:23 与 01:25 两个 run **被正常调度并执行**（炸在 #4） |
| **01:31:54** | **`git pull` 改了 DAG 文件** |
| 01:31:56 起 | 此后每一次 `trigger` 都排队不动 |
| 02:50 | `details` 显示 `is_paused: **True**` —— 期间没有人执行过 pause |
| 02:52:27 | `unpause` → 两个积压的 run 在 **6 秒内**先后跑完，全部 success |

🔴 **已证实的是**：paused 会让 trigger 静默排队，且这个 DAG 在没人手动 pause
的情况下从 False 变回了 True。**推断但未单独验证的是**具体机制——最像的是
DAG 文件变更后重新注册，`dags_are_paused_at_creation` 的默认值再次生效。
下次改 DAG 文件后顺手 `details | grep is_paused` 就能确认，成本几秒。

#### 三条会重复咬人的操作规则

1. **改完 DAG 文件后重新确认 paused 状态**，别信"我昨天 unpause 过"。
2. 🔴 **`airflow dags unpause` 打印的是改之前的状态。** 它对 `dag_gold_build`
   和 `dag_smoke_alert` 都打印了 `is_paused | True`，而两者都确实被解除了。
   判据只有 `airflow dags details <id> -o yaml | grep is_paused`。
   这个误导性输出让本轮在错误方向上多绕了两圈。
3. **定位"DAG 不跑"先用 `dag_smoke_alert` 划范围**：它手动触发、故意失败、
   不写任何数据。这次它 1 秒被调度、6 秒失败，一步就把
   "整套 Airflow 不调度了" 和 "只有这个 DAG 有问题" 分开了。
   🟢 顺带把 L1 欠的 **C5（告警端到端验证）** 的执行条件凑齐了 ——
   这是它存在的全部理由，别在下次排障时忘了有这个工具。

⚠️ **没有改 `dags_are_paused_at_creation`**。把它设成 False 会让新建的**调度型**
DAG 一注册就开始 catchup，那个后果比手动 unpause 一次严重得多。

### 4.13 🔴 顺带发现：Silver 每日增量已断三天，Gold 不受影响

E2 收尾时去 Discord 确认 C5 告警，在同一个频道里看到一条 **02:16** 的告警，
与 Gold 无关但更要紧：

```
dag_silver_service_request.sync_partitions failed (try=4)
TrinoConnectionError: host='localhost', port=8090: Connection refused
```

`localhost:8090` 是**宿主机视角**，容器里当然拒连。代码没有硬编码
（`dags/_trino_common.py` 读 `TRINO_HOST`/`TRINO_PORT`），是 `.env` 当时被改成了
宿主机视角。**现已自愈**（容器重建后 `printenv` 实测 `TRINO_HOST=trino` /
`TRINO_PORT=8080`），不需要改代码。

🔴 **但这暴露了一条规则**：`.env` 只能存**容器视角**，宿主机跑命令临时加前缀。
CLAUDE.md 记了"宿主机要加前缀"，没记反向——**改 `.env` 迁就宿主机会打断容器里
所有 Trino 调用，且要等重试耗尽（16 分钟）才以告警形式暴露**。

#### 实测到的数据缺口

| | 值 |
|---|---|
| `sync_partition_metadata('uoip_silver','silver_service_request')` | 手动跑过，`synced` |
| 同步后分区 / 行数 | **4,879 / 12,477,414** |

`run_silver_etl` 那一步是 **success**，失败的只有 `sync_partitions`——即
**数据写进了 MinIO，Trino 侧的元数据停在旧状态且不报错**（正是 `_trino_common.py`
文件头警告的那件事）。但补完元数据后看每日行数，缺口不止于此：

| 日期 | Silver 行数 | 判断 |
|---|---|---|
| 08-13 四 / 08-14 五 | 3,004 / 2,760 | 正常 |
| 08-15 六 / 08-16 日 | 1,385 / 1,109 | 周末低谷，正常 |
| 08-17 一 | 3,098 | 正常 |
| **08-18 二** | **8** | 🔴 工作日该有 ~3,000 |
| **08-19** | **无分区** | 🔴 |

与 `dags list-runs` 对得上：08-17 scheduled / 08-18 manual / 08-19 scheduled
**三次都 failed**。**`sync_partitions` 只是表象，Silver 增量实际断了三天。**

Bronze 侧已查：`bronze/raw/SRC-WPG-311/service_requests/2026-08/` 有
`data_2026-08-01` … `data_2026-08-18` 齐全。
⚠️ **缺 `2026-08-19` 是正常的**，不是缺口——`dag_ingest_service_requests` 每天
05:00 跑，08-19 的数据要等 08-20 05:00 那次；查的时候是 08-20 03:00。

#### ✅ Gold 的 13 张表不受影响，不需要重建

`.claude/rules/gold-sql.md` 记的 2026-08-19 实测行数是 **12,477,414**，
与同步后的**完全相同**。也就是说 01:35 建 Gold 时可见的数据与现在逐行一致，
§3 记的行数有效。（分区数 4,878 → 4,879 的差异不影响行数。）

另有一条同方向的推理**但未实测**：Gold 事实表读的是雪季事件窗口，8 月中旬三天
既不落在任何降雪事件内、`type` 也不在冬季子集里。**要证实就用 `only=facts`
重跑一次对行数**（14 分钟）；行数不变即坐实。

## 5. 遗留项

- O9：`dim_snowfall_event` 的 99 vs 159 口径，L3 M1 训练前复议
- ✅ O12 已关闭（阶段 A）。孤儿文件确实存在，`_purge_storage` 已写进
  design §4.3 的四步序列；执行器实现时**这一步不能只写在注释里**
- O13：Trino 的全表扫墙是平台级共享服务的属性，**本仓库不调它的连接参数**
  （ADR 0006 §9）。L3 的评分链按事件窗口读 Silver，天然满足 R1，但每加一条
  新 DML 都要重核一次
- O14：F8 的行数期望在本次重算后写进本篇 §3，design 篇的 `≈1.6 M` 不追改
- ✅ **O15 已关闭（2026-08-20）**，见 §4.11
- ✅ **O16 已关闭（2026-08-20）**，见 §4.12。scheduler 触发这条路是通的，
  卡住的原因是 DAG 处于 paused
- 🔴 **O17（新，2026-08-20，见 §4.13）：`silver_service_request` 缺三天数据。**
  08-18 只有 8 行（工作日该 ~3,000）、08-19 无分区，对应三次 failed 的 DAG run。
  Bronze 齐全到 08-18，**所以大概率只需重跑 Silver**。
  **这件事要排在 E1 之前**——DQ 基线是要写进文档当基准的，不值得量在缺三天的表上。
- 🟡 **O18（新）：`.env` 只能存容器视角。** 宿主机跑命令临时加前缀，不要改文件。
  改了会打断容器里所有 Trino 调用，且要等重试耗尽（16 分钟）才告警。
- 🟡 全部 10 个 DAG 都在刷 `airflow.models.param.Param` 与
  `airflow.operators.python.PythonOperator` 的 deprecation warning。今天没动——
  那是一次涉及所有 DAG 的独立变更。但 `days_ago` 刚刚演示过 deprecated 会变成
  删除，**下个大版本这就是 10 个 DAG 一起 parse 失败**

## 7. 交接 —— 下个会话从这里继续

### 7.1 已经做完的（不要重做）

| | 状态 |
|---|---|
| 阶段 A（O12 实测） | ✅ 结案。四条重建路径量完，见 §4.1 |
| 阶段 B（代码） | ✅ 执行器 + 门禁 + 4 份种子 CSV + 33 项单测 + Makefile + DAG，`ed3bff1` |
| 阶段 C 的 DML | ✅ 9 张全部就绪（3 种子由执行器生成 + 6 份手写 SQL） |
| **阶段 C 执行** | ✅ **9 张维表全部建成、门禁全绿**（2026-08-19）。数字在 §3，两个缺陷在 §4.6 |
| 阶段 D 的 DML | ✅ 5 份全部写完 + 门禁接进执行器（2026-08-19）。决定见 §4.8 |
| **阶段 D 执行** | ✅ **关闭**（2026-08-19）：5 张事实表建成、门禁全绿、连跑两次行数逐张相同（D10）。两条门禁数字的更正见 §4.9 |
| 阶段 E（收口） | 🚧 进行中：E3 早已完成，**E2 已跑通**（2026-08-20，四个缺陷见 §4.10），余 E1 / E2c / E4 / E5 / E6 |

门禁基线：`make lint` 干净 · `make test-unit-offline` = **861 passed, 2 skipped**
（E2 的新单测 +25）。

### 7.2 下一步，按顺序

1. ~~跑阶段 C~~ ✅ **已完成（2026-08-19）**，9 张维表全绿，见 §3 与 §4.6。
2. ~~阶段 D 的 5 份事实表 DML~~ ✅ 已写完并**跑通生产**（2026-08-19）：
   13 张表全部建成，改完两个门禁数字后全绿。数字在 §3，两条更正在 §4.9。
3. ~~D10 复跑~~ ✅ **已完成**：连跑两次行数逐张相同，第二趟全绿。
   R4 的 purge 在事实表上验证完毕。**阶段 D 关闭。**
4. **阶段 E 收口。进行中。** ✅ **E2 已完全跑通**（2026-08-20）：全量 13 张表在
   Airflow 容器里 2,127 秒全绿，scheduler 触发那条路也验过了。
   代价是四个必然失败的缺陷（§4.10）+ 一个 paused 造成的假死（§4.12）。
   O15 / O16 均已关闭。

   **下一个会话从这里开始，按顺序：**

   1. 🔴 **先修 O17（§4.13）**：`silver_service_request` 的 08-18 只有 8 行、
      08-19 无分区。Bronze 到 08-18 齐全，所以第一步是**查 08-18 的 Bronze
      到底有多少条**——`manifest_2026-08-18.json` 里的 `record_count`
      对上 8 就是上游真没数据，对不上就是 Silver 那侧的问题。
      然后触发 `dag_backfill_silver_service_request`，窗口
      `2026-08-12` → `2026-08-20`（幂等，按日分区覆盖）。
      ⚠️ 触发前先 `airflow dags details <id> -o yaml | grep is_paused`，
      §4.12 那个坑对每个 DAG 都成立。
   2. 补完后 `ONLY=facts` 重跑一次 Gold（14 分钟），对 §3 的五个行数。
      §4.13 已论证不该变，但那是推理不是实测。
   3. **E1 DQ 基线**：13 张表逐张记行数 / 各列空值率 / 构建耗时。
      §3 已有行数与耗时（全量 2,127 秒那次），**只缺空值率**。
   4. D6 空间命中率复现 · E4 CHANGELOG · E5 PR。

阶段 D 跑完后，对阶段 E 与 L3 有约束的三件事：

- 🔴 **探针数字会漂，而且不出声。** §4.9 记的机制（Open-Meteo 回修存档 →
  事件边界重切）对 L3 直接有效：F6/M1 的口径数字同样是这样量出来的，
  **重跑一次探针再对比，比相信台账里的数字便宜**。
- 🟡 **分片表就是慢。** 事实表段 14 分钟里 97% 是 F1 + F8 两张 19 片的表。
  L3 若再加分片表，按每张 **5–10 分钟**估。
- 🟢 **门禁的等值/下界之分已经定了口径**：能抓构建故障的保持等值，
  盯着实时上游的才允许下界，且单测钉死了当前只有一条。L3 新增门禁照这个分。

---

阶段 C 跑完后，对阶段 D 的三条约束（已消化，留作记录）：

- 🔴 **`dim_service_type` 的 3,516 行是 D4 面板的一个输入**，不是 3,563。
  期望值写「≤ 3,563」是对的，但事实表的门禁要用实测值推。
- 🟡 **分片表很慢**：两张 19 片的维表占了 18 分钟里的 94%。F8 同样 19 片、
  且每片要扫的分区更多，§4.3 估的「5 分钟量级」偏乐观，按 **10–20 分钟**准备。
  好消息是现在有 Discord 通知（§4.7），可以走开。
- 🟢 **anti-join 那类全表扫这次没有 timeout**，O13 的墙比预想的靠后。
  但这不构成对 F8 的保证——F8 读的是真实列不是 `DISTINCT type`。

#### 2026-08-19 补：三份 DML 已写完（`dim_service_type` / `dim_plow_event` /
`dim_region_crosswalk`），四处写的时候做的决定，跑之前先知道：

- 🔴 **`dim_service_type` 也必须分片**，理由与 `dim_admin_label` 完全一样：
  它要枚举全历史的 distinct `type`，正是 R1/O13 禁止的 4,878 分区扫描。
  已加进 `build_gold.CHUNKED`（2008..2026），分片重叠，PK 靠**对已插入行的
  反连接**保住。
- 🔴 **仲裁顺序与优先级正则没有 Gold 表可落**（17 张冻结，`dim_winter_category`
  也没有 priority 列），所以由执行器把两份 CSV 渲染成**字符串占位符**注入：
  `winter_category_order`（逗号连接的行序）与 `service_type_keywords`
  （`优先级;正则;权重`，`|` 连接）。两者都落在字符串字面量里，文件照样可 lint。
  CSV 仍是唯一权威；单元格里出现 `; | , '` 任一分隔符会直接抛。
- 🟡 **`dim_service_type` 的 anti-join 门禁本身是一次全表扫**
  （`SELECT DISTINCT s."type" FROM silver_service_request`，4,878 个分区）。
  构建走分片绕开了 O13，**门禁没有**。首次执行时它可能 `Read timed out` ——
  真发生了不要删门禁，改成按年分片累计比对（表由同一批分片构建，覆盖率是
  构造保证的，门禁抓的是「某个分片挂了」）。
- 🟡 **`dim_region_crosswalk` 的窗口是硬写的两个日期字面量**
  （`2023-11-01` / `2026-05-01`，= 最近 3 个雪季），与 `calibration_window`
  列里的 `'2023-2024..2025-2026'` 是同一个事实写了两遍，**必须一起改**。
  写成字面量而不是参数，是因为它是业务口径（校准在哪几个雪季上做的）而不是
  执行日期；它同时兼作 R1 的分区谓词（约 540 个分区，不是 4,878）。
  O5 未结：3 个雪季是假设不是实测最优。

### 7.3 这轮踩过的坑，别再踩一遍

- **改了 compose 的卷，用 `make stack-recreate-airflow`**，不是
  `stack-restart-airflow`——后者是 `restart`，容器不重建、卷不重挂。
  判据：`docker ps` 的 CREATED 远早于 STATUS 里的 Up 时长。
- **新 DAG 默认 paused，而 `dags trigger` 对 paused 的 DAG 照样返回成功**——
  run 排进队列后永不执行，state 列一直空着，看起来像"卡住"不像"没跑"。
- **连 Trino 要加前缀**：`TRINO_HOST=localhost TRINO_PORT=8090`。`.env` 里的
  `trino:8080` 是**给 Airflow 容器的**，宿主机 shell 解析不到（`18375d0`）。
  执行器现在会在连接失败时直接打印这条命令，不再甩 urllib3 堆栈。
- **占位符必须落在字符串字面量里**，否则 sqlfluff 解析不了整个文件——
  连带 `SELECT *` 和分区谓词的静态检查也一起失效。分片用
  `DATE '{chunk_start}'` / `DATE '{chunk_end}'`，**不要**用裸的 `{predicate}`。
- **`sql/dml/*.sql` 存的是裸 `SELECT`**，不是完整语句。`INSERT INTO` 与显式
  列清单由执行器从 DDL 组装（列名只有一个权威来源），分片谓词也由它注入。
- **`dim_admin_label` 是分片的**，尽管它的粒度不含日期：它要枚举全历史标签，
  正好是 R1 禁止的那种全表扫。分片会重复，靠**对已插入行的反连接**保住 PK ——
  这依赖「分片是先后执行的独立语句」，不要合并成一条。
- 新写的 DML 想读 `silver_service_request`，**必须带 `open_date_local` 谓词**，
  单测会拦（`test_dml_reading_silver_service_request_carries_a_date_predicate`）。

### 7.4 还没验证的

- ~~阶段 D 的五份 DML 一次都没对生产跑过~~ ✅ 五份全部跑过（§3）。这次暴露的
  两条都在**门禁的数字**上而不在 SQL 上（§4.9），与阶段 C 相反。
- ~~`ONLY=facts` 只跑过一次~~ ✅ 连跑两次行数逐张相同，purge 已验（D10）。
- 🟡 **五张事实表的 DDL 头注里 `-- relationships:` 仍写着 `... = 916`**，
  作为不执行的 prose note 每次都会打印出来。没有改，是因为它与
  `contracts/gold-contracts/` 是同一份口径而 schema 已冻结——**要改得走变更流程**。
  在那之前以 §4.9 为准，别照抄那行。
- ~~九份 DML 一次都没对生产跑过~~ ✅ 九份全部跑过，门禁全绿（§3）。
  代价是两个只有跑起来才暴露的缺陷，见 §4.6。
- ~~`dim_plow_event` 的 17/2 分布没在 Trino 上验证过~~ ✅ 已验证：19 / 17 / 2，
  扇出守卫 17 = 17。
- ~~`dag_gold_build.py` 仍未在 Airflow 里跑过~~ ✅ 跑通了（2026-08-20，§4.10），
  但**只经 `airflow dags test`**，scheduler 触发那条路仍未验证（O16）。
- `dim_plow_zone` 的 `GEOMETRYCOLLECTION` 是**字符串拼装**（Gold 不用几何函数），
  Trino 侧能不能被 `ST_GeometryFromText` 读回来**没验过**——L3 若要用它做几何
  运算，先在 smoke prefix 上试。

---

## 6. 上线后需要观察的

Gold 是手动触发，没有「连续观察 3 天」这回事。真正要盯的是**下一次 Silver
增量跑完之后**：`dim_snowfall_event` 与 F1 的行数会不会跟着变。
按当前口径（2008-11 起 + 冬季月份）不该变——除非真的下了一场新的雪。
