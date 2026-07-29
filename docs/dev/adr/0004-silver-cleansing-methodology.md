# ADR 0004 — Silver 清洗规则的制定方法论

> **Status**: Accepted · **Date**: 2026-06

决策：写转换代码之前先做数据探查 + 冻结 schema 契约，而不是先写 transform
再迭代修。取舍理由：上游 Socrata 字段历史上出现过漂移，先探查能避免返工。
执行链路见 [ADR 0005](0005-silver-execution-architecture.md)。

---

## To Silver 前置操作

  
在写 Silver 清洗代码之前，企业级做法通常会先做这几件事（不是写代码，是探查+定契约）：  
  
1. **Explore Bronze 真实数据**：抽样读几天/几月的原始 JSON（311、NYPD、Open-Meteo、DCP），核对字段名、类型、空值率、时间字段格式是否和 `ingestion/schemas/` 里的 Pydantic 模型一致——Socrata 字段经常会有 null 或类型漂移。  
2. **补上 `contracts/`**：AGENTS.md 里明确要求写 Spark 代码前先看 `contracts/`，但这个目录还不存在，得先建（source-registry + 各数据集的 schema 契约）。  
3. **定义 `spark/schemas/` 的 StructType**：基于探查结果而不是凭记忆,对应每个数据集的 Silver schema。  
4. **定义 `sql/ddl/`**：Gold 表结构要提前定好，尤其 `dim_geography` 的 borough 空间字段。  
5. **明确分区/去重/幂等策略**：Silver 按 date 分区，哪个字段做唯一键去重（避免 7 天回溯窗口造成重复）。  
  
这一步的主要权衡是：花时间先探查数据 vs 直接写转换代码再迭代修。鉴于 Socrata 字段历史上出过变更（CLAUDE.md 里也提到"上游 schema 变更需升级人类"这条红线），我建议先探查 + 定 contracts/schemas,再写 transform,这样能避免后面重写。




### 1. Bronze 数据质量审计（Data Profiling）

在写任何转换代码之前，先摸清楚原始数据的真实面貌：

- 每个字段的 null 率是多少？
- 时间戳字段有没有异常值（1970-01-01、9999-12-31）？- 
- Borough 字段有没有脏数据（"BRONX" vs "Bronx" vs "BX"）？- 
- 数值字段的分布（min/max/mean）是否在合理范围？- 
- 每天的记录数是否连续，有没有某几天数据量异常低（API 断档）？

工具：Great Expectations / dbt tests / 自己写 PySpark profiling job


#### BigQuery Autodetect
BigQuery 的 **External Table** 功能直接指向 GCS 上的 Bronze 文件(`gs://nyc-uoip-prod/bronze/raw/{sid}/{ds}/...`),



---

### 2. Schema Contract 冻结

确认 Bronze 实际字段 与 `contracts/api-contracts/` 里记录的一致，**签字锁定**：

- 上游 API 有没有悄悄加字段、改字段名
- Pydantic 模型是否还准确反映现实
- Silver StructType 定义基于的是真实数据，不是文档假设

---

### 3. 采样验证（Sampling）

不跑全量，先抽一个月数据跑通 ETL 逻辑，验证：

- 空间 JOIN 命中率（ST_CONTAINS 覆盖了多少条记录）
- 时间戳标准化后有没有时区错位
- 输出行数与输入行数的比例是否合理

---

### 4. SLA 与数据量基线建立

记录每个源每天/每月的预期行数，用于后续告警阈值：

```
311:   ~8,000 条/天NYPD:  ~300 条/天Weather: 24 条/天（每小时一条）
```

Silver job 跑完后输出 0 行或低于基线 50% → 自动告警。