# 快照采集上线记录

> **Date**: 2026-08-__ · **Design**: [../design/2026-08-snapshot-collection-deployment.md](../design/2026-08-snapshot-collection-deployment.md)
> **Result**: _执行中_（完成后改为 Success | Partial | Rolled back）

> ⚠️ **本篇是唯一一次"上线前先写"的 launch 记录**，与
> [launch/README.md](README.md) 约定的"上线后写"不同。理由：这次上线的动作绝大多数
> 在 git 之外（MinIO 凭证、systemd、外部监控），一步做错就是一天不可再生的历史。
> 因此第 0 节先落**执行清单**，执行时逐条填实际输出；第 1–5 节按模板在上线后补齐。
> 第 0 节写下的是**计划**，第 1–3 节写下的是**发生的事**，两者的差异不要抹平。
>
> 命令的解释与取舍不在这里重复，运维手册见
> [guide/snapshot-collection.md](../../guide/snapshot-collection.md)；
> 分批的理由见 design doc。本篇只管"按什么顺序敲、看到什么才算过"。

---

## 0. 执行清单

约定：每一步给出**命令**、**期望看到什么**、**不符合怎么办**。
`⛔` 标记的步骤不通过就**停止**，不要进入下一步。

| 批次 | 做什么 | 大致耗时 | 中断的后果 |
|---|---|---|---|
| 批 0 | 本地保险副本 | 5 分钟 | 无（这一步就是为了让后面允许出错） |
| 批 1 | 存储节点环境 + 凭证 + 监控注册 | 60–90 分钟 | 出血继续 |
| 批 2 | 首日落盘 | 15 分钟 | **出血继续** |
| 批 3 | 定时器 + 告警验证 | 30 分钟 | 明天开始靠人肉记得跑 |

**批 0–3 必须同日完成。** 只做到批 2 等于每天手动跑，只做到批 1 等于没上线。

---

### 批 0 · 保险副本（先做，不依赖任何环境）

在动 MinIO 之前把当天的全量数据落到本地磁盘。它不是 Bronze（非 NDJSON、无
manifest），只是一份保险：后面任何一步卡住，当天数据仍在，可按冻结契约事后补写；
上游一旦过夜覆盖就永久没了。

```bash
mkdir -p ~/wpg-snapshot-insurance
curl -sS --fail --compressed \
  'https://data.winnipeg.ca/resource/g3p4-h83y.json?$limit=500000' \
  -o ~/wpg-snapshot-insurance/g3p4-h83y-$(date +%F).json
ls -lh ~/wpg-snapshot-insurance/
python3 -c "import json,sys;print(len(json.load(open(sys.argv[1]))))" \
  ~/wpg-snapshot-insurance/g3p4-h83y-$(date +%F).json
```

- **期望**：文件约 100–200 MB（未压缩 JSON 数组），记录数 **~238k**。
- **不符合**：记录数远小于 238k → 上游当前状态异常，**当天不要继续上线**，
  次日重来（这一步没有覆盖风险，重来是免费的）。
- 用 `curl` 而不是项目脚本，是因为此刻存储节点上还没有 Python 依赖，
  这一步必须**零前置**。

实际结果：

```
（贴命令输出）
```

---

### 批 1 · 存储节点环境

#### 1.1 ⛔ 先确认时区与 systemd 版本

分区标签由机器本地日期决定，这一条错了，后面所有分区都错位。

```bash
timedatectl                # Time zone: 期望 America/Winnipeg
systemctl --version | head -1   # 期望 >= 252（OnCalendar 才支持时区后缀）
```

- 时区不是 America/Winnipeg：`sudo timedatectl set-timezone America/Winnipeg`。
- systemd < 252：不能在 `OnCalendar=` 里写时区，改为设置主机时区（同上命令），
  并在批 3 记下这一偏差。

#### 1.2 运行用户与代码

