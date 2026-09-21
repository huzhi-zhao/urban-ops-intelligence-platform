# 政策文件考古探针 上线记录

> **Date**: 2026-08-31 · **Design**: [../design/20260831-policy-document-research-probe.md](../design/20260831-policy-document-research-probe.md)
> **Result**: ✅ **Q1/Q2/Q3 三问全部命中**（2026-08-31 第二轮，出站已通）——
> G1/G2/G3/G5 达成，G4 已触发但按设计留给 H1 由人执行。
> 🔴 第一轮（2026-09-01 记）预检不通的记录保留在 §1.2，不要读成本篇的结论。

## 0. 为什么提前开篇

与 L3 那篇同一条理由，而与其余七篇都不同：**不是变更不可逆，是结论只在采集的那一刻存在。**

市政网站改版、撤稿、换链接是常态。一条今天能打开的 URL，三个月后可能 404，
而那时无法自证当初读到的是什么。因此：

- **快照先于引文**。§3 台账里没有 `快照` 一列的行，等于没查过。
- **检索日与 URL 同等重要**，两者缺一不可。
- 台账**在查的当时逐条填**，不在事后回忆着补。

本次**没有任何一步不可逆**，也不触碰生产数据：只读公开文档，不打 SODA、
不碰 MinIO / Trino / Silver。唯一会改动仓库既有文档的动作是 design G4，
且只在 Q3 命中时发生。

---

## 1. 执行结构：H0 → M → H1

分工判据与批次设计见 [design §3.4](../design/20260831-policy-document-research-probe.md)。
**人只出现在头尾，中间三小时无人值守。**

### 1.1 H0 · 人 · 5 分钟

- [ ] 批准 design（本篇 §4 的门禁与 §3 的字段要求随之冻结）

出口：design 状态为 Accepted。**H0 结束即可离开**，M 批不依赖任何外部答复。

### 1.2 M · 机器 · ≤ 3 小时（无人值守）

| 时刻 | 段 | 动作 | 结果 |
|---|---|---|---|
| 2026-09-01 | 预检 | 试取目标域名 | 🔴 **不通,当场终止** |
| 2026-08-31 | 预检（第二轮） | 同一 URL 原样重试 | ✅ **通** |
| 2026-08-31 | A | —— | **跳过**：四个 URL 上一轮已定位 |
| 2026-08-31 | B | 取一手件、逐字摘引、存快照 | ✅ 三问命中，见 §3 |
| 2026-08-31 | C | 填台账、裁决草案、自评门禁 | ✅ 见 §3–§4 |

> ⚠️ 日期看起来是倒的：第一轮记为 2026-09-01，第二轮是 2026-08-31。
> **不要"修正"成顺序**——两个日期都照记录原样保留，改哪一个都是编造。

### 预检实测（2026-09-01）

| 试法 | 结果 |
|---|---|
| `WebFetch` `www.winnipeg.ca` | `EGRESS_BLOCKED` |
| `WebFetch` `legacy.winnipeg.ca` | `EGRESS_BLOCKED` |
| `curl` `legacy.winnipeg.ca` | `CONNECT tunnel failed, response 403` |
| `curl` `winnipeg.ca` · `clkapps.winnipeg.ca` · `web.archive.org` | 全部 `000` |
| **Chromium**（headless，同一代理） | `ERR_TUNNEL_CONNECTION_FAILED` |
| `WebSearch` | ✅ 通 |

代理自陈：`connect_rejected · gateway answered 403 to CONNECT (policy denial)
· legacy.winnipeg.ca:443`。

🔴 **这不是登录墙、验证码或注册墙——是本环境的出站策略拒绝该域名。**
因此**换浏览器不解决**：Chromium 走同一条隧道，撞同一堵墙，
错误页里没有任何可点的东西。这一条值得单独记下来，因为
「抓不到就开浏览器手动点」是一条看起来总该有效、而在本环境下**先验无效**的退路——
判据是错误发生在 `CONNECT` 阶段，早于任何页面内容存在。

