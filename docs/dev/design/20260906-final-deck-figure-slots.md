# 定稿 deck 的图位映射、三条补充查询与地图导出通路

> 状态：设计。执行未开始。
> 前置：[20260903-presentation-figure-rendering.md](20260903-presentation-figure-rendering.md)
> —— 那篇定的**图形类型**（§3.3）与**渲染管线**（`scripts/presentation/render_html.py`）
> 在本篇里是既定事实，不重开、不重述。本篇只处理它没有、也不可能有的三件事。

---

## 1. 为什么另开一篇，而不是往 20260903 里加

20260903 篇 §1 白纸黑字写着，「目前唯一的『谁看什么形状』的真实需求来源」是
`UOIP-DayOfData-2026-09-19.pptx`（由 `part1.js` / `part2.js` / `part3.js` 用
pptxgenjs 生成，主线 26 张 + 附录 9 张）。

**那个锚点在 2026-09-06 被替换了。** 用户交付了定稿版
`UOIPDayOfData20260919.pptx`：**43 张**，结构与旧版不同，并且带来三个旧篇里
根本不存在的图 ID —— `FIG-BO2-01b` · `FIG-BO3-00` / `FIG-BO3-00b` ·
`FIG-BO4-01b`。

三条理由，都不是风格偏好：

1. 🔴 **在一篇已上线的设计里换锚点，等于让「当时按什么决定的」不可考。**
   20260903 已有 launch 篇，且 §5 记了三条用户核对后拍板的决策。往闭合的
   上线记录里塞新东西，下一个读的人分不清哪些是 9-03 定的、哪些是 9-06 加的。
   ADR 的规矩（过时了写新的，把旧的标为被取代，不改名不删除）在 design 篇上
   同样成立。
2. **地图是一项新能力，不是渲染管线的补丁。** 20260903 §3.3 的十九图判定里
   **没有任何一张是地图**，`render_html.py` 也没有 geo 的任何痕迹。把它塞进
   「JSON → 自包含 HTML」那条缺口的叙述里，会让那条缺口的边界变得说不清。
3. **本篇要写的一半是新 SQL，那本来是 20260827 篇的地界。** 与其在两篇之间
   来回指，不如在一篇里把「定稿 deck 需要什么」讲完。

**分工一句话：** 20260903 回答「一张表画成什么形状」；本篇回答
「定稿 deck 的哪个图位放哪张图、缺的数据从哪来」。

### 1.1 顺带记两条已发生的漂移（不在本篇修）

| # | 内容 | 归属 |
|---|---|---|
| D1 | 20260903 launch §5 决策 1 定「FIG-BO4-02 维持 `carrier: echarts`，不改 Superset」，但定稿 deck slide 39 把它标成 **SUPERSET**。本篇按「附录卡在会场同样不能依赖活服务」照样出静态 HTML，**不改 SQL 头注** | deck 侧文案，与仓库无冲突 |
| D2 | 20260903 launch §4 的 **L6 在仓库侧已闭合**（companion 已渲染，§3.1.1），deck 侧标注仍在 —— 定稿 slide 41 依旧写 `ECHARTS · FIG-BO6-01 / FIG-BO6-03`，而 FIG-BO6-03 的头注是 `carrier: superset` | 用户，deck 仓库 |

---

## 2. 约束

- **C1（继承 20260903 C3/C7）自包含。** 页面不得在观看时发起任何网络请求。
  地图方案因此不能用任何在线瓦片服务 —— 这条直接砍掉了「拉个底图」这类做法。
- **C2 取数只能由用户在线上执行。** 本仓库这边连不上生产 Trino
  （见 memory `uoip-eda-loop-is-user-driven`）。所以本篇产出的是
  **SQL + 执行命令**，渲染代码等 JSON 回来再写 —— 渲染代码跟着数据走，
  不反过来。
- 🔴 **C3 `silver_weather_archive` 的全表扫描会超时。** 它是日分区表，
  全历史约 6,600 个分区，而 [gold-sql.md R1](../../../.claude/rules/gold-sql.md)
  实测 4,878 个分区的整表扫描在 Trino 的 S3 客户端上 `Read timed out`。
  任何读它的新查询必须带日期谓词，且谓词要打在**分区列** `"date"` 上，
  不是 `weather_date`。
- **C4 不为一张只跑一次的查询增加可失败的依赖。** 详见 §4.3。
- **C5 一个图位不是一个 fig_id。** 定稿 deck 里 slide 11 与 slide 12 是
  同一条 SQL 问两个不同的问题，slide 39 一张卡上放两个 fig_id。这一条已经
  在 `render_html.py` 的 `SLIDE_SLOTS` 里落地为可执行形式。

---

## 3. 方案