在 GitHub 仓库 Settings → Deploy keys 添加一把**只读** key（勾掉 Allow write access），
私钥放到存储节点 `/opt/uoip/.ssh/id_ed25519`（属主 uoip，600）。

```bash
sudo useradd --system --home-dir /opt/uoip --shell /usr/sbin/nologin uoip
sudo install -d -o uoip -g uoip /opt/uoip
sudo -u uoip git clone git@github.com:<org>/<repo>.git /opt/uoip
```

- **期望**：clone 成功，`/opt/uoip/scripts/collect_snapshot.py` 存在。
- **不要**用带 token 的 HTTPS URL——那等于把一份可写凭证长期留在无人值守的机器上，
  且写进 `.git/config` 明文。

#### 1.3 依赖（只装实际 import 的那几个）

```bash
sudo -u uoip python3 -m venv /opt/uoip/.venv
sudo -u uoip /opt/uoip/.venv/bin/pip install -r /opt/uoip/requirements-snapshot.txt
sudo -u uoip /opt/uoip/.venv/bin/pip list | wc -l
```

- **期望**：18 个包左右，**没有 pyspark、没有 shapely**。
- 不要跑 `uv sync` / `make install`：会往这台小机器装几百 MB 永不 import 的计算栈。

#### 1.4 MinIO 受限凭证

在 MinIO 上创建**仅供这个任务使用**的 service account，策略限定在该源前缀、
只给写入与分段上传、**不给删除**（策略 JSON 见
[guide §2.3](../../guide/snapshot-collection.md)）。

```bash
mc admin policy create local uoip-snapshot-writer /path/to/policy.json
mc admin user svcacct add local <existing-user> --policy /path/to/policy.json
```

- **期望**：拿到一对新的 Access Key / Secret Key。
- 分段上传的动作不能漏：18.5 MB 的对象走 multipart，只给 `PutObject` 的策略
  在小对象上全绿、在真实数据上失败。
- 不给 `DeleteObject`：攒了半个冬季的历史，不该由一把无人值守的密钥承担误删风险。

#### 1.5 配置文件（不进仓库，属主与运行用户分离）

```bash
sudo install -d -m 750 -o root -g uoip /etc/uoip
sudo install -m 640 -o root -g uoip /dev/null /etc/uoip/snapshot.env
sudo -e /etc/uoip/snapshot.env
```

按 `.env.example` 填 `S3_ENDPOINT_URL`（9000，**不是** 9001 控制台口）、
`S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY`（1.4 那对）、`S3_BUCKET_NAME=uoip`、
`S3_REGION`、`SOCRATA_APP_TOKEN`、以及两个 `SNAPSHOT_*`（下一步拿到 URL 后回填）。

#### 1.6 注册死人开关

在 healthchecks.io 建一个 check：**Period 1 day，Grace 6 hours**，
并挂上能推到手机的集成（Pushover / Telegram / ntfy 任一）。把 ping URL 填进
`/etc/uoip/snapshot.env` 的 `SNAPSHOT_WATCHDOG_URL`。

- 宽限期 6h 是本次上线**最高杠杆的参数**：06:30 采集 → 最迟 12:30 告警 →
  当天还剩大半天可以重跑，上游此刻仍持有当前状态，重跑不丢东西。
- 宽限期设成 1 天以上，等于把可恢复的故障变成不可恢复的。
- 邮件不算告警通道。凌晨三点的邮件等于没有。

#### 1.7 ⛔ 判据 1：集成测试真跑（在开发机上，不在存储节点）

用**无限制的开发凭证**（不是 1.4 那把受限 key，它按设计写不了测试前缀）在开发机
`.env` 里配好 `S3_*` 指向这台 MinIO，然后：

```bash
make test-integration
```

- **期望**：全部 PASS，**skip 数为 0**。skip 就是环境变量没被读到，等于什么都没验证。
- 其中 `tests/integration/test_snapshot_roundtrip.py` 是本次上线新增的：它覆盖
  `write_snapshot_stream` 的真实往返（upload → download → gunzip → 比对）、
  manifest 四个字段的语义、小样本闸门不破坏既有分区、以及 **>8 MB 的 multipart 路径**
  ——正是 mock 测不出、而受限策略最容易漏权限的那条路。