🟡 **`WebSearch` 通,但它救不了本探针。** 它返回的是检索服务对页面的**摘要**，
不是页面原文，满足不了 §3.3 的「逐字引文 + 页码 + 快照」。
把它当命中记账，等于用一条无法复核的转述去修一句无出处的转述——
正是 design §1 要解决的那个毛病。它只能作**线索**，见 §3。

🔴 **中途不提问。** 遇到需要人判断的分叉，两条都记进 §3 并标
`⚠️ 待 H1 裁决`，继续往下跑。

🔴 **唯一允许中途停下的情况是预检不通**（出站被代理拦、市政站点取不到）。
那时**立刻退回 H0**，不要在不确定网络能力的前提下跑满三小时再报告失败。

### 1.3 H1 · 人 · 20 分钟（一次坐下，把判断一起做完）

- [ ] 逐条看 §3 里标 `⚠️ 待 H1 裁决` 的边缘一手性判定
- [ ] Q3 命中？→ 决定是否执行 G4（改 `business-objectives.md` §987）
- [ ] Q1/Q2 采纳 / 放弃
- [ ] design §6 的开放项各给一个状态

### 1.4 回滚点

**全程可退**，没有一步产生不可逆后果：只读公开文档，不打 SODA、
不碰 MinIO / Trino / Silver。唯一改动仓库既有文档的动作是 G4，
它在 H1 由人执行、单独一次改动。

---

## 2. 与设计的偏差

| 设计怎么写的 | 实际怎么做的 | 为什么改 |
|---|---|---|
| 预检不通 → 退回 H0，等人裁决「放开出站 / 人工取文」 | **2026-08-31 复跑预检，`legacy.winnipeg.ca` 已通**，M 批未经人工裁决即按 §5「恢复入口」继续 | 分叉的前提消失了。§3 第 1 条分叉问的是「怎么绕过出站封锁」，封锁已不存在，两个选项都不必选。裁决记为**已失效**而非「选了 A」——没人改过策略，是环境变了 |
| 段 A（定位，60 分钟） | **跳过**，按 §5 第 2 步 | 四个 URL 上一轮已定位，这正是上一轮留下的产出 |
| §3.2 把「一手政策 PDF」当作检索目标 | 实际的一手载体是 **DMIS**（`dmis.winnipeg.ca`，市政决策信息系统），policy PDF 与 1979/1987 两份会议记录都在那里，且互相带链接 | 上一轮定位到的是 `legacy.winnipeg.ca` 的 HTML 页；那页脚有一条指向 DMIS 的链接，一手文件在链接的另一端。**HTML 页的价值不是内容，是它指向了哪里** |
| 引文从 HTML 页摘 | **引文全部取自 PDF**，HTML 页只用于交叉核对 | §3.2 把公众页列为第 3 类（二手措辞）。既然一手件到手了，就没有理由用二手的 |

🔴 **一处方法上的坑，值得单独记：`PW-001.pdf` 的文字层用了偏移编码，
`pdftotext` 能还原字母（每字符 +29），数字却全部丢失。** 表现是
「thirty-six hours」这种拼写出来的数完好，而「3 cm」「1,000」「1987」直接变成空白——
**不报错，看起来只是排版松散**。本轮所有数字与日期因此改为**渲染成图像后逐个核读**
（`pdftoppm` → 人眼/视觉核对），没有一个数字来自文字层。
判据留给下一轮：**PDF 抽出的文本里如果一个数字都没有，那不是文件没数字，是抽错了。**

---

## 3. 证据台账

**一条证据一块，六个字段缺一不可**（design §3.3）。逐字引文，不转述、不翻译。

> 主件是同一份文件：**City of Winnipeg Policy No. PW-001, "Snow Clearing and Ice
> Control"**，office consolidation dated July 18, 2024，28 页。
> 快照 `var/research/snow-policy/PW-001-snow-clearing-and-ice-control.pdf`（52 MB）。

### Q3 · P1–P3 街道等级的官方定义 —— ✅ 命中

