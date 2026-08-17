# Airflow 失败告警与 s3a 403 事故收尾

> **Status**: 批 1 / 批 2 已实现（`ba43372`，2026-08-16）· 批 3 未做 · **Date**: 2026-08-16
>
> 落地情况逐条见 §3 各批标题上的标注。**「约定写了从未实现」这句话自 `ba43372`
> 起不再成立** —— 引用本篇时注意时点：`dags/_alerts.py` 已存在，
> `on_failure_callback` 已在 `DEFAULT_ARGS` 里对全部 DAG 生效。
> 唯一还欠的是**端到端人工验证**（触发 `dag_smoke_alert`，确认 Discord 真收到），
> 那条判据不能靠「看起来配好了」代替，已收进 L1
> （[20260817-silver-etl-runnable.md](20260817-silver-etl-runnable.md) §3.6）。

## 1. 问题

### 1.1 事故与根因（已解决，此处只留结论）

`dag_backfill_silver_weather_archive` 连续多日失败，报

```
java.nio.file.AccessDeniedException: s3a://uoip/bronze/raw/SRC-Open-Meteo/
weather_archive/2024-07/data_2024-07-26.ndjson.gz: ... 403 Forbidden
```

**根因是 `bds3.huzhi.dev` 挂在 Cloudflare 橙云代理后面。** `.gz` 在 Cloudflare
的默认可缓存扩展名列表里，边缘节点收到 HEAD 时会**回源发 GET** 去填缓存。
SigV4 把 HTTP method 纳入签名，method 被改写后签名失配，MinIO 返回
`SignatureDoesNotMatch`——HTTP 状态码正是 403。

之所以查了很久，是因为它同时满足"间歇、不可复现、与并发无关"：是否改写取决于
边缘缓存状态，所以同一份代码同一条路径，几分钟内可以从全绿变成 21% 失败再变回全绿。

放大它的是 **s3a 把 403 归类为不可重试**（`S3ARetryPolicy` 的 fail-fast 类），
`fs.s3a.attempts.maximum` 不介入。而当时 `_bronze_paths` 逐日枚举路径，
18 年回填要发 ~6,800 个 HEAD，单次故障概率再低也几乎必然踩中。

排查中依次排除并留下判据：凭证（三处 md5 一致）、region/签名算法（boto3 六种
组合全通，MinIO 不校验 region）、Bronze 数据缺口（9,724 天无缺口）、并发
（单线程反而失败最多）。最终的决定性证据是 **nginx access log 的第一列是
Cloudflare 边缘 IP，且方法记录为 GET 而客户端只发过 HEAD**。

> 事故叙事本身更适合放 [postmortem/](../postmortem/README.md)（目前尚无复盘）。
> 本篇只承接**待办**。

### 1.2 真正要修的：12 天没有任何人被通知

`dags/_dag_common.py` 的 `DEFAULT_ARGS` 里有 `retries` / `retry_delay` /
`email_on_failure: False`，**没有 `on_failure_callback`**。而 CLAUDE.md
的 Airflow 约定明确要求：

> Every DAG must have: `retries=3`, `retry_delay=...`, `on_failure_callback`
> pointing to the Slack/email alert utility.

前两项落地了，第三项从未实现。所以这不是"配漏了一个参数"，是**这个能力不存在**，
静默是必然的。事故被发现纯属偶然。

### 1.3 日志噪音掩盖了真实错误

`docker-compose.yml` 把整个 `scripts/` 挂到 `/opt/airflow/plugins/scripts`，
而 Airflow 的 plugins_manager 会**导入 `plugins/` 下每一个 `.py`**。每次任务
运行都刷 15 行 ERROR：

- 6 × `Source 'SRC-XXX' already registered with run` —— `backfill_*.py` 被导入
  两次（Airflow 插件扫描一次、`scripts/backfill/main.py` 的 `pkgutil` 发现一次），
  `@register_backfill` 撞车
- 9 × `No module named 'shapely'` —— `scripts/analysis/` 的探针依赖 Airflow
  镜像里没有的包

这些不影响功能，但排查时真正的异常就淹没在这堆里。

### 1.4 契约清单有洞：`accum_flag`

`accum_flag` 在 `spark/schemas/weather_schemas.py`、`sql/ddl/`、
`contracts/silver-contracts/` 三处都加了（`7ceedbd`），**但产出它的
`segment_snowfall_events` 从来没写这一列**，运行时抛
`DataFrame is missing expected column(s): ['accum_flag']`。

关键在于：AGENTS.md 的「数据契约义务」列了 StructType / DDL / 契约 / CHANGELOG
四项，**唯独没列"产出这一列的 transform"**。也就是说，照着清单一步不落走完，
依然会掉进去。不补这条，下次加字段还会一模一样地重演。

## 2. 约束