- 不通过就停：MinIO 没打通之前，批 2 的失败会分不清是环境还是代码。

实际结果：

```
（贴 pytest 结果尾部）
```

---

### 批 2 · 首日落盘

#### 2.1 ⛔ 判据 2：dry-run 量级核对

```bash
cd /opt/uoip && sudo -u uoip /opt/uoip/.venv/bin/python \
  -m scripts.collect_snapshot --source SRC-WPG-SNOW --dry-run
```

- **期望**：`DRY-RUN SRC-WPG-SNOW/snow_clearing_status: ~238000 records upstream`，
  与调研实测（[winnipeg-data-sources.md §4.3](../requirements/winnipeg-data-sources.md)）
  和批 0 的行数同量级。
- **量级不符则停止**，不得继续——上游若真的变了，先搞清楚再落盘，
  第一天落下去的形状就是最终形状。

#### 2.2 首次真实采集（用 service 单元跑，不在交互 shell 里跑）

先装 `/etc/systemd/system/uoip-snapshot.service`（内容见
[guide §2.6](../../guide/snapshot-collection.md)，含 `Restart=on-failure` 与
`RestartSec=30min`），然后：

```bash
sudo systemctl daemon-reload
sudo systemctl start uoip-snapshot.service
journalctl -u uoip-snapshot.service -n 50 --no-pager
```

- **期望**：日志出现 `N records, ~18.5 MB stored (~Nx compression) -> s3://uoip/...`，
  退出码 0。
- 这同时兑现**判据 4**（在定时器的空环境里跑通，而不是在你的 shell 里跑通）——
  "手动能跑、定时不能跑"是这类任务最常见的死法，一次性把它排除掉。
- 失败：`Missing required object-storage environment variable(s)` → `EnvironmentFile`
  没被读到或没填全；`Could not connect to the endpoint URL` → 端口填了 9001。

#### 2.3 ⛔ 判据 3：回读确认成对且体积合理

用开发凭证（`mc` 或开发机）：

```bash
mc ls --recursive local/uoip/bronze/raw/SRC-WPG-SNOW/snow_clearing_status/
mc cat local/uoip/bronze/raw/SRC-WPG-SNOW/snow_clearing_status/ingest_date=$(date +%F)/manifest.json
```

- **期望**：当日 `ingest_date=` 目录下 `data.ndjson.gz` 与 `manifest.json` **同时存在**；
  manifest 的 `record_count` ≈ 238k、`compression` = `"gzip"`、
  `stored_bytes` 与 `file_size_bytes` 的比值与
  [data-volume-baseline.md](../data-volume-baseline.md) 的实测压缩比相符。
- `data_date_min` / `data_date_max` 为 `null` 是**正确**的：上游没有任何时间字段。

**出血在这一刻停止。** 后面全是加固。

实际结果：

```
（贴 mc ls 与 manifest 内容）
```

---

### 批 3 · 自动化与告警

#### 3.1 判据 5：定时器的下一次触发落在预期的当地时刻

装 `/etc/systemd/system/uoip-snapshot.timer`（内容见 guide §2.6），然后：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now uoip-snapshot.timer
systemctl list-timers uoip-snapshot.timer --no-pager
```

- **期望**：`NEXT` 是明天 06:30（±5 分钟随机延迟）**当地时间**，
  不是机器默认时区推算出的另一个时刻。
- 对不上：回到 1.1 的时区处理。

#### 3.2 失败通知走通（进程活着时自报）

用一个必然触发小样本闸门的阈值跑一次。**不会写任何东西**——闸门在上传前拦截，
既有分区不受影响：

```bash
sudo systemd-run --uid=uoip --working-directory=/opt/uoip --wait \
  --property=EnvironmentFile=/etc/uoip/snapshot.env \
  /opt/uoip/.venv/bin/python -m scripts.collect_snapshot \
  --source SRC-WPG-SNOW --min-records 999999999