```
状态: 命中
引文: “Priority I Streets” means streets that can be designated as being major
      thoroughfare and are defined by the criteria contained in Appendix A and
      by and large includes Regional Streets.
      “Priority II Streets” means streets that are defined by the criteria
      contained in Appendix A and generally include transit routes not included
      in Priority I and residential collectors.
      “Priority III Streets” means all remaining streets not included in the
      Priority I and Priority II categories and includes residential back lanes.
出处: City of Winnipeg Policy No. PW-001, "Snow Clearing and Ice Control",
      office consolidation July 18, 2024 · §2.1–2.3, Page 1–2 ·
      https://dmis.winnipeg.ca/DownloadCouncilPolicyDocument/3514/PW-001.pdf
检索日: 2026-08-31
一手性: 一手政策（City Council 通过的政策正文）
快照: var/research/snow-policy/PW-001-snow-clearing-and-ice-control.pdf
      + var/research/snow-policy/page-01.png · page-02.png · page-03.png
      · appendixA-13.png（数字核读用：文字层丢数字，见 §2）
```

**定义指向 Appendix A，而 Appendix A 也在同一份文件里**（Page 13，
"4.1 Appendix A ... (Approved by Council September 30, 1987)"，
标题 `Street Classification Criteria`）。这是本轮最有价值的一条：
分级不是描述性的，它有**成文的客观判据**。逐字抄录该表（数字经图像核读）：

| Classification | No of Lanes | Min. Average Daily Traffic Volume | R.O.W. Width | Roadway Width | Parking | Sidewalk |
|---|---|---|---|---|---|---|
| **Priority I** (Regional Streets) a) Divided | 4 | 4,000 | 100 – 400 feet | Variable | Restricted | Variable |
| **Priority I** (Regional Streets) b) Undivided | 4 | 4,000 | 66 – 100 feet | Variable | Restricted | Variable |
| **Priority II** (Bus routes, collectors) | 2 | 800 | 66 – 80 feet | 24 – 46 feet | One side | Yes |
| **Priority III** (Residential and Lanes) | 2 | Less than 800 | 50 – 66 feet | 14 feet | One side | Variable |

顺带结清 design §1325 的待办（**顺手，不是目标**）——承诺时限的逐字原文：

```
引文: The snow plowing operations shall be completed within thirty-six hours
      following the end of an average storm.            [P1 · §3.1 A · Page 2]
      The snow plowing operations shall be completed within thirty-six hours
      following the end of an average storm.            [P2 · §3.1 A · Page 3]
      The snow plowing operations shall normally be completed within five
      working days following the commencement time of the plowing effort on
      Priority III streets.                             [P3 · §3.1 C · Page 3]
出处/检索日/一手性/快照: 同上（PW-001，Page 2–3）
```

🔴 **P1/P2 的时限措辞是 `shall be completed`，P3 是 `shall normally be completed`
且起算点不同**（前者「风暴结束后」，后者「作业开始后」）。三条不是同一种承诺，
做 SLA 合规审计时不能并成一列算。

### Q1 · 分级制度的建立年份 —— ✅ 命中（但有两个日期，见裁决项 #1）

```
状态: 命中
引文: POLICY TITLE: Snow Clearing and Ice Control · ADOPTED BY: City Council ·
      EFFECTIVE DATE: September 19, 1979 · CITY POLICY NO: PW-001 ·
      MOST RECENT CONSOLIDATION: July 18, 2024
      —— 而定义一节另行标注：
      2. Definitions: (Approved by Council September 30, 1987)
出处: PW-001, office consolidation July 18, 2024 · 封面表格与 §2 抬头, Page 1 ·
      https://dmis.winnipeg.ca/DownloadCouncilPolicyDocument/3514/PW-001.pdf
检索日: 2026-08-31
一手性: 一手政策
快照: var/research/snow-policy/PW-001-snow-clearing-and-ice-control.pdf · page-01.png
```

DMIS 的政策记录页（`ViewCouncilPolicy?councilPolicyId=3514`，快照
`dmis-council-policy-3514.html`）与之一致：`Council decision: Sep 19, 1979 Minute 1751`，
其后 18 条修订，最早两条同为 `Sep 30, 1987`（Minute 1584 与 1586）。

