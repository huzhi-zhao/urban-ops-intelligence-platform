# `sql/presentation/` — 一图一份的定稿查询

> 由 `docs/dev/design/20260827-bo-eda-and-presentation-sql.md` §3.4 定义，
> 执行记录在 `docs/dev/launch/20260827-bo-eda-and-presentation-sql-launch.md` §18。

每个文件是**一张图的唯一数据源**，文件名 `fig_<bo>_<nn>_<slug>.sql`。

## 三条硬约束

1. 🔴 **纯 Trino SQL，能原样粘进 CLI 跑。** 载体特有的模板语法
   （Grafana 的 `$__timeFilter`、Superset 的 Jinja）不进这里 —— 它既过不了
   `make lint`（sqlfluff 判 unparsable 会**连带停掉该文件其余所有检查**，
   `.claude/rules/gold-sql.md` R6 记过这个失败模式），也不再可复核。
   要参数化由载体那一侧包一层。
2. 🔴 **文件头注是图的一部分，不是注释。** 三样必写，缺一由单测拦下：
   它回答哪条 BO 判据（`criterion:`）、它的图注全文（`caption:`）、
   它**不能**被读成什么（`must_not_say:`，取自台账 §1 那张禁语表）。
   图注不是 slide 上的补充说明。
3. 🔴 **一个文件一条语句，且不带日期字面量之外的参数。** `scripts/eda/run.py`
   逐个执行它们并冻结结果，多语句无法对应到一张图。

## 头注键（`scripts/eda/run.py` 按它建目录，不另存第二份清单）

| 键 | 含义 |
|---|---|
| `fig_id` | 台账里的图 ID，必须在台账中出现（单测校验，杜绝孤图 / 孤 SQL） |
| `bo` | 归属的 BO |
| `carrier` | `echarts` / `superset` / `grafana`，判据见 design §3.3 |
| `schema` | `gold` / `silver` / `meta` —— 执行时注入的会话 schema |
| `criterion` | 这张图承担的 BO 判据 |
| `caption` | 图注全文 |
| `must_not_say` | 禁语 |

🔴 `schema:` 是承重的。执行器按它连接，裸表名在错的 schema 下**不会报"没这张表"
而是解析到另一张同名表**——R6 的同一个失败模式。单测比对每个文件引用的表是否
真的在它声明的 schema 的 DDL 目录里。