- **Discord webhook URL 本身是凭证**，等同于往那个频道发消息的权限。异常日志
  不得回显 URL（`requests` 的异常字符串会带完整 URL）。
- **`ingestion/snapshot/notify.py` 已经解决过这些问题**，不要重解：脱敏只留状态码、
  Discord `content` 2000 字上限截断且标识信息前置、同时发 `content`(Discord) 与
  `text`(Slack) 双键、永不抛异常（告警失败不能顶掉原始错误）。
- **死人开关不能共用 snapshot 的**。理由与 `.claude/rules/backfill.md` 里
  `BACKFILL_WATCHDOG_URL` 拒绝回落 `SNAPSHOT_WATCHDOG_URL` 完全相同：在 snapshot
  没跑的那天替它签到，正好压掉那个 check 存在的意义。
- **`plugins/` 路径是耦合的**。`_spark_common.py` 的
  `spark.executorEnv.PYTHONPATH=/opt/airflow/plugins` 与 spark-worker 的同路径
  挂载是配套的——为了让 `spark.transforms.xxx` 在 driver 与 executor 上解析成
  同一个模块。改挂载路径必须三处一起改。
- 全量 backfill 正在运行，批 3 涉及重建容器，需等它跑完。

## 3. 方案

### 批 1 —— 失败告警（最高优先级）· ✅ 已实现（`ba43372`）

> 实现与本节设计一致，无偏差。落地物：`dags/_alerts.py`（`alert_on_failure` +
> `ping_watchdog`）· `_dag_common.DEFAULT_ARGS` 挂 `on_failure_callback` ·
> 两个 ingest DAG 成功时 ping watchdog · `.env.example` 的 `AIRFLOW_WATCHDOG_URL` ·
> `tests/unit/test_dag_alerts.py`。
> 另加一件本节没写的：`dags/dag_smoke_alert.py` —— 一个故意失败的手动 DAG，
> 用来做 §5 那条「不能靠看起来配好了代替」的人工验证。

新增 `dags/_alerts.py`，复用 `ingestion/snapshot/notify.py` 的 payload 约定与
脱敏逻辑（能抽公共函数就抽，不能就照抄并注明来源）。

**两个通道，对应两种故障：**

| 故障 | 谁能发现 | 机制 | 环境变量 |
|---|---|---|---|
| 任务跑了但失败 | 进程自己 | `on_failure_callback` → Discord | `BACKFILL_ALERT_WEBHOOK_URL`，回落 `SNAPSHOT_ALERT_WEBHOOK_URL` |
| 任务根本没跑 | 只有外部 | 成功时 ping 死人开关 | `AIRFLOW_WATCHDOG_URL`，**无回落** |

告警通道**复用 `BACKFILL_ALERT_WEBHOOK_URL`**，不新增
`AIRFLOW_ALERT_WEBHOOK_URL`——告警通道只是投递目的地，共用一个 Discord 频道
没有副作用，而环境变量已经够多了。回落链与现有 backfill 一致。

死人开关**必须独立**，见 §2。scheduler 挂掉时没有任何进程存在去执行
`on_failure_callback`，这正是 1.2 那类静默的另一半。

落地点：
- `DEFAULT_ARGS` 加 `on_failure_callback`（覆盖全部 6 个 DAG，一处生效）
- 两个 ingest DAG（`dag_ingest_weather_archive` / `dag_ingest_service_requests`）
  成功时 ping watchdog
- `.env.example` 补 `AIRFLOW_WATCHDOG_URL` 并说明无回落的理由
- 单测：webhook 未配置时只 warn 不抛、payload 双键、超长消息截断后仍保留 dag_id/task_id

### 批 2 —— 防复发（五分钟，与批 1 不冲突）· ✅ 已实现

- **AGENTS.md 的「数据契约义务」补一条**：产出该列的 transform / ETL job。
  这是 1.4 的直接修复。
- **建 `CHANGELOG.md`**。AGENTS.md 第 4 条要求往里写迁移说明，但文件不存在，
  所以这条约定同样是空转的。

### 批 3 —— 日志噪音（等 backfill 跑完）· 🔴 未做

> `docker-compose.yml` 仍把 `ingestion` / `scripts` / `config` / `spark`
> 挂在 `/opt/airflow/plugins/` 下。L1 的全量回填还没跑，本批的前提条件未到。

把代码挂载移出 `/opt/airflow/plugins/`（例如 `/opt/uoip/`），用 `PYTHONPATH`
指过去，让 Airflow 的插件扫描看不到它。注意 §2 的三处耦合要一起改。

## 4. 被否决的选项