🔴 **1979 那条不是分级的起点。** 已取到 1979-09-19 的会议记录原文
（快照 `council-decision-1979-minute-1751.pdf`，标题
`Snow Removal Procedures`，File SC-3.6）——**通篇讲的是除雪作业的发包与承包商比价，
没有一处提到 Priority I/II/III**。分级的成文起点是 **1987-09-30**。

⚠️ 还有第三个年份：1987 年的报告正文自陈当时执行的是
`the current City of Winnipeg (Council adopted 1980) snow clearing policy`。
**1979（决议）/ 1980（自陈的采纳年）/ 1987（分级定义批准）三者不一致**，
本轮不裁决，列为裁决项 #1。

### Q2 · 当年写下的理由原话 —— ✅ 命中（限定到 1987 那一轮，见裁决项 #2）

```
状态: 命中
引文: To qualify for a Priority II status the criteria identifies streets which
      are regular transit routes and/or residential collectors carrying 1,000 or
      more vehicles a day. Should streets such as those leading to schools on
      residential streets be upgraded to Priority II, there would have to be a
      corresponding increase in operating budgets because of the increased level
      of service provided to these streets. In view of the limited availability
      of equipment, an upgrading of priorities for some Priority III streets,
      would cause a corresponding delay in completion of existing Priority II
      streets.
出处: Council Minutes – September 30, 1987 · Minute 1584 ·
      "Report of the Committee on Works and Operations, dated September 15, 1987" ·
      Snow Clearing Policy, File SC-3.6 · §4 "Priority of plowing residential
      Priority III streets", Page 5 (共 10 页) ·
      https://dmis.winnipeg.ca/DownloadCouncilPolicyCustomDocument/3514/MRSR_Relation/5582
检索日: 2026-08-31
一手性: 委员会报告 / Council 会议记录（§3.2 第 2 类）
快照: var/research/snow-policy/amend-1987-minute-5582.pdf
      + var/research/snow-policy/m1584.txt（同文件的文字抽取）
```

同一份记录给出了这轮修订的**触发事件**，也是逐字：

```
引文: On November 27th, 1986, with follow-up correspondence on January 8th,
      1987, the Chairman of the Committee on Works and Operations forwarded to
      all members of Council a copy of the current City of Winnipeg Snow
      Clearing Policy and requested written comments from Councillors as to
      whether or not changes in the policy were required. ... The concerns of
      various Councillors which included their experiences of the November
      7th-8th, 1986 blizzard, can be categorized and reviewed in five distinct
      groupings
出处/检索日/一手性/快照: 同上（Minute 1584, Page 1）
```

**理由的形状是「服务水平 × 预算 × 设备可得性」的配给论证，不是「主干道更重要」的原则宣示。**
这与 §987 那句转述的语气不同，但不冲突——见裁决项 #4。

### 🟡 交叉核对（不是独立证据）

`legacy.winnipeg.ca/publicworks/snow/snow-clearing-policy.stm`（快照
`snow-clearing-policy.stm`，检索日 2026-08-31）**逐字复现了 PW-001 的正文**，
本轮把它与 PDF 对读，用于确认没有抄错：措辞、阈值（3 cm / 5 cm / 10 cm）、
时限（36 hours / five working days）**逐条一致**，
唯一差异是 HTML 写 `Priority 1/2/3` 而政策原文写 `Priority I/II/III`。
🔴 **它仍是第 3 类公众页，不作为出处**；上面所有引文的出处栏写的都是 PDF。

`legacy.winnipeg.ca/publicworks/snow/street-priority.stm`（快照
`street-priority.stm`）另给出一条与本仓库直接相关的对应关系：
P1/P2 街道对应 **Winter Route Parking Ban**，P3 街道对应 **Residential Parking Ban**。
🔴 记为线索、**不记为结论**——它出自公众页，且不在本轮三问范围内（design §3.1「不许扩项」）。

### 检索轨迹

