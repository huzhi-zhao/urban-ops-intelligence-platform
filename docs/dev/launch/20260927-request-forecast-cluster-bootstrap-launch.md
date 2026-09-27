# 请求量预测的事件整簇自助区间上线记录（H2-R13）

> **Date**: 2026-09-27 · **Result**: Success
> · **Design**: [20260927-request-forecast-cluster-bootstrap.md](../design/20260927-request-forecast-cluster-bootstrap.md)
> · **Model version**: `m1-poisson-20260822-df31d954`

## 1. 时间线

1. 在计算节点的生产 Gold 上用既有 M1 查询导出真实 2,178 行面板。第一次从宿主机执行时，
   `.env` 中的 `TRINO_HOST=trino` 只能在 Docker 网络解析；改为本次进程显式使用
   `127.0.0.1:8090` 后成功。没有改 `.env`、容器或线上仓库。
2. 将面板、生产 `metrics.json` / `predictions.csv` 和冻结的 `FIG-BO8-03` 复制到本地
   `var/`，逐文件 SHA-256 对账。面板指纹为 `df31d954`，与生产版本一致。
3. 在本地用最终代码串行完成 500 次事件整簇重拟合；任一副本失败即整批失败。本次
   500/500 完成，在不变的 1,298 格真实面板上产出 649,000 行预测和 5,500 行系数。
4. 从原始副本冻结 90% 请求数预测区间、K=3/5/8 排名稳定性、18 组 K/S/P 网格和
   留出覆盖率摘要。默认 K=5、S=4、P=0.8 下提示数为 0；没有看到结果后改阈值。
5. 三份模型产物上传到既有对象存储版本目录并回读校验 SHA-256。Gold 表、DDL 和契约
   均未改变。
6. dashboard 数据打包为 26 frozen / 0 sample / 0 missing，构建后上传新目录；旧站点先
   移到 `/opt/uoip/uoip-portfolio-site.bak-20260927-pre-r13`，再切换新站点。
7. 在 `https://uoip.huzhi.dev/#/zone` 实际打开验证：默认留出事件、点估计、90% 范围、
   前 K 稳定性、500 次说明与 78% 覆盖率均可见；浏览器控制台 0 条 warning/error。

## 2. 与设计的偏差

| 设计怎么写的 | 实际怎么做的 | 为什么改 |
|---|---|---|
| 在计算节点运行 500 次拟合 | 节点只做只读导出，本地完成拟合，再经节点上传对象存储 | 节点线上 checkout 落后于本次实现且已有未提交的 compose 改动；不切分支、不覆盖线上工作区更安全。输入和输出均以 SHA-256 对账 |
| 页面发布前已有完整本地冻结集 | 第一次打包为 24 frozen / 2 missing；补回节点上已认证的 `FIG-BO2-06/07` 后重跑为 26/0 | 本地旧 `outputjson/` 缺两份既有 lookup 导出，不是 R13 数据缺失 |
| 90% 请求数预测区间 | 名义 90%，实际留出覆盖率 77.922% | 这是模型结果，不是运行故障；按预登记纪律如实发布，不回调区间或提示阈值 |
| 默认规则产生复核提示或明确没有 | 默认规则产生 0 个；18 组网格中 4 组非零，全部 18 组稳健集仍为 0 | 排名稳定性不足本身就是 R13 允许的完成结论 |

## 3. 验收判据的实际结果

测量时间统一为 2026-09-27；模型计算环境为本地 `.venv-ml`，Python 3.12、锁定依赖；
数据环境为计算节点生产 Gold 与 2026-09-27 冻结的 `FIG-BO8-03`。

| 判据 | 实测 | 可重跑入口 |
|---|---:|---|
| 原始面板 / 指纹 | 2,178 行 / `df31d954` | `scripts.models.train_m1 --dump-panel` + `prepare_training_data` |
| 训练 / 留出簇 | 91 / 7 个事件；每簇 22 区 | `bootstrap_metadata.json` |
| 副本完成数 | 500/500，失败 0 | `scripts.models.bootstrap_m1` |
| 原始预测 | 649,000 行；`(replicate_id,event,zone)` 全唯一 | `bootstrap_predictions.csv` 主键检查 |
| 系数副本 | 5,500 行；11 个项 × 500 | `bootstrap_coefficients.csv` |
| `severity_score` 90% 系数区间 | P05 -1766.39 / P50 32.879 / P95 580.239 | 对系数长表按 `term` 求 P05/P50/P95；宽区间量化了已知共线性 |
| 页面格 | 1,298/1,298 均为 `ready` | `build_demand_plan` 对生产版本与摘要求值 |
| 请求数预测区间覆盖率 | 120/154 = **77.922%** | `scripts.presentation.demand_uncertainty` |
| 默认复核提示 | 0；样本内提示 0 | K=5、S=4、P=0.8 |
| 敏感性 | 18/18 组完成；4 组非零，范围 0–3；稳健集 0 | uncertainty 摘要 `sensitivity_grid` |
| 对象存储 | 3/3 文件回读 SHA-256 与本地一致 | `gold/_forecast_runs/m1-poisson-20260822-df31d954/` |
| 页面构建 | 26 frozen / 0 sample / 0 missing；Vite 432 modules | `make portfolio ...` + `npm run build` |
| 公网页面 | 新资源 `index-CuzGJ3vp.js` / `index-BpAKZrpg.css`；R13 数据为 500、59 事件、22 区 | 公网首页和 `/data/lookup.json` 回读 + 浏览器实测 |
| 代码门禁 | lint 通过；ML 定向 116 passed；离线全量 1,454 passed / 12 skipped / 1 deselected | `make lint`、`make test-ml`、`make test-unit-offline` |

对象存储回读摘要：

| 文件 | 字节 | SHA-256 |
|---|---:|---|
| `bootstrap_predictions.csv` | 24,844,233 | `55602443c86de866c929dc7cfb33888190fd4e1d426064ccfdf31fa8cc874358` |
| `bootstrap_coefficients.csv` | 207,781 | `44b688811902f846c4d4688b7d318bafd853c3b941c74014778d07039b296963` |
| `bootstrap_metadata.json` | 13,673 | `18d511b0f74e31e3530882c89e6639285230385fd94d047382196ec5a0cb7731` |

## 4. 回滚

- 页面：把当前 `/opt/uoip/uoip-portfolio-site` 移走，再将
  `/opt/uoip/uoip-portfolio-site.bak-20260927-pre-r13` 移回原名；无需重载 nginx。
- 模型层：F5 与 Gold 没有变化。R13 是既有版本目录下的三个追加对象；旧消费者不会读取。
- 本地真实面板和派生产物均在被 Git 忽略的 `var/`，不会进入提交。

## 5. 遗留项

- R13 本身无遗留，状态可标完成。
- 完整 `make test-unit` 仍有一项既有实时 NYC 311 API 测试失败：最新 50 行中 2 行缺少
  `incident_zip`；R13 未放宽该断言。离线全量与 R13 定向测试均通过。
- 77.922% 覆盖率、默认提示 0 和稳健集 0 必须原样进入后续 R1/R2 比较；不得把它们当成
  调整本轮阈值的理由。