### 3.1 图位映射表：`SLIDE_SLOTS` / `UNFILLED_SLOTS` / `ALREADY_IN_DECK`

定稿 deck 的 43 张幻灯片，按「这个图位要不要本仓库出一张 HTML」分成三类，
三类都写进 `scripts/presentation/render_html.py`，单测钉住一张 slide 不能
同时落进两类。

🔴 **`ALREADY_IN_DECK` 这一类是有意存在的，不是凑数。** 没有它，
「本仓库不该出这张图」和「忘了出这张图」长得一模一样 —— slide 25 的 59 个
事件标记就是活例子：早先一版我在这边渲染过它，而定稿 deck 已经用 59 个原生
shape 画好了，两份并存只会让人不知道该信哪个。

**定稿版只有一套编号。** 每条讲者备注都以 `Slide N ·` 开头，N 就是 pptx
序号；旧 v2 草稿需要的「pptx 序号 / 页脚页码」双编号在这一版取消。

#### 已渲染（9 张，源数据 `certified`）

| slide | 图位标签 | fig_id | 说明 |
|---|---|---|---|
| 11 | ECHARTS | FIG-BO2-01 | 均值 + min/max 须，S/C 满饱和 |
| 12 | ECHARTS | FIG-BO2-01b | **同一条 SQL**，去掉均值只留须，shift 1 处一条竖线，K 唯一够不到 |
| 13 | ECHARTS | FIG-BO2-02 | 斜率图，V/M |
| 17 | ECHARTS / MAP | FIG-BO4-01 | deck 首选地图叠加；本仓库出的是它自己点名的非地图替代形式（25 × 15 热力矩阵） |
| 37 | 附录 3 · ECHARTS | FIG-BO2-04 | 散点 + 拟合线，两个系列 |
| 39 | 附录 5 | FIG-BO4-01 | 完整矩阵（deck 标 SUPERSET，见 D1） |
| 39 | 附录 5 | FIG-BO4-02 | 主导份额排序条形 |
| 41 | 附录 7 · ECHARTS | FIG-BO6-01 | 59 × 22 拆两块，**绝不共用色标** |
| 41 | 附录 7 · ECHARTS | FIG-BO6-03 | 卡片点名的 companion：level 分布，两套坐标系（见 §3.1.1） |

##### 3.1.1 `OFFLINE_SUPERSET_SPEC` —— 第四张表，故意不进三桶记账

slide 41 那张卡在正文里点了**两张图**：panel heatmap（FIG-BO6-01，早已渲染），
以及 `Companion: level distribution, faceted by scale — two coordinate systems,
never a shared axis`。companion 一直缺着，就是 20260903 launch §4 的 **L6**。

它的数据不缺 —— `FIG-BO6-03.json` 早就是 `certified` 的冻结数据，7 行，
**不需要跑任何新脚本**。缺的是渲染侧的一个结构问题：`FIG-BO6-03` 的头注是
`carrier: superset`，而 `render_figure` 会拒绝 superset payload，
`FIGURE_SPEC` 又被单测钉死必须**恰好等于** echarts 那一组。

处置：**新开一张 `OFFLINE_SUPERSET_SPEC`，不并入三桶。** 理由是三桶那条
`real == covered` 的断言是「新 `fig_*.sql` 落地没人管」的唯一守卫，往里塞一个
非 echarts 的 id 就把它废了。

🔴 **这不是给 superset 图开的口子，是一条一图一记的人工决定。** `carrier`
回答的是「画成什么形状」，不是「在哪跑」——会场 wifi 不能依赖（C1），
所以 deck 放到台上的 Superset 图照样需要一张离线静态页。列进这张表 =
有人明确说「这张也离线出一份」，因此 `render_figure` 对**其余每一个** superset
payload 仍然照拒，单测 `test_a_superset_figure_not_in_the_offline_table_is_still_refused`
钉死这一点。

🔴 **不改 `fig_bo6_03_*.sql` 的 `carrier:` 头注。** D2 是 deck 侧把一张
superset 图标成了 ECHARTS，改仓库里的头注等于把 deck 的笔误追认为事实。
L6 在**仓库这一侧**到此闭合（图出来了），deck 侧的标注仍然是 D2。

新增图形族 `faceted_level_bars`：两个 facet 各自一个 grid、各自一个 x 轴与
y 轴，**y 轴不设共享 `max`**。分组柱状图是最省事的画法，也是错的——它在一个
轴上把两个 profile 并排，而 CLAUDE.md 的对外禁语②明写
「`load_level` 不得跨 `score_weight_profile` 比较」：CRITICAL 在三因子尺上是
75.0，在两因子尺上是 52.5，不是同一个量。单测
`test_the_two_load_level_scales_never_share_an_axis` 直接断言两个 grid、
两套轴、且 y 轴没有钉死的 `max`。

