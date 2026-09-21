# 暂缓选项

被认真考虑过、但**当下不做**的事。每条只记三件：想解决什么问题、为什么现在不做、
重新捡起来时第一步验证什么。有了结论就从本篇删掉——升格成 ADR（选型定了）、
design（要动手了），或者直接丢弃（判定不值得）。

本篇是常青的：它描述的是"现在的暂缓集合"，集合变了就原地改写，不追加历史。
它**不记进度、不记排期**（那是 ticket 的事，见 [README](README.md) 第二节）。

---

## 1. 在 Azure 上复现一遍本平台

**想解决什么**：当前全栈自建（MinIO / Spark Standalone / Trino），简历与讲述里
缺一段"同一套分层逻辑在托管云上怎么落"的对照。云证书（AZ-900 / DP-203 /
Databricks）考的正是这类组件取舍，手上没跑过就只能背名词。

**为什么现在不做**：H1 会议交付（2026-09-19）之后平台本身没有新增能力需求，
迁移换不来新东西——真正缺的是**云侧的取舍经验**，不是第二份部署。而全量迁移的
代价不小：12.47 M 行 Silver 上云要付存储 + 计算 + 出网三笔钱，出网流量（把数据
取回来）比上传更贵。

**重新捡起来时第一步验证什么**：

1. 先确认免费额度的形态，再决定规模。三条路互斥，优先级从上到下：
   - **Microsoft Learn 沙箱** —— 跟官方学习路径做实验，不碰自己的订阅，
     零成本零风险。考证的实验优先走这条。
   - **Azure for Students** —— 有效学校邮箱可拿一年期额度且**无需信用卡**，
     没有信用卡就没有意外扣费的可能。注册前先确认 PACE 的邮箱域名能否通过验证。
   - **Azure 免费账户** —— 试用额度 + 12 个月免费服务档，需信用卡验证。
     走这条的话**注册当天**就去 Cost Management 设 Budget 告警，阈值设成个位数
     美元；12 个月免费档用完之后是真扣钱。
2. 最短验证路径是**一个 Storage Account + 一个 Silver 日分区**（几十 MB）+
   Synapse Serverless 直接 `OPENROWSET` 查那份 Parquet。这条通了就说明
   "对象存储 → SQL 引擎"在云侧成立，其余是同构的。Storage Account 必须开
   hierarchical namespace，否则它是对象存储不是数据湖。
3. 组件对照（真要搭时照这张表，不要临时想）：

   | 本平台 | Azure |
   |---|---|
   | MinIO | ADLS Gen2 |
   | Spark Standalone | Azure Databricks / Synapse Spark |
   | Hive Metastore | Unity Catalog / Synapse 内置 |
   | Trino | Synapse Serverless SQL Pool（按扫描量计费） |
   | Airflow | Data Factory / Managed Airflow |
   | Superset | Power BI Desktop |

🔴 三条红线，都属于"跑起来才发现在烧钱"那一类：
**Databricks 集群必须设 auto-termination**（忘关一夜就是两位数美元）·
**不要把 TB 级数据传上去**（出网收费，取回来比传上去贵）·
**不要用试用额度跑长回填**——E 阶段那次 3 小时 03 分里有 2.5 小时在
`FileOutputCommitter` 的 commit 上（见 `launch/20260817-silver-etl-runnable-launch.md` §2），
在云上那 2.5 小时同样计费，而且看起来像卡住。

---

## 2. Dashboard 的双屏演示形态

**想解决什么**：对外讲这个平台时，演讲者屏和投影屏应当是两块内容不同但滚动
同步的屏——讲者看备注与下一屏，观众只看图。Superset 原生没有这个形态。

**为什么现在不做**：Gold 的 17 张表已有生产数据，但对外展示材料本身排在
项目后期；演示形态在有确定的讲稿结构之前定不下来。

**重新捡起来时第一步验证什么**：先核实工具名。备忘里记的是「view.js」，
**该名字未经核实**——按描述（演讲者视图 + 投影视图 + 多屏同步）最接近的现成方案
是 **Reveal.js**：它自带 speaker view（另开一个窗口，显示备注和下一页）和
multiplex 插件（多客户端跟随同一个演示进度）。第一步是打开 Reveal.js 的
speaker-notes 与 multiplex 文档确认这两项是否就是要的东西，若是则本条改写成
「Reveal.js」，若确有一个独立的 view.js 项目再纠正回来。

承载方式两选一，届时再定：把 Gold 的图表导成静态资源嵌进 Reveal.js 幻灯，
或让幻灯 iframe 嵌 Superset 的 dashboard。前者可离线、可控；后者是活数据，
但会把演示的可靠性押在现场网络和 Superset 的可用性上。
