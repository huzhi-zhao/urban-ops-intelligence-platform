# infra/docker

UOIP 的计算节点栈：Airflow（webserver / scheduler / dag-processor + Postgres）
和一个 Spark worker。Compose project name 固定为 `uoip`。

## 和共享大数据平台的边界

这台机器上还跑着一套共享的大数据平台（`/opt/pace_ai_lab/docker`：Hadoop、Hive、
Kafka、Flink、Trino、Superset、MongoDB，以及 **Spark master**）。两边通过同一个
外部 Docker 网络 `bigdata-net` 互通。

划分标准：**这个容器的镜像内容是不是由项目代码决定的。**

| 组件 | 在哪 | 理由 |
|---|---|---|
| Spark **master** | 平台 `pace_ai_lab/docker/spark` | 只做调度协调，不执行用户代码 |
| Spark **worker** | **本栈**（`spark-worker-uoip`） | 执行本项目的 Python UDF —— 见 `Dockerfile.spark-worker`：从源码编 Python 3.11 是为了匹配本项目 driver 的版本，装 shapely 是为了 `spark/transforms/dcp.py` |
| Airflow | **本栈** | 装的是本项目的 provider，挂的是本项目的 `dags/` |

历史上两边的 compose 都定义了 `spark-master` / `spark-worker`，容器名在
`bigdata-net` 上互撞，实际跑成了"master 用平台的、worker 用项目的"这种半吊子
状态，导致 master 那段 `spark.ui.reverseProxy` 配置从来没生效过。现在 master
只由平台定义，本栈的 worker 改名 `spark-worker-uoip`。

已知天花板：Spark standalone 无法按项目路由 executor，任何 app 都可能落到任何
worker 上。这套只在各项目 worker 的 Python 版本一致时成立。

## 项目级容器命名规范

`bigdata-net` 是跨项目共享网络，Compose 会**自动把 service 名注册成网络别名**
（无法关闭），所以任何本栈 service 名如果撞上平台侧或别的项目的 service 名，
Docker 内置 DNS 会在两个容器间**轮询解析**——不是报错，是偶尔连对、偶尔连错，
两边密码不同就表现成间歇性 `password authentication failed`，比直接连不上更难查。
spark-master/worker 是第一次撞（见上一节），2026-08-17 又在 `postgres` 上撞了
一次：本栈的 Postgres service 曾经就叫 `postgres`，平台侧 `platform-postgres`
也把自己起了同一个别名，`getent hosts postgres` 能同时查到两个 IP。

**规则**：本栈任何要在 `bigdata-net` 上暴露的 service，名字必须带 `uoip-` 前缀
或者本身已经足够具体（如 `spark-worker-uoip`），不能用 `postgres` / `redis` /
`mysql` 这类通用名——通用名假设自己是网络上唯一的那个，共享网络上这个假设不成立。
`postgres` service 已按此改名为 `uoip-postgres`（`AIRFLOW__DATABASE__SQL_ALCHEMY_CONN`
与 `depends_on` 一并更新，具名 volume `uoip_postgres_db` 不受影响）。

**待办**：评估把本栈的项目内部服务（Postgres、以及未来任何不需要被平台或其他
项目访问的组件）迁到一个**不和 `bigdata-net` 共享**的私有网络，只让真正需要
跨项目可见的服务（目前是 Spark worker，靠 `bigdata-net` 连平台的 spark-master）
留在共享网络上。前缀命名是当前这次的最小修复，能防止"撞名→静默连错"，但不能
防止本栈的内部服务被平台侧其他项目意外发现或连接——网络隔离才是把攻击面/耦合面
收紧到设计边界，前缀命名只是把症状压下去。

## 启动

前提：`bigdata-net` 已存在，且平台侧的 spark-master 在跑。

```bash
docker compose -f /opt/pace_ai_lab/docker/spark/docker-compose.yml up -d
```

⚠️ `.env` 只放在**项目根目录**，`infra/docker/` 下没有、也不应该有这个文件。
`--env-file` 是相对**运行 `docker compose` 时的 cwd** 解析的，不是相对
`-f` 指向的 compose 文件所在目录。所以正确用法是在**仓库根目录**下跑：

```bash
docker compose --env-file .env -f infra/docker/docker-compose.yml up -d
```

而不是 `cd infra/docker` 之后再用 `--env-file infra/docker/.env` 或
`--env-file .env`——那个目录下根本没有 `.env`，会导致每个 `${VAR:?}` 直接
中止。优先用 `make stack-up`（见根 `Makefile` 的 `COMPOSE` 变量，同样固定
`--env-file .env -f infra/docker/docker-compose.yml`，且要求从仓库根目录调用）；
`name: uoip` 写在 compose 文件里，所以不需要 `-p`。

## 从旧的 `docker` project 迁移（一次性）

改 project name 之前，卷叫 `docker_postgres-db` / `docker_airflow-logs`；
现在固定成 `uoip_postgres_db` / `uoip_airflow_logs`。Airflow 元数据库里有
connections、variables 和 DAG run 历史，值得搬。

先停掉旧栈。旧 project 名是 compose 文件的父目录名 `docker`，`make stack-down`
（现在指向 `uoip`）**看不见它们**，必须走专门的 target：

```bash
make stack-down-legacy
```

它先 `docker compose -p docker ... down`，再按 `com.docker.compose.project=docker`
标签把剩下的容器扫干净 —— 新 compose 文件里已经没有 `spark-master` 了，光靠
`down` 删不掉那个残留容器，这也是 `make stack-up` 报
`container name /spark-master is already in use` 的原因。

容器必须先停，再拷 Postgres 数据卷，否则拷到的是运行中的库文件。确认旧卷还在：

```bash
docker volume ls | grep -E 'docker_(postgres-db|airflow-logs)'
```

建新卷并拷数据：

```bash
docker volume create uoip_postgres_db && docker run --rm -v docker_postgres-db:/from:ro -v uoip_postgres_db:/to alpine sh -c 'cp -a /from/. /to/'
```

```bash
docker volume create uoip_airflow_logs && docker run --rm -v docker_airflow-logs:/from:ro -v uoip_airflow_logs:/to alpine sh -c 'cp -a /from/. /to/'
```

起新栈、确认 DAG 和 connection 都在之后，再删旧卷：

```bash
docker volume rm docker_postgres-db docker_airflow-logs
```

如果不在乎历史，跳过上面整节，直接 `docker compose up -d`，`airflow-init`
会在空库上重新 migrate 并建管理员。

## 反向代理

`airflow.huzhi.dev` → 宿主机 28080，nginx 配置在平台仓库的
`pace_ai_lab/docker/nginx/big-data.huzhi.conf`。`AIRFLOW__API__BASE_URL`
必须和对外域名一致（通过 `.env` 的 `AIRFLOW_BASE_URL` 设置），否则静态资源和
登录重定向会指向 `http://localhost:28080`。