另一处刻意的取舍：partial 那一侧**不画一根高度为 0 的 CRITICAL 空柱**。
空柱读起来是「量过了，没有」，而真实陈述是「这个 level 在该尺上存在，
当前 0 格到达，最高分 50.27 差 2.23」——所以它是副标题里的数字，不是一根柱子。

#### 待补（7 个，§3.2 与 §3.3 分别处理）

| slide | fig_id | 缺什么 |
|---|---|---|
| 5 | —— | 城市底图，不是我们的任何一张图 |
| 16 | 半个 FIG-BO4-00 | 🔴 ward 轮廓**仓库里没有几何**，见 §3.3.1 |
| 20 | FIG-BO3-00 | 逐日降雪序列没有被冻结过 |
| 21 | FIG-BO3-00b | 同上（同一份数据的第二种画法） |
| 24 | FIG-BO6-01（单事件） | 三个**原始观测值** + 一个人的决定 |
| 18 | FIG-BO4-01b | 分区几何 + 地图渲染通路 |
| 29 | FIG-BO1-03（map form） | 同上 |

#### 不需要本仓库出图（5 个）

slide 25（59 个标记已是原生 shape）· slide 36 / 42 / 43（幻灯片上已有原生
pptx chart 对象）· slide 40（附录 6 全是文字卡 —— 顺带一条：
**FIG-BO3-03 在定稿版里没有图位**，「17 次里 11 次提前开工」那条结论以
文字呈现）。

### 3.2 三条补充查询

已进仓、sqlfluff 绿、已登记进 ledger（状态「🚧 SQL 已进仓，未取数」）、
已放进 `render_html.py` 的 `NOT_YET_IMPLEMENTED`。

| 文件 | 图位 | 返回什么 |
|---|---|---|
| `fig_bo3_00_daily_window.sql` | slide 20 / 21 | top 5 候选窗口 × 14 天的逐日降雪 + 累计值 |
| `fig_bo6_00_single_case.sql` | slide 24 | 374 个 scored 格全部，带三个原始观测值 + `distinctness_rank` |
| `fig_bo4_00_zone_geometry.sql` | slide 18 / 29 | 25 行分区 WKT + 排班/修复标记 |

执行（宿主机 shell 必须加前缀，`.env` 里的 `trino:8080` 是给 Airflow 容器的视角）：

```bash
TRINO_HOST=localhost TRINO_PORT=8090 make eda-export ONLY=FIG-BO3-00,FIG-BO6-00,FIG-BO4-00
```

三条各有一个值得写下来的取舍：

1. 🔴 **BO3-00 让 SQL 自己选窗口，不写死日期。** deck 的要求是「用真实存档
   窗口，不要画虚构天气」，而合格窗口要同时含有几个小雪日、一个 0 cm 间隔日、
   以及间隔日之后重新开始的降雪 —— 这三条是可判定的，所以写成 `WHERE`
   而不是靠人肉挑一段日期。扫描面按 C3 钉在 2021-11 → 2023-04：
   2021-2022 是十八冬最重的一季（FIG-BO3-02 实测 11 事件 / 106.2 cm），
   雪日多则候选多，不必扩面。五个候选都不合适就改那两个日期重跑。
2. 🔴 **BO6-00 返回全部 374 格，排序但不过滤。** deck 自己的验收条件是
   「三个因子齐全、视觉上分得开、**没有未结的 DQ finding**」，最后一条 SQL
   判断不了，所以挑哪一格是人的决定。`distinctness_rank` 只衡量三个数在视觉上
   是否分得开，**与这一格是否典型无关** —— 让 SQL 做「优选」就是把一个
   人的判断伪装成一个计算结果。
3. 🔴 **BO6-00 取的是原始观测值，不是三个 factor。** factor 是加权归一化后的
   贡献值（0–1），放到台上会被读成「降雪 0.14 cm」。三个原始数各来自一张表、
   粒度也不同（事件级 / 事件 × 分区 / 犁雪作业 × 分区），放在一张图上是有意的
   收敛，但它们不是同一个观测 —— deck 自己的话是「三个观测，不是一个原因」。

### 3.3 地图导出通路（slide 18 / 29）—— 本篇唯一的新能力

`dim_plow_zone.geometry_wkt` 有 25 行 MultiPolygon，所以**几何是有的**，
不是「哪也画不了」。缺的是 WKT → 可渲染形式这一段。两条路：

