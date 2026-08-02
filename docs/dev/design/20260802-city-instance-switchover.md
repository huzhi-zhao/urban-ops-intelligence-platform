# 城市实例切换：退役存量实例、泛化能力、接入新实例

> **Status**: Draft · **Date**: 2026-08-02
>
> 覆盖 [roadmap.md](../roadmap.md) 的 **Phase D** 与 **Phase 2W**，
> 终点是「Silver 就绪、可开工 Gold」。Phase 3 及之后不在本篇范围内。
> 为什么退役 NYC 见 [project-overview.md](../requirements/project-overview.md#h3--向其他城市移植留围栏不留实现)；
> 为什么是自建栈见 [ADR 0006](../adr/0006-storage-compute-query-stack.md)；
> 完成时间为何只到分区粒度见 [ADR 0007](../adr/0007-clearing-completion-time-source.md)。

---

## 1. 问题

三件事经常被并称为「改造」，实际工作量差一个数量级，混谈会同时高估前两项、
低估第三项：

| | 真实状态 | 量级 |
|---|---|---|
| **去 GCP** | 代码侧已完成，CI grep 已在守。只剩 `infra/terraform/` 一条 `rm -rf`，且卡在一个**人工动作**（撤销 service account 密钥）上 | 一条命令 + 一次控制台操作 |
| **去 NYC** | 12 个 `dag_*.py` 中 7 个纯实例、3 份 source YAML、3 个 backfill 脚本、边界作业的 transform/schema/test | 约 20 个文件，纯删除 + 少量泛化 |
| **接 Winnipeg** | 需要一批本代码库**从来没有过的能力**：气象存档+预报双数据集、三套互不嵌套几何、业务语义的配置化落点、事件切分 | 本篇的实际主体 |

除此之外还有两类不显然的现状：

**通用层已有城市字面量污染**（护栏 §1 的既有违规，都是一行改）：
`ingestion/clients/socrata_client.py` 的 `domain` 默认值写死为
`data.cityofnewyork.us`；`ingestion/config/loader.py` 的环境变量名
`NYC_UOIP_CONFIG_DIR`；`scripts/backfill/bulk.py` docstring 里的
`bucket="nyc-uoip"`；`pyproject.toml` 的包名 `nyc-uoip`。

**`infra/docker/docker-compose.yml` 里有一处硬编码口令**（Airflow admin，tracked
文件）。AGENTS.md 的规定是「看到硬编码密钥先修再做别的」，因此它排在批 0。

---

## 2. 约束

**硬约束**

- **9 月底之前只服务 H1。** 本篇任何一步若只为长期运维，推迟到 H2。
- **`39ur-higg` 未经实测**，而它是 BO-4 交叉映射表的唯一输入，进而是摘要
  「三源联结」的单点依赖。取不到就要改摘要措辞，改措辞要经导师同意——
  **这条链上有人的响应时延**。
- **`g3p4-h83y` 快照不可重放**，采集已于 2026-08-02 上线（见
  [launch 记录](../launch/20260802-snapshot-collection-deployment-launch.md)）。
  本篇的任何一步都不得让它停摆。
- **Bronze 已落盘的数据不可重写**：路径布局与 manifest 字段是冻结契约
  （AGENTS.md「Manifest contract」）。
- **通用层不得出现城市专有字面量**（护栏 §1/§2）。`config/sources/` 与
  `contracts/` 里的城市名是内容不是污染；`SRC-*` 这类 source ID 照抄不改。

**显式接受的风险**

- MinIO 环境仅经 mock 单测与快照采集这一条真实通路验证，`tests/integration/`
  的 12 项在 `S3_*` 缺失时整体 skip。本篇不把「集成测试全绿」作为出口判据
  （那是 H2 的 Phase E），但批 3 的首次回填就是一次真实的端到端压力测试。
- 归一化分母（分区地址数）取自当期快照，却用于归一化 2015 年起的历史事件。
  影响量级不大，作为局限主动说明（BO-6）。

---

## 3. 数据源剖析结论（按 design/README 附表的五个问题）

新接四个源，逐个回答。**标注「未验证」的项即批 3 的实测任务**，
不得在 Silver 逻辑里按假设写死。

| | `u7f6-5326` 311 | `tix9-r5tc` 排班 | `mfzv-893p` 禁令 | `39ur-higg` 边界 |
|---|---|---|---|---|
| **主键 / 唯一性** | `case_id`（哈希串，100% 填充）；**唯一性未验证**——7 天回溯窗口必然重复拉取，去重键定不死就会累积 | `id` | `id` | 未验证 |
| **时间字段与时区** | `open_date` / `closed_date`，Socrata **floating timestamp**（无时区）——按本地时间解释后转 UTC，不可当 UTC 直接读 | `shift_start` / `shift_end`，同为 floating | `ban_start` / `ban_end` | 无（静态） |
| **高 NULL 率字段** | `neighbourhood` / `ward` / `geometry` 全表仅 **20.9%**，冬季子集 **80.1%**。这是空间命中率告警的分母 | — | — | — |
| **低基数字段** | `subject`(3) / `reason`(20 个部门) / `channel_type`(15) / `ward`(15)；`type` **3,563 个取值**是高基数 | `plow_zone`(22) / `snow_ban_id`(19) | `ban_type_id`(3) | `plow_zone`(22) |
| **关联键** | `neighbourhood` / `ward` 文本字段；`geometry` → plow_zone 空间归属 | `snow_ban_id` → 禁令 `id`（**关联完整性未验证**）；`plow_zone` | `id` | `plow_zone` |

四个必须处理的既有结论（依据见
[winnipeg-data-sources.md](../requirements/winnipeg-data-sources.md)）：

1. **`reason` 是责任部门，不是投诉原因。** 分析粒度在 `type`。
2. **渠道口径 2022 年迁移**：`Self Service + Mobile + SMS In → VOF`，
   跨年渠道结构不可直接比较，总量与类型仍可比。
3. **`type` 内嵌官方 P1/P2/P3 与 Reg/After**，但写法不统一
   （`Pr 2` / `Priority 2` / `P2`，另有 `_vof` 后缀变体）。
4. **冬季关键词匹配必须精确**：`%ICE%` 会误伤 `Serv-ice` / `Pol-ice` /
   `Not-ice` / `Invo-ice`，粗糙匹配曾误得 10.40%，真值 1.50%。

---

## 4. 方案

按**谁挡着别人**排序，不按分层排。每批独立通过 `make lint` + `make test-unit`，
不合并成一个大提交。

### 批 0 · 前置（不写业务代码，最先发起）

三件事互不依赖，都应立刻发起，因为它们的时延不由写代码的速度决定。

| | 内容 | 为什么在这里 |
|---|---|---|
| 0a | ~~快照采集上线~~ **已完成 2026-08-02** | 唯一每天在流血的事项，已止血 |
| 0b | **实测 `39ur-higg`**（备选 `tm8b-h7pb`） | 不写代码。取不到 → 改摘要 → 等导师，**链上有人的响应时延** |
| 0c | GCP 控制台撤销 SA key → `rm -rf infra/terraform` | 人工动作，可并行。⚠️ 顺序不可颠倒：删本地文件 ≠ 撤销凭证 |
| 0d | 移除 `docker-compose.yml` 的硬编码 Airflow admin 口令 | AGENTS.md 安全规则要求先于其他工作 |

**出口判据**：0b 产出一份可复现的调用记录（22 个多边形、几何类型、坐标系、
`plow_zone` 取值与 `tix9-r5tc` 是否一致），并落进
[winnipeg-data-sources.md](../requirements/winnipeg-data-sources.md) §6.2
替换掉「未实测」标注。**取不到则本篇批 4 改走 ADR 0007 §4.2 的降级方案，
且必须在批 1 开工前知道结论。**

### 批 1 · 实例退役与通用层去字面量（Phase D）

**本批不新增任何能力。** 放在前面只有一个理由：它会污染 H1 的每一次改动。
限时一天量级，超时说明范围划错了。

三件事：

1. **删城市实例**——三份 source YAML、对应 backfill 脚本、7 个纯实例 DAG、
   实例专有的 profiling 分发表条目与测试。
2. **通用层去字面量**——Socrata 客户端的 `domain` 改为**必填无默认**
   （默认值是这类污染最隐蔽的形态：它让「忘了配」变成「静默连到别的城市」）；
   配置目录环境变量改为城市无关名；docstring 与包名同批清理。
3. **保留能力实例暂不动**——`etl_open_meteo.py` / `etl_dcp.py` 及其
   transform/schema 留到批 2、批 4 泛化后再删。**先泛化后删除，不可颠倒。**

**出口判据**（可执行）：

```bash
grep -rniE "nyc|cityofnewyork|nypd|borough|dcp" --include="*.py" \
  dags ingestion spark scripts/backfill/_common.py scripts/backfill/bulk.py \
  scripts/backfill/main.py scripts/backfill/_registry.py \
  | grep -vE "etl_dcp|transforms/dcp|dcp_schemas"
```

期望输出为空（批 4 结束后连排除项一起去掉）。同时 `make test-unit` 保持全绿——
删实例不该让通用层的测试变红，变红就说明通用测试依赖了实例数据。

### 批 2 · 气象源双数据集（BO-3）

**这不是「改坐标」，是 H1 的一项新功能。** 当前 `SRC-Open-Meteo` 指向 forecast
端点、hourly 变量、`America/New_York`；BO-3 要的是**两个数据集**：

| 数据集 | 端点 | 粒度与字段 | 服务 |
|---|---|---|---|
| 存档 | Archive API | 日粒度 `snowfall_sum` / `temperature_2m_min|max`，回溯至 2008 | BO-3 事件切分、M1 训练 |
| 预报 | Forecast API | 前瞻窗口 | M1 推断（评分得以前瞻而非回顾） |

**source id 保持 `SRC-Open-Meteo` 不变**——它本来就是城市无关的角色名，
换城市只换 `query_params`。改的是 dataset 名、端点、坐标 `49.895,-97.138`、
时区与变量集。

⚠️ **两个数据集的分区策略不同**：存档是 `daily`（记录日期即分区键），
预报是覆盖式前瞻窗口，落 Bronze 后每次拉取都会与前一次重叠。
预报的分区策略在 §6 作为开放项。

**出口判据**：能从 Bronze 读出 2008 起的逐日 `snowfall_sum` 序列，
并跑通一次阈值切分产出事件表；事件数量**实测得到**，不得沿用 BO-3 的「百级」预估。

### 批 3 · 新城市源接入与回填

Socrata 客户端与三层 backfill 架构零改动复用；registry 自动发现，
**新增源 = 丢一个 `config/sources/*.yaml` + 一个 `backfill_*.py`**。

四个源按体量分两类：

- `tix9-r5tc`(418) / `mfzv-893p`(49) / `39ur-higg`(22)——体量极小，
  `static` 或 `monthly`，一次拉完。
- `u7f6-5326`(1,835 万行 / 18 年)——`daily` + `timestamp_field: open_date`，
  是唯一需要认真排期的回填。回填范围见 §6 开放项。

**DAG 数量纪律**：存量的「每源一个 backfill DAG + 一个 ingest DAG」模式在
5–6 个源上会膨胀到 12 个 DAG。H1 不需要——**回填留 CLI（本就是三层架构的入口），
只给活跃源建 ingest DAG**。

**出口判据**：

```bash
ls dags/dag_ingest_*.py | wc -l    # ≤ 4
```

且 `dag_audit_bronze` 的滚动窗口核对在新源上跑通（快照部分仍**只报不补**）。

### 批 4 · 边界能力泛化 → 删除存量实例（BO-4 前置）

`etl_dcp.py` + `transforms/dcp.py` 是**唯一一条跑通过的 GeoJSON → WKT 通路**，
而 BO-4 的 plow zone 边界要用的正是这条通路。先删后写等于在最大风险点上自断退路。

现状分层已经很清楚，泛化成本低：

| 现有函数 | 性质 | 去向 |
|---|---|---|
| `_geojson_struct_to_wkt` / `add_geometry_wkt` | 已是通用的（只写死了列名 `the_geom`） | 移入按能力命名的 transform，列名参数化 |
| `cast_scalars` / `split_by_validity` | 实例专有（`borocode` / `boroname` / `VALID_BOROUGH_IDS`） | 字段映射与校验白名单进配置，代码只留「按映射投影 + 按白名单分流」 |
| `enforce_schema` | 通用 | 原样保留 |

⚠️ `spark/schemas/dcp_schemas.py` 记录了一条**必须随能力一起搬走的约定**：
这是 AGENTS.md「always pass schema=」的**唯一豁免源**——MultiPolygon 的
StructType 深达数百层，因此刻意不传 schema，只对标量字段重新定型。
泛化后的作业继承同一豁免，且必须在新文件里重述理由，否则下一次 review 会当 bug 改掉。

**顺序**：0b 结论 → 泛化 → 用新作业跑通 22 个多边形 → **再删**存量实例。

**出口判据**：`dim_geography` 能同时承载 neighbourhood / ward / plow_zone 三种归属；
空间命中率 > 90%，**分母为有 `geometry` 的冬季工单**（不是全表——全表 79%
无地理是上游特性，按全表算会永久误报）。

### 批 5 · 业务语义配置化 + Silver

**这是全篇唯一容易违反护栏 §1 的地方**，因为三类语义看起来「就是几行
when/otherwise」，默认会被写进 `spark/transforms/`：

- 冬季关键词（6 类，且不误伤 `Serv-ice`/`Pol-ice`）
- 渠道归一化映射（`Self Service + Mobile + SMS In → VOF`）
- 3,563 个 `type` 的优先级/时段解析（P1/P2/P3、Reg/After、`_vof` 变体）
- 降雪事件阈值

当前 `config/` 只有 source registry 一种机制，**没有承载这类东西的地方**——
这正是它们会被写进代码的直接原因。因此本批先定落点：

| 内容 | 落点 | 理由 |
|---|---|---|
| 解析规则、关键词、映射、阈值 | **`config/semantics/*.yaml`**，与 `config/sources/` 同级、同样 Pydantic 校验 | 它们是「换个城市要改」的东西，判据即护栏 §1 |
| 解析产出的派生字典（如 `dim_request_type`） | **Gold 种子表**（`sql/ddl/` 下的 seed） | 3,563 行取值是数据不是配置；且它要被 SQL JOIN |

Silver 本身按 [ADR 0004](../adr/0004-silver-cleansing-methodology.md)：
去重键（依赖 §3 的唯一性实测）、UTC 标准化（floating timestamp 按本地时间解释）、
`has_geo` 标记列、渠道归一化。

**出口判据**：冬季工单识别在 Silver 上复现 **275,243 / 220,580 带地理**这两个
实测数字（±1%）——对不上就是识别规则或去重键错了，这是最便宜的一条端到端断言。

---

## 5. 被否决的选项

| 选项 | 否决理由 |
|---|---|
| 先删 `etl_dcp.py` 再写 plow zone 边界作业 | 它是唯一跑通过的 GeoJSON→WKT 通路，而它的替代品依赖一个**未实测**的数据源。先删等于在最大风险点上自断退路 |
| 把 `SRC-Open-Meteo` 拆成两个 source id | source id 是**角色名**，本就城市无关也端点无关。拆开会让「气象」这个角色在 registry 里出现两次，且两者共用同一个 client 与 fetcher |
| 三类业务语义写进 `spark/transforms/` | 违反护栏 §1。判据不变：这段逻辑换个城市要不要改？要改就是配置 |
| 为 Winnipeg 每个源复制「backfill DAG + ingest DAG」模式 | 12 个 DAG 对 H1 无收益。回填的入口本来就是 CLI，三层架构的设计意图正是让 DAG 只做调度 |
| 把 Phase D 推到 Winnipeg 开发之后 | Phase D 不新增能力，但存量会污染 H1 的每一次改动——每一次「改到相关文件」都要先判断这是存量还是范式 |
| 先跑通 Trino + Superset 再做 Gold | H1 的空间归属规模是 22 个多边形 × 22 万个点，Spark 广播多边形逐点判定即可。查询层属 H2（roadmap） |
| 保留 NYC 存量作为可移植性实证基线 | 该论证依赖 H3 有真实价值，H3 已降为围栏。且它与 H2 判据直接冲突：一个号称可复用的平台不能内含半个别的城市 |

---

## 6. 开放项

| 项 | 现状 | 建议 | 何时必须定 |
|---|---|---|---|
| **311 回填范围** | 全量 18 年 = 约 6,600 个日文件，而冬季相关仅 1.50% | **按月份维度限定 11–3 月**。这是**分区级范围参数**（现有 CLI 已支持），不是行过滤，不破坏 Bronze 不可变语义。代价：§2.4 的长期趋势分析缺夏季对照 | 批 3 开工前，**需人工决定** |
| **新源的 source id 命名** | 仅 `SRC-WPG-SNOW` 一个既有先例 | 沿用同一前缀模式；但**以写进 `config/sources/` 的值为唯一权威**，本篇的任何写法都不作数 | 批 3 |
| 预报数据集的分区策略 | 未定。前瞻窗口每次拉取都与前次重叠 | 倾向 `snapshot`（按采集日分区，语义正好是「这一天我们看到的未来」）；`daily` 会让同一记录日期被反复覆盖 | 批 2 |
| `case_id` 唯一性 | 未验证 | 剖析阶段实测；定不死去重键，7 天回溯窗口会累积重复 | 批 5 之前 |
| `snow_ban_id` ↔ `mfzv-893p.id` 关联完整性 | 未验证 | 与批 3 的回填同批验证 | Silver 之前 |
| `closed_date` 语义 | 未确认 | 不阻塞——BO-5 已改口径绕开它。**不要单独排期**，与源 contract 一起做 | 若要做 SLA 合规审计 |
| 快照的 Silver 是否逐日存全量 | 未定（沿自上一篇 design doc） | 先逐日全量（2.6 GB/年）；delta 化留到用得上时 | H2 |

---

## 7. 依赖关系

```
0b probe boundaries ─────────────┐
0c revoke key + rm terraform     │  (all independent, start immediately)
0d remove hardcoded credential   │
                                 │
批 1 retire instance ────────────┼──→ 批 2 weather (archive + forecast)
                                 │         │
                                 │         └──→ 批 3 ingest + backfill ──┐
                                 └──→ 批 4 generalise boundary job ──────┤
                                                                         │
                                                              批 5 semantics + Silver
                                                                         │
                                                                    Phase 3 · Gold
```

一句话：**先发起有人参与时延的 0b，同时用批 1 把存量清干净，
然后沿因果链起点（气象）往下打通。**