```

- **期望**：退出码 2，`SnapshotTooSmallError`，`SNAPSHOT_ALERT_WEBHOOK_URL`
  收到一条通知；再跑一次 2.3 确认当日分区**没有**被改动。
- 这一步会完整走一遍上游（数分钟），是为了顺带确认闸门在真实数据量下的行为。

#### 3.3 ⛔ 判据 6：死人开关确实会在没等到打卡时告警

这是唯一直接验证"漏采会被发现"的判据，**不能省略**。不用等一整天：

1. 在 healthchecks.io 把该 check 临时改成 **Period 10 min / Grace 5 min**；
2. 什么都不做，等 15 分钟；
3. **期望**：手机收到告警；
4. 改回 **Period 1 day / Grace 6 hours**，然后手动 `systemctl start
   uoip-snapshot.service` 打一次卡，确认 check 回到 up（绿色）。

- 第 4 步别忘了：留在红色状态会让明天真正的告警混在里面。
- 顺带确认成功时确实打卡了（判据 6 的另一半）：check 的 last ping 时间应等于
  2.2 那次成功采集的时刻。

实际结果：

```
（贴告警截图说明 / 时间点）
```

#### 3.4 记录基线（供一周后抬高闸门用）

把这三个数抄下来，一周后据此把 `--min-records` 抬到真实行数的量级附近：

| | 值 |
|---|---|
| `record_count` | |
| `file_size_bytes`（未压缩） | |
| `stored_bytes`（压缩后） | |

---

## 1. 时间线

（上线当日填：关键动作与时刻。含回滚点——哪一步之前还能退，之后不能。
本次的不可逆点是**批 2 首次落盘**：在此之前一切可推倒重来，
在此之后 `ingest_date=` 分区已成为冻结契约的一部分。）

## 2. 与设计的偏差

| 设计怎么写的 | 实际怎么做的 | 为什么改 |
|---|---|---|
| | | |

（没有偏差也写一行——那是对设计质量的正面证据。）

## 3. 验收判据的实际结果

逐条对照 design doc 第 5 节，贴真实输出（命令 + 结果），不写"已验证"。

| # | 判据 | 结果 |
|---|---|---|
| 1 | 对象存储真实往返通过，集成测试无 skip | |
| 2 | dry-run 记录数与调研实测同量级 | |
| 3 | 首日分区成对存在、压缩比相符 | |
| 4 | 在定时器的空环境下跑通 | |
| 5 | 下一次触发落在预期的当地时刻 | |
| 6 | 死人开关收到打卡，且漏采能在宽限期内告警 | |

## 4. 遗留项

- **抬高小样本闸门**（当前默认 1000，比真实行数低两个数量级）——积累一周基线后，
  去处：一次独立改动 + 单测。
- **`--skip-if-exists` 开关**——在它落地前，重试只挂在失败条件上；
  挂第二个定时器之前必须先有它。
- **冗余采集是否要做**——先把"小时级发现故障"跑稳，积累一个完整降雪季之前再评估。
- **计算节点 `dag_audit_bronze` 的快照只读核对**——代码已在，需在计算节点起来后确认
  它真的在扫这个前缀（只报不补：快照"补"只会把今天的数据写进昨天的分区，那是伪造历史）。

## 5. 上线后需要观察的

- **头一周每天看一眼** healthchecks.io 是否绿、当日分区是否成对。
- **record_count 的日间波动**：若某天较基线掉超过 30%，先查上游再动闸门。
- **压缩比突变**：意味着上游 schema 变了（字段增删），需要回看
  [contracts/](../../../contracts/) 与 §4 的闸门取值。
- **升级 OS / Python 之后必须手动 `systemctl start` 一次**：包升级导致 venv
  失效是这类无人值守任务的典型死法，而它只在下一个 06:30 才暴露。