| | ECharts geo（`registerMap` + 内联 GeoJSON） | 静态导出（QGIS / matplotlib → PNG） |
|---|---|---|
| 自包含 | ✅ GeoJSON 内联，与其余 8 张一致 | ✅ 图片本来就是静态的 |
| 新代码 | 一个 map family + 一条 WKT→GeoJSON 转换 | 一个一次性脚本 |
| 交互 | 有 tooltip（这两张**不需要**） | 无 |
| 配色控制 | 受 `visualMap` 约束 | 完全自由 |

**倾向静态导出。** slide 18 是一个放大裁切、slide 29 是单色阶填充，两张都
不需要 tooltip；而 slide 29 还带着 deck 的开放项 O3（填色地图是否越过项目
自己划的「不画路况地图」那条线），先出静态图更容易拿来讨论。

两条画的时候不能违反的纪律，都来自数据本身：

- 🔴 **`has_plow_schedule = false` 的 3 个分区只画轮廓、不填色。** 它们是
  没有排班数据，不是负荷为零。填成最浅的那一档就等于说「这里最闲」。
- 🔴 **slide 29 的图例只能写 "estimated winter-related resident reports"**，
  不能写 "load"，更不能写 "priority"。这是 deck 自己写死的措辞，且它是
  承重的 —— 这张图是全场离「我们不预测路况」那条线最近的一张。
- `geometry_repaired = true` 的 8 个分区面积被 `make_valid` 改过，
  改动量在 `area_delta_pct` 里，不静默处理。

### 3.3.1 🔴 ward 几何不存在 —— slide 16 只有一半能画

slide 16 要并排两张纯轮廓图：15 个 ward，25 个 plow zone，同一范围、同一比例尺、
同一投影。**右半边有数据，左半边没有。**

`dim_plow_zone.geometry_wkt` 是全仓库**唯一**带几何的 Gold 列
（`silver_plow_zone_boundary` 是它的上游）。ward 这一侧，`dim_admin_label`
只有 `label_type` / `label_id` —— **ward 有名字，没有形状**。
`dim_region_crosswalk` 的 548 行**根本不是从几何算的**——`weight` 是
`该 (分区, 选区) 的冬季工单数 / 该分区冬季工单总数`（DML 里就是一个
`COUNT(*)` 除以窗口和，口径 2023-11 → 2026-05）。它从头到尾没有碰过 ward 几何，
自然也不能反推回边界。

所以这不是「导出通路还没写」，是**上游从来没接过 ward 边界数据源**。
这与 slide 17 / 39 的 ward × zone 矩阵不矛盾，而且比原先想的更不矛盾：
那张图要的是**工单占比**这个数，crosswalk 里有，且它压根不需要几何；
slide 16 要的是**形状**，没有。

🔴 **附带修掉一处贯穿三页的口径错误（2026-09-08）。** slide 17 / 18 / 39 此前把
这一列讲成「面积占比」，`fig_bo4_01` 的列别名也叫 `area_share`，slide 39 上还写着
"Area-weighted overlap"。**四个数（V 的 10 个选区、26.0%、中位 54.0%、T/N 两个分区）
全部出自这一列，全部是工单份额而不是面积份额。** 已改：列别名 →
`request_share`，三份 SQL 的 caption / must_not_say、`render_html.py` 的英文
caption、以及 pptx 上三页正文 + 两页讲者备注。结论本身不受影响——按工单份额讲
「按 ward 打分会把一条作业路线的工作量拆开」反而比按面积讲更贴题。

同一条约束打在 slide 18 上：它是「zone V 放大，ward 边界穿过它」，
ward 边界同样缺。§3.3 的两条路（ECharts geo / 静态导出）都只解决 plow zone 那半边。

补 ward 几何要新接一个源（Winnipeg Open Data 有 ward boundary 数据集），
按批 3 的形状走：一份 source YAML + 一个 `backfill_*.py` + 一份 contract。
**这是 H1 之外的工作量，不在本篇范围内**，记为 O5。

---

## 3.4 deck 侧需要维护的措辞（slide 20 / 21）

> 对象：`var/presentation/UOIPDayOfData20260919.pptx`（定稿版副本，2026-09-07 存入）。
> 本节记的是**幻灯片正文与讲者备注的文字**，不是仓库代码。图已经能画，
> 需要动的是话怎么说。

### 3.4.1 事情的经过

FIG-BO3-00 第一次取数回来 **0 行**，查下去发现两层原因，第二层才是要紧的：

1. `silver_weather_archive` 在 metastore 里**零个分区** —— Spark 走 s3a 直接写文件
   不经过 Hive Metastore，Trino 侧是**假 0 且不报错**。跑
   `CALL hive.system.sync_partition_metadata(..., mode => 'FULL')` 之后回来
   **9,747 个分区，2000-01-01 → 2026-09-07，一天不缺**。与 20260817 篇 §4
   记的 `silver_service_request` 同一个机制，见本篇 O7。
