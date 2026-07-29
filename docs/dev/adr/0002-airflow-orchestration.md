# ADR 0002 — 用 Airflow 编排回填与增量摄取

> **Status**: Accepted · **Date**: 2026-06

决策：调度层用 Airflow，DAG 只做触发与参数渲染，切片和业务逻辑留在
`scripts/backfill/bulk.py`。部署形态见 [ADR 0005](0005-silver-execution-architecture.md)——
Cloud Composer 已被自建 Docker Airflow 取代。

---

![airflow-architexture-overview](../../images/backfill-architecture.png)

### GCP Composer 启动
```shell
# 相当于 `cd infra/terraform && terraform apply`
make terraform-apply

# 3. 等创建完成后，部署代码
make deploy-composer

# 4. 浏览器打开 Airflow UI
terraform -chdir=infra/terraform output composer_airflow_uri
```

### VM端Docker部署

```shell
cd infra/docker
docker compose run --rm airflow-init
docker compose up -d airflow-webserver airflow-scheduler
```
