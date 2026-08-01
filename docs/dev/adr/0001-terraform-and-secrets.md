# ADR 0001 — Terraform 管理 GCP 基础设施与密钥

> **Status**: **Superseded by [0006](0006-storage-compute-query-stack.md)** · **Date**: 2026-06
>
> GCP 已整体放弃，`infra/terraform/` 退役，Secret Manager 不再是密钥载体。
> 本篇保留作为决策历史。其中**仍然有效**的部分是密钥管理的原则本身：
> 凭据不落任何 git-tracked 文件、不写入任何会被序列化的状态文件。
> 该原则在新栈下的落地方式见 ADR 0006 §8.3。

决策：GCP 资源全部用 Terraform 声明；敏感凭据不落任何文件，改由 GCP Secret
Manager 托管，运行时注入。被否决的方案是把 token 写在 git-tracked 的
`terraform.tfvars` 里（明文入库），以及用 `env_variables` 注入（明文进
Terraform state）。

---

### Secret
Secret 是在GCP云端维护类似.env的配置

|项目|修复前|修复后|
|---|---|---|
|`terraform.tfvars`|git tracked，含 `socrata_app_token`|git untracked（`git rm --cached`），加入 `.gitignore`|
|Secret 存储位置|tfvars 文件（明文）|GCP Secret Manager（`secret: socrata-app-token`）|
|Composer 注入方式|`env_variables`（写入 Terraform state 明文）|`secret_environment_variables`（运行时从 SM 读取）|
|SA 权限|无|新增 `roles/secretmanager.secretAccessor`|

#### 添加一个secret 
**你在 `terraform apply` 之前还需要手动做一次：**

```
# 创建 Secret（只做一次）
gcloud secrets create socrata-app-token --project=pace-lab-bdp
# 写入 token 值
echo -n "your_actual_token" | \  
    gcloud secrets versions add socrata-app-token \  
    --data-file=- --project=pace-lab-bdp
```

之后 Terraform 会自动从那里读取，注入到 Composer，不需要任何文件存储。

#### 本地.env云端如何配置
**`.env` 里的配置在云端怎么处理**

`.env` 是**纯本地开发文件**，进云端之后它整个"退休"，每个变量有不同的替代方案：

|`.env` 变量|云端替代方案|原因|
|---|---|---|
|`GOOGLE_APPLICATION_CREDENTIALS`|**不需要**|Composer 运行在 GCP 内，SA 身份由 node_config 自动绑定，不需要 key 文件|
|`SOCRATA_APP_TOKEN`|**Secret Manager**|敏感凭据，已在 Terraform 里用 `secret_environment_variables` 注入|
|`GCS_BUCKET_NAME`|**Composer env_variables**（Terraform 管）|非敏感配置，直接写 Terraform 就行|
|`DEPLOYMENT_PHASE`|**Composer env_variables**（Terraform 管）|非敏感配置，同上|

结论：**`.env` 只给本地 `uv run` / `make test-unit` 用**，云端完全不需要这个文件，Terraform 已经把所有值注入到 Composer 里了。