2. 🔴 **slide 21 描述的那张图，在存档里没有纯粹形态。**

### 3.4.2 slide 21 的措辞与数据对不上

卡片现在写的是：

> Left: several days that **each fall below the single-day threshold** — visually unremarkable.
> Right: the same days as a cumulative line **crossing the accumulation threshold**.

而规则框写的是 `a single day of 3 cm or more · or · 10 cm or more over the trailing 10 days`。

把这两句合起来，要求的是一个**十天内累计到 10 cm、且这十天没有任何一天到 3 cm**
的窗口。**2021-11 → 2023-04 实测这样的窗口是零个。**

原因在数据本身，不在扫描范围：那 8 个 `accum_flag = true` 的事件，
**全部是踩着一个越过单日阈值的日子累起来的**。最典型的 `SNOW-20221115`
（单日 0.21 cm）之所以成立，是因为它前十天累计 11.0 cm —— 而那十天里
**有一个 3.71 cm 的日子**（2022-11-10）。`accum_flag` 的定义是
「**事件自己的**那几天里峰值不到阈值」，滚动窗口却会把事件之前的日子算进来，
两者并不矛盾，但它意味着「每一天都很小、累计却越线」是一幅**没有实例的图**。

**已定的处置（选项 A）**：`accumulation_only` 的判据放宽成
「窗口内 max 单日 < 3 cm **且这十四天累计 >= 10 cm**」。首选窗口
**2022-12-16**：max 单日 **2.66**（确实全部低于 3），十四天累计 **12.81**，
第 12 天越过 10。图上每一根柱、每一个累计点都是真实存档，没有虚构。

🔴 **代价必须记住：这个窗口在生产规则下不会产生事件。** 它的严格十天累计是
**9.45**，差 0.55 不到线。SQL 因此照常返回 `max_trailing_10d_cm` 这一列 ——
放宽的是选窗口的判据，不是对外的说法。

### 3.4.3 要改的三处

| # | 位置 | 现状 | 改成 |
|---|---|---|---|
| W1 | slide 21 卡片第二行 | `crossing the accumulation threshold` | `crossing the accumulation threshold` **保留**，但删掉/改写规则框与图之间的暗示关系 —— 图上的累计是**这十四天**的，规则量的是**十天**，两个数并排出现而不说明，等于请人去算 |
| W2 | slide 21 讲者备注 | 只讲「小雪日会累加」 | 末尾加一句自保：**「这十四天是用来演示累加这件事的，它本身不是那 8 个事件之一。」** |
| W3 | slide 21 底部断言 | `8 of the 99 events exist only because of the accumulation rule — no single day in them ever crossed the line.` | **这句是真的，不要改** —— `accum_flag` 就是这么定义的。但它紧挨着上面那张图，读者会把两者当成同一件事，所以 W2 那句自保是必须的，不是可选的 |

🔴 **绝对不能说的三句**（讲者被问到时最容易脱口而出）：

- 「这就是那 8 个事件之一」—— 不是。
- 「按我们的规则，这十四天会被判为一次降雪事件」—— 不会，差 0.55 cm。
- 「把单日阈值调低就能救回这些事件」—— 这条已经被探针证伪过
  （`plow_without_snowfall`：21 日累计保留了对照组 76%，单日峰值只保留 26%），
  而 slide 21 的讲者备注**已经正确地写了**这一条应答，保持原样。

**被问到时的正确答法**：这十四天里没有一天达到单日阈值，而它们加起来超过了
累积阈值的量级 —— 这就是我们为什么要加第二条判据。至于这一段本身会不会被判为
一次事件，取决于滚动窗口取十天还是十四天，那是规则的参数，不是这张图要讲的事。

### 3.4.4 slide 20 不需要改

首选窗口 **2022-11-06**：`2.66 · 0.21 · 0.28 · 0 · 3.71 · 2.24 · 0.42 · 0 ·
0.28 · 0.21 · 0.91 · 1.47 · 0.98 · 0.77` —— 三段降雪被两个 0 cm 日隔开，
正是卡片要的「a few small snow days, one clear gap day, then snow again」，
也正好让「一场、两场还是三场」这个问句成立。这张图**不画阈值线也不画事件边界**，
所以里面那个 3.71 的日子不会被读成「越线了」，它只是一根高一点的柱子。

---

## 3.5 统一视觉主题（2026-09-08 增补）

起因是一句评价：这些图「像 CAD 二维草图」，不如 Grafana 那种看着高大上。
诊断成立——此前十一张图用的全是 ECharts 出厂默认：四面轴线、实心灰网格线、
12 px 以下的字号，谁都没决定过它长什么样。