| 源类 | 查了什么 | 结果 |
|---|---|---|
| 1 一手政策 | `dmis.winnipeg.ca` 政策记录 3514 → `PW-001.pdf`（28 页，含 Appendix A 与 B） | ✅ **命中**。Q3 全部、Q1 的两个日期、SLA 三条时限 |
| 2 委员会报告 / 会议记录 | 同一记录页的 19 条 decision/amendment 链接；取了最早的 1979-09-19 Minute 1751 与 1987-09-30 Minute 1584 全文 | ✅ **命中**。Q2 的理由原话；并证伪「1979 = 分级起点」 |
| 3 公众页 | `legacy.../snow-clearing-policy.stm` · `.../street-priority.stm` · `winnipeg.ca/.../frequently-asked`（快照 `faq-snow-clearing-ice-control.html`） | 🟡 仅用于交叉核对与定位 DMIS，未作出处 |
| 4 综述 | **未检索** | 一手件已到手，按 §3.2「一手性决定结论能不能被引用」无必要 |
| 5 新闻 | **未检索** | 同上。G3 因此是空达成，不是「查了但没用」 |

**未取到的**：Appendix A 之外的分级判据修订史（1987 之后 17 条修订里哪几条动过
Appendix A，逐条未查）；1979/1980 那版政策的正文（DMIS 只挂了 1979 的会议记录，
没有当时的政策文本）。两条都不影响 Q1–Q3 的裁决，列入 §5。

### ⚠️ 待 H1 裁决

M 批遇到需要人判断的分叉，**两条都记下来并继续跑**（design §3.4）。

| # | 分叉是什么 | 选项 A | 选项 B | H1 裁决 |
|---|---|---|---|---|
| 0 | 上一轮记的「出站被拒」分叉 | —— | —— | ✅ **已失效**：2026-08-31 复跑预检直接通了，无需裁决 |
| 1 | Q1 的「建立年份」写哪个：政策生效 **1979-09-19** / 报告自陈采纳 **1980** / 分级定义批准 **1987-09-30** | 写 **1987**——问的是「分级制度」，而 1979 的会议记录里没有分级 | 写 **1979（政策）+ 1987（分级）两个日期**，说明两者不是一回事 | 待裁决（**建议 B**：一个日期必然要在别处再解释一次） |
| 2 | Q2 取到的是 **1987 修订轮**的理由，**1979/1980 原始制度的理由未取到**（DMIS 无该版政策正文） | 判 Q2 **命中**，注明范围限定到 1987 | 判 Q2 **未命中**，因为问的是「当年写下的」 | 待裁决（本篇暂记命中 + 限定，理由：分级本身就是 1987 才成文的，1987 就是「当年」） |
| 3 | **Appendix A 与报告正文的数字不一致**：附表 Priority II 的 `Min. Average Daily Traffic Volume` 是 **800**，同一天通过的报告正文写 `carrying 1,000 or more vehicles a day` | 以**附表**为准（它是被批准的判据本体） | 两个都记，注明同日不一致 | 待裁决（🔴 **不要只记一个**——本探针的整个由来就是一句没留下出处的数） |
| 4 | §987 的转述与查到的原文**语气不同**：转述说「不是靠经验，是政策规定的」，原文的论证是服务水平/预算/设备配给 | 判**不冲突**，G4 只补出处、不改措辞 | 判需要改写措辞 | 待裁决（**建议 A**：原文确实是成文规定，转述在事实上成立） |
| 5 | `legacy.winnipeg.ca` 的 HTML 页逐字复现政策正文，它算第 1 类还是第 3 类 | 第 3 类公众页（本篇采用） | 视为政策正文的电子版 | 待裁决（**低风险**：本轮引文全部取自 PDF，无论怎么裁都不影响台账） |

---

## 4. 验收判据的实际结果

逐条对照 design §5，**贴真实内容，不写「已验证」**。

| # | 判据 | 结果 |
|---|---|---|
| G1 | Q1/Q2/Q3 各有一行裁决，无空缺 | ✅ **达成**——Q3 命中 · Q1 命中 · Q2 命中（范围限定，裁决项 #2） |
| G2 | 每条「命中」六字段齐全，引文非空、快照存在 | ✅ **达成**——三块证据卡六字段齐全，引文为英文原文，四个快照文件在 `var/research/snow-policy/` |
| G3 | 无一条结论的一手性为「新闻」 | ✅ **达成**——新闻类**一条都没检索**（轨迹表第 5 行），不是查了不用 |
| G4 | Q3 命中 → `business-objectives.md` §987 补上出处 | 🔺 **已触发，未执行**——按 §1.4「G4 在 H1 由人执行」，M 批不动既有文档。草案见下 |
| G5 | 全程 ≤ 3 小时 | ✅ **达成**——段 B+C 实耗约 25 分钟（段 A 跳过） |