| 选项 | 否决理由 |
|---|---|
| 新增 `AIRFLOW_ALERT_WEBHOOK_URL` | 告警通道只是投递目的地，共用频道无副作用；环境变量已经够多。复用 `BACKFILL_ALERT_WEBHOOK_URL`。 |
| 死人开关回落到 `SNAPSHOT_WATCHDOG_URL` | 在 snapshot 没跑的那天替它签到，会压掉那个 check 唯一存在的理由。同 `.claude/rules/backfill.md`。 |
| 用 `email_on_failure` | 没有可用 SMTP，且 Discord 通道已经在跑，再引一条链路只是多一个会坏的地方。 |
| 保留 Cloudflare 橙云 + Cache Rule 关缓存 | 把 S3 API 的正确性押在 CDN 的内部行为上，策略一变再炸一次；且免费版 100 MB 请求体上限仍在。已改为灰云 + Let's Encrypt 证书。 |
| 给 s3a 加 403 重试 | 403 语义上就该 fail fast，重试会把真实的权限错误拖成超时，更难查。正确方向是减少请求数（已做：逐日 → 按月，~6,800 → ~220）。 |
| 在 DAG 里逐个写 `on_failure_callback` | 6 个 DAG 全部要改，且新 DAG 会漏。放 `DEFAULT_ARGS` 一处生效。 |

## 5. 验收判据

批 1：

```bash
# 1. 所有 DAG 都覆盖到（DEFAULT_ARGS 一处即可，此处确认没有 DAG 覆盖掉它）
grep -rn "on_failure_callback" dags/ | grep -v "_dag_common.py"   # 期望：无输出或显式复用

# 2. 未配置时不炸：清空变量跑单测
BACKFILL_ALERT_WEBHOOK_URL= SNAPSHOT_ALERT_WEBHOOK_URL= make test-unit
```

- **人工验证**：把某个 task 改成必失败触发一次，Discord 频道收到消息，
  且内容含 `dag_id` / `task_id` / `run_id` / 日志链接。
- **死人开关验证**：停掉 scheduler 超过一个调度周期，healthchecks.io 报警。
  这一条不能靠"看起来配好了"代替——1.2 的教训正是约定写了但没生效。

批 2：`CHANGELOG.md` 存在且 AGENTS.md 清单含 transform 一项。

批 3：任务日志里 `Failed to import plugin` 为 0 行。

## 6. 开放项

**写 `sql/dml/` 之前必须先定的三个口径**（原文见
[20260813-gold-silver-schema-derivation-launch.md](../launch/20260813-gold-silver-schema-derivation-launch.md) §8.2）：

1. Gold 的增量/幂等策略（C6/C17）—— 决定所有 DML 怎么写，定错了每张表返工
2. `silver_service_request` 的小文件 coalesce（C7）—— **必须在 16 GB 回填之前**定
3. C1/C2 改名 —— 回填后再改成本跳一个量级

**低优先级遗留：**

- `bdminio.huzhi.dev`（MinIO Console）仍在用 Cloudflare Origin 证书，靠橙云续命。
  哪天灰云会以本次同样的方式挂掉（客户端不信任该 CA）。
- `contracts/source-registry.md` 不存在但 AGENTS.md 引用它；
  `contracts/api-contracts/open-meteo.yaml` 仍写着批 2 已废弃的 dataset 名。
- `docs/dev/README.md` 的 design 索引缺两篇（`20260809-gold-silver-schema-derivation.md`、
  `20260812-gold-bus-matrix.md`），且 `design/README.md` 写的命名是
  `YYYY-MM-<topic>.md` 而目录里实际全是 `YYYYMMDD-`。二者取一后统一。

---

## 附：本次已完成的修复（`df921ab`）

代码：

- `spark/transforms/weather_archive.py` —— 补 `accum_flag`，取值按契约定义
  `peak_daily_snowfall_cm < threshold_cm`；无累积判据时恒为 `False` 而非 null
- `spark/jobs/etl_weather_archive.py` —— `_bronze_paths` → `_bronze_month_prefixes`，
  按月目录读，18 年回填 ~6,800 → 224 个路径。**必须配 `pathGlobFilter="data_*.ndjson.gz"`**：
  月目录里 `manifest_*.json` 与数据文件同级，整目录读会把 manifest 当气象记录解析，
  对着声明的 schema 得到整行 null，**静默落进 `_rejects` 而不报错**
- `Makefile` —— 新增 `stack-recreate-airflow`。`docker compose restart` 只重跑容器内
  进程，环境变量仍是创建时烤进去的那份，改 `.env` 重启多少次都不生效
- 契约里两处过时的 "🆕 Not yet in SNOWFALL_EVENT_SCHEMA" 标注

基础设施（不在本仓库）：

- `bds3.huzhi.dev` 改灰云（DNS only），A 记录 `168.138.95.139`
- Cloudflare Origin CA 证书 → Let's Encrypt（Origin CA 不被任何公共信任根接受，
  灰云后客户端会 TLS 校验失败；它有效期到 2041，**不是过期问题，续期无用**）
- nginx `server_name` 的逗号笔误（certbot 重写时顺带修掉）