处置是**一份注册一次的主题** `DECK_THEME`（`scripts/presentation/render_html.py`），
经 `echarts.registerTheme` 注入页面，再由 `echarts.init(el, 'uoip')` 应用。
放在主题里而不是各 builder 里，是为了让 builder 只说**数据的意思**
（哪根是重点色、哪个格是主导 ward），不说它长什么样。

### 3.5.1 两条不能照抄 Grafana 的地方

1. 🔴 **主题是浅色的，而参照的 Grafana 面板是深色的。** deck 自身是近白底
   + 藏青字，深色图落到幻灯片上是一个洞。深色**不是更好的品味**，它是
   「在暗房里盯屏幕」这个不同的媒介。要改成深色得先改 deck。

2. 🔴 **没有按数值取色的色阶**，无论 Grafana 那条绿→黄的条形有多好看。
   slide 11 自己的卡片写着「Colour must carry no value judgement —
   no red-for-bad. One hue, two saturations.」按量级取色等于宣称
   「负荷分数高 = 情况不好」，正是 BO-6 上线记录禁止的读法。
   主题里的渐变**同色相**（同一 hex 的 82% → 100%），只给条形一点体积，
   不编码任何东西。单测 `test_the_theme_encodes_no_value_in_colour` 与
   `test_bar_washes_stay_inside_one_hue` 钉死这两条。

### 3.5.2 顺带修掉的三个真缺陷

主题本身是装饰，但配它时暴露了三个不是装饰的问题：

- 🔴 **FIG-BO2-01（slide 11）此前是一张退化的箱线图**：均值 + [min, max]
  没有四分位数，实现却把 q1=median=q3 压到均值上。而**零高度的箱就是一条线**,
  所以整张图除了发丝线什么都没有，幻灯片要讲的那个排序在页面上没有形体——
  「CAD 草图」的说法首先指的就是这一张。它还顺带邀请了一种错读：
  懂箱线图的人会看到从未计算过的四分位数。改成**真条形 + `markLine` 画范围**。
  `markLine` 走数据坐标、不参与条形布局，两个毛病都够不着。
- 🔴 **入场动画必须关掉。** PNG 按钮是在点击那一刻读画布的，点在那大约一秒
  的条形生长里，导出的就是一张半截图——每根条都标着正确的数字、画着错误的
  长度。`DECK_THEME["animation"] = False`，单测 `test_charts_do_not_animate`。
- 🟡 **轴名被裁掉。** ECharts 默认把轴名停在轴的尽头，那里越出 grid 被画布
  边缘切断：`address count` 到了幻灯片上是 `ad`，`dominant share` 是 `do`。
  居中是唯一不会被右边缘截断的位置。

> ⚠️ 记一条诊断教训：排查 slide 11 时连续三轮把「条形只画出五个像素」判成
> 布局 bug，还据此写了两段「实测」注释。**真因是截图拍在了入场动画中间。**
> 结论反过来支持了上面第二条——一个能骗过人眼判断的动画，同样能骗过 PNG 导出。

## 3.6 一个图位两张图：slide 39 / 41（2026-09-08 修正）

🔴 **§3.1 的图位表把 slide 39 和 slide 41 各算成了两个图位，而 pptx 上各只有
一个图框。** 实测（`python-pptx` 读 `var/presentation/UOIPDayOfData20260919.pptx`）：

| slide | 图框 | 占位文字 |
|---|---|---|
| 39 | `TextBox 26` L=0.62 T=1.90 **W=6.80 H=4.10** | `FIG-BO4-01 + FIG-BO4-02` |
| 41 | `TextBox 26` L=0.62 T=1.90 **W=6.60 H=4.10** | `FIG-BO6-01 + FIG-BO6-03` |

占位文字自己写的就是「A + B」——deck 要的是**一个框里两张图**，而渲染器给了
两张各自按整框尺寸导出的图。两张图都按 6.8×4.1 导出，插进去当然放不下。

处置：图位表改为**一条**，用 `with_fig_id` 指出同框的第二张图，
`spec.family` 指向新的合成 builder。渲染时把 companion 挂到 payload 上
（`payload["companion"]`），builder 仍然只收一个对象。

### 3.6.1 slide 39：共用一条 y 轴，不是并排两张

FIG-BO4-01（25×15 工单份额矩阵）与 FIG-BO4-02（25 个分区按最大 ward 份额排序）
**索引的是同一个东西——plow zone**。所以合成后只有一套顺序、每个分区一行：
横着读一行，右边的条说这个分区有多集中，左边的矩阵行说它摊在几个 ward 上。
按 dominant share 排序让 slot 自己那句「the median and the tail are both
visible」对两半同时成立。**这不是妥协排版，比原来两张分开更能说事。**
右轴不重复分区名——6.8 in 里把 25 个名字写两遍，代价是矩阵的宽度。