### G4 的改动草案（H1 执行，**M 批未落**）

`docs/dev/requirements/business-objectives.md` §987 那段引文块之后，加一条出处脚注。
拟议措辞（**只补出处，不改原句**，对应裁决项 #4 选项 A）：

> 出处：City of Winnipeg Policy No. PW-001《Snow Clearing and Ice Control》
> §2.1–2.3（分级定义，Council 1987-09-30 批准）与 §4.1 Appendix A
> `Street Classification Criteria`（客观判据：车道数 / 最小日均车流 / 路权宽度）·
> §3.1 A–C（时限：P1/P2 `within thirty-six hours following the end of an average
> storm`，P3 `within five working days following the commencement time`）·
> office consolidation 2024-07-18 · 检索日 2026-08-31 ·
> 快照 `var/research/snow-policy/PW-001-snow-clearing-and-ice-control.pdf`

🔴 **§1325 那条待办（提取官方 P1/P2/P3 承诺时限）随 G4 一并可以划掉**——
逐字时限已在 §3 台账里。但 `closed_date` 语义那半截**没查、也不在本轮范围**，
划掉时不要连它一起划。

---

## 5. 遗留项

| 遗留 | 去处 |
|---|---|
| §3「待 H1 裁决」的 #1–#5 | H1，一次坐下裁完 |
| G4 改 §987 | H1 执行，草案见 §4 |
| Appendix A 的**修订史**（1987 之后 17 条修订里哪几条动过分级判据） | 未查。**不重开本轮**（design P5），需要时另起一轮 |
| 1979/1980 版政策正文 | DMIS 只挂了 1979 的会议记录。同上，另起一轮 |
| `sr8r-ehr3` 的街道分级列与 Appendix A 判据是否对得上 | 🔴 **本轮不做**——那是数据查询，design §2 明确排除。但它现在**变得可做了**：判据是成文的四列数值 |

### design §6 开放项逐条状态

| # | 状态 |
|---|---|
| **P1**（分级与 `shift_number` 实质冲突 → §1176 范围约束需重评） | 🔴 **冲突已确认存在，且是实质的。** Appendix A 的判据是**车道数 / 日均车流 / 路权宽度 / 路面宽度**——全部是**单条街道**的属性，分的是**线**；`shift_number` 分的是**面**（`plow_zone` 多边形）。两者不是同一套划分，不能互相翻译。**按 design P1「不在本探针内解决，另起一篇」**，本篇只把冲突从「推测」升级为「有原文佐证」 |
| **P2**（顺带查到 SLA 时限是否触发 BO-5 重评） | ✅ **按 design 不触发**。时限已逐字入账（§3），BO-5 仍是 P1 级、不占关键路径 |
| **P3**（`var/research/` 进 `.gitignore`） | ✅ 已关闭。本轮实测：4 个快照文件（含 52 MB PDF）落盘后 `git status` 干净 |
| **P4**（一手件只能靠信息公开申请） | ✅ **不触发**。一手件公开可下载，未提交任何申请 |
| **P5**（日后拿到本轮未检索到的一手文件） | 维持：不重开本轮，追加到本篇 |
| **P6**（与 `data-source-portfolio.md` §4.4「街道分级是 P1/P3 的客观口径」冲突？） | 🟢 **不冲突，且被证实**——Appendix A 就是那个「客观口径」，判据是可核的四列数值。该条断言从「无出处的判断」升级为「有原文支持」。**H1 可顺带给它补同一条出处**，但那属于 G4 之外的改动，需另行决定 |

## 6. 上线后需要观察的

只有一条，而且它不随时间衰减：

🔴 **凡是引用了本次结论的地方，必须同时携带出处与检索日。**
一条政策引文脱离出处之后，与一句无出处的转述在文本上无法区分——
而 design §1 的整个问题，就是从一句无出处的转述开始的。
