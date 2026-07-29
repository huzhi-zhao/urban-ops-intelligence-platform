

### Airflow 核心概念速通（Java 视角）

|Airflow 概念|Java 类比|本项目对应|
|---|---|---|
|**DAG**|一个 `@Configuration` 类，定义任务流|`dag_backfill_nyc_311.py`|
|**Task / Operator**|一个 `@Bean` 方法，定义一个工作单元|`PythonOperator` 调用 `backfill_daily_window`|
|**DAG Run**|一次任务执行实例（带时间戳）|你手动触发一次 = 一次 Run|
|**Params**|运行时注入的参数（类似 Spring `@Value`）|`start=2024-01-01, end=2025-01-01`|
|**XCom**|任务间传值的消息总线|本项目暂不需要|
|**schedule**|Cron 表达式|backfill 场景设为 `None`（纯手动）|

关键认知：**Airflow 本身不执行业务逻辑**——它只是一个任务调度器。我们已有的 `bulk.py` 才是干活的，DAG 只是"触发器 + 监控面板"。



#### Airflow是如何部署到Composer的
Composer 2 的 Worker Pod 已经预装了 Python 3.x + Airflow。业务代码（ingestion/、scripts/、config/）通过 gsutil rsync 放到 plugins/ 目录，Composer 自动把它加到 PYTHONPATH，等价于你在本地 export PYTHONPATH=. 然后跑脚本。

```shell
airflow tasks run dag_backfill_nyc_311 run_backfill 2024-01-01
  └─ Airflow 在 Worker Pod 里 import DAG 文件
  └─ 找到对应 Task，执行 callable（如 PythonOperator 的 python_callable）
  └─ callable 里 from ingestion.backfill import BackfillFacade ← 从 plugins/ 目录 import
  └─ 直接调用 Python 函数
```

> 代码通过 `gsutil rsync` 上传到composer实例，每次执行rsync命令只上传有变更的文件

|方式|Composer 用吗|适合场景|
|---|---|---|
|直接 import Python 文件|✅ 就是这个|纯 Python 任务、小中型 ETL|
|DockerOperator / KubernetesPodOperator|可以选用|依赖复杂、需隔离环境|
|打包 wheel 安装|可以选用|大型项目、有 C 扩展依赖|


### DAG
|`   hello_airflow.py`|`hello_airflow_python.py`|
|---|---|
|Operator|`BashOperator`|`PythonOperator`|
|任务怎么写|`bash_command='echo "extract work"'` —— 跑一条 shell 命令|`python_callable=extract_data` —— 调一个 Python 函数|
|适合场景|调用现成的 shell 脚本/系统命令(比如 `spark-submit`、`curl`、`gsutil cp`)|需要用 Python 写业务逻辑(调 API、解析 JSON、写 Pandas 处理)|
|Schedule|`timedelta(days=1)`(固定周期表达)|`"@daily"`(cron 预设别名,等价于每天跑一次)|
|任务数/流程|3 个任务:extract → transform → load|4 个任务:extract → clean → transform → load(多了一步 cleaning)|



`@task` 装饰器(TaskFlow API)和 `PythonOperator` 不是两套不同的东西——**`@task` 本质上就是 `PythonOperator` 的语法糖,底层执行机制完全一样**,只是写法更省事,而且这次想加的分支/循环用 TaskFlow 写起来明显更顺手:

## 为什么这次换了写法

1. **自动传值,不用手写 XCom**
    - 旧写法(`PythonOperator`):函数之间传数据要手动 `ti.xcom_push(...)` / `ti.xcom_pull(...)`,啰嗦。
    - TaskFlow:函数 `return` 什么,下游函数直接当参数接,比如 `sum_cleaned(cleaned)` 里的 `cleaned` 就是 `clean_item` 的返回值,框架自动用 XCom 搬运。
2. **`.expand()` 动态映射只支持 TaskFlow 风格的任务**
    - 你要的"循环"(对列表逐项生成并行任务实例)是 `@task` 函数才能 `.expand()`。`PythonOperator` 也能做动态映射(`PythonOperator.partial(...).expand(...)`),但写法更绕,官方教程现在主推 `@task`。
3. **`@task.branch` 是 `BranchPythonOperator` 的同款语法糖**
    - 一行装饰器替代 `BranchPythonOperator(task_id=..., python_callable=...)` 那一整段。

## 它们是同一回事吗

是。`@task` 编译后,内部就是创建一个 `PythonOperator`(确切说现在是 `_PythonDecoratedOperator`,继承自它)。所以你前面看到的 `hello_airflow_python.py` 第一版(显式 `PythonOperator(task_id=..., python_callable=...)`)**完全没错,也能正常跑**,只是这次想顺便演示分支+循环,TaskFlow 写法更紧凑、更接近官方现在的教程范例。