### 3.6.2 slide 41：四张子图，四把尺，没有一把是共用的

FIG-BO6-01 的两个 block（左）+ FIG-BO6-03 的两个 facet（右上/右下）。
🔴 **加了两张子图不放松任何约束**：每个 block 的 `visualMap` 仍按自己观测到的
max 缩放，每个 facet 仍有自己的 y 轴。整张卡片存在的理由就是
「一把尺上的 CRITICAL 不是另一把尺上的 CRITICAL」。
🟡 两个 block 的 59 个 event id **不再标注**——那个尺寸下它画成 7 px 的糊字，
是图本身兑现不了的精确度承诺；block 读的是纹理（面板里哪种颜色占多少），
数字在旁边的柱子上。

单测三条钉死：`test_a_slot_never_claims_a_deck_box_twice`（一个 slide 只出现
一次）· `test_a_two_figure_box_names_a_companion_that_exists` ·
`test_a_composite_refuses_to_draw_half_of_itself`（没有 companion 必须 die，
不能安静地只画一半——**画了一半的附录看起来是完成的**）。

---

## 3.7 pptx 自带图表的数据核对（2026-09-08）

deck 里有三个**原生 pptx 图表**（不是插图），另有 0 张原生数据表。逐个对
`var/presentation/outputjson/` 的线上导出核过：

| slide | 图表 | 对照 | 结论 |
|---|---|---|---|
| 36 | 五个班次的格数 `[115, 131, 128, 25, 19]` | FIG-BO2-03（418 行）| ✅ **逐值相同** |
| 42 | MAE `23.628 / 7.345 / 7.919` | FIG-BO1-03（308 行）| ✅ **逐值相同** |
| 43 | 六个冬季类别 `[33639, 4293, 4074, 3127, 847, 0]` | **没有对应的 fig SQL** | ⚠️ 见下 |

同时核了几张卡片上的数字，全部对得上：slide 36 的 89.5% / shift4=6 区 /
shift5=5 区 / 只有 K 从未进 shift 1 / 49 条禁令按 type 11+19+19、只有 type 4
的 19 条匹配上；slide 39 的中位 54.0%（25 区）与 53.5%（22 个有排班的区）、
只有 T 和 N 完全落在一个 ward 内、10 个区没有过半 ward、V 跨 10 个 ward 最大
26.0%；slide 41 的 1,298 = 374 + 924、71.2%、最高 partial 分 50.27。

### 3.7.1 slide 43 的类别计数没有 frozen 导出

`sql/presentation/` 里**没有**产出这六个冬季请求类别计数的查询，所以这张原生
图表**无法对 outputjson 核对**。数字可追到
`docs/dev/launch/20260827-bo-eda-and-presentation-sql-launch.md` §B7
（`SNOW 33,639`、六个 category 合计 45,980），那是一次真实的生产实测，
**不是模板示例数据**——但它没有跟着这一轮重跑。

✅ **`sql/presentation/fig_bo1_04_winter_categories.sql` 已补（2026-09-08）**，
`fig_id: FIG-BO1-04` 已进台账 §5.2。它按 `is_effective = TRUE` 过滤
`dim_winter_category`（6 个生效类别，`PLOUGH` 是可移植性行、匹配 0 行 Winnipeg 数据），
在 `fact_service_request_zone_event` 上聚合，输出
`winter_category / cells / requests / cells_with_any_request / weighted_requests /
pct_of_all_requests`。**可测的列是 `requests` 不是 `cells`**——F1 是完整笛卡尔积
（13,068 = 99 事件 × 22 分区 × 6 类别 = 每类别 2,178 格），所以任何 `cells = 0`
的门禁永远不会触发。

⚠️ **它还没有跑过**，因此这张图的口径仍然是 2026-08-27 的那次运行。O9 现在问的
不再是"要不要写"，而是"要不要现在跑并冻结"。

### 3.7.2 🔴 slide 42 讲稿有一处措辞与当前导出对不上

slide 42 正文写着「a quarter are 0 and the median is 2.9, against a maximum
of 381」。当前 FIG-BO1-02 导出的是：

```
cells 1298 · zero_cells 390 · zero_pct 30.0 · p25 0.1 · median 2.9 · max_count 381
```

median 与 max 对得上，**「a quarter are 0」不对**：实测 **30.0% 恰好为 0**，
而 25 分位是 **0.1 而不是 0**。此前台账里「25 分位 = 0」的说法在当前导出上
已不成立。措辞应改为「**30% are 0**」——它比原句更强，且是当前导出里真有的数。
列为 **W3**，与 §3.4.3 的 W1/W2 一并在 deck 侧处理。

## 4. 被否决的选项

1. **用 Superset 补那 5 个填不了的图位。** Superset 读的是同一个 Trino，
   拿不到多余的数据；而且它是活服务，与 C1 直接冲突。它既不提供缺的东西，
   也不满足离线要求 —— 这五个图位缺的从来不是「一个画图工具」。
2. **把这些内容写进 20260903 篇。** 见 §1。
3. **BO3-00 写死一段日期。** 那就把「这个窗口凭什么合格」变成一句无法复核的
   断言。写成判据之后，换一季重跑仍然成立。
4. **BO4-00 里加一列 `ST_Area` 做 sanity check。** WKT 是经纬度，
   面积单位是「平方度」，没有意义；给一条只跑一次的查询多加一个可能失败的
   函数依赖不划算（C4）。

---

## 5. 验收判据

| # | 判据 | 怎么验 |
|---|---|---|
| A1 | 三条新 SQL 在生产上各跑出一个 `var/presentation/FIG-BO{3-00,6-00,4-00}.json`，`certification.status` 非 `unknown` | 用户执行 §3.2 的命令 |
| A2 | BO3-00 至少返回 1 个候选窗口，且该窗口人工看下来确实「像三场雪也像一场雪」 | 人工核对 5 个候选 |
| A3 | slide 24 的那一格被选定，且 event id / zone / etl_run_id / 认证状态写进 deck 备注 | deck 侧，本仓库只提供候选 |
| A4 | slide 18 / 29 两张地图产出，且 3 个无排班分区只有轮廓、slide 29 图例措辞与 §3.3 一致 | 人工核对 |
| A5 | `SLIDE_SLOTS` ∪ `UNFILLED_SLOTS` ∪ `ALREADY_IN_DECK` 三集互不相交，且新 fig_id 不落在任何桶外 | `make test-unit`（已有单测） |
| A6 | 每个新产出的 HTML 仍然自包含 | `test_render_is_self_contained`（已有） |

---

## 6. 开放项

| # | 内容 | 归属 |
|---|---|---|
| O1 | slide 18 / 29 走 ECharts geo 还是静态导出 —— §3.3 给了倾向，**仍未拍板**。O2 定案后倾向更明确：要在每个分区上印数字，静态导出更容易控制排版 | 未定 |
| O2 | ~~slide 29 的填色地图是否越线~~ ✅ **已定（2026-09-08）：画，但每个填色分区上印出预估数**。判据是「内容不越线、形式越」——浅到深的城市填色图是路况/优先级图的视觉语法，印上数字后它读作贴在地图上的数据表。三条纪律（图例措辞、3 个无排班分区只画轮廓且不标数、8 个 `geometry_repaired` 分区不得静默处理）已写进 slide 29 讲者备注 | 已定 |
| O3 | 🔴 **`var/presentation/outputjson/` 是那 19 份 `certified` JSON 的唯一副本，且 `var/` 不进版本控制。** 重跑不保证复现（Open-Meteo 会回修历史存档、`segment_events` 会重切 —— CLAUDE.md 记过 F1 非零格从 916 漂到 908 就是这个机制）。要不要给它一个更结实的落点 | 未定 |
| O4 | FIG-BO3-03 在定稿 deck 里没有图位，它的 HTML 要不要继续维护 | 未定 |
| O5 | ~~slide 16 / 18 缺 ward 几何~~ ✅ **已定（2026-09-08）：走第三条——deck 侧自备底图**（Winnipeg Open Data 的 ward boundary GeoJSON，QGIS 出图），**只作示意、不 join 任何数据**。两页都解锁，不删页。接新源那条路仍然成立，但确认为 H1 之外。🔴 约束：两页讲者备注必须写明该轮廓是外部素材、仓库无 ward 几何、deck 里没有一个数字来自它（§3.3.1） | 已定 |
| O6 | slide 5 的城市底图不是我们的图，由谁提供 | deck 侧 |
| O7 | 🔴 `silver_weather_archive` 的 9,747 个分区此前对 Trino **完全不可见**，说明 `etl_weather_archive` 每天写完新分区都没人同步 —— 读这张表的任何看板都是空的**而且不报错**。20260817 篇 §5 记过是「遗漏的常规步骤」但没落地。要不要进 DAG | H1 之后，未定 |
| O8 | W1/W2/**W3** 三处 deck 措辞（§3.4.3、§3.7.2） | deck 作者 |
| O9 | ~~要不要补 `fig_bo1_04_winter_categories.sql`~~ **SQL 已补（2026-09-08）**。剩下的问题是要不要在 9-19 之前跑一次并冻结，好让 slide 43 的六根柱子对得上一份导出，而不是对着一个月前的上线记录（§3.7.1） | 待跑 |
