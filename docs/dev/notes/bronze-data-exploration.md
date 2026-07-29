

### 第一步：为4个数据源各建一张外部表

在 `explore` dataset 里，每个数据源建一张外部表：

```
-- 311
CREATE OR REPLACE EXTERNAL TABLE `pace-lab-bdp.explore.raw_nyc_311`
OPTIONS (format = 'NEWLINE_DELIMITED_JSON',
  uris = ['gs://nyc-uoip-prod/bronze/raw/SRC-NYC-311/*/*/data_*.ndjson']);

-- NYPD 碰撞
CREATE OR REPLACE EXTERNAL TABLE `pace-lab-bdp.explore.raw_nypd_collisions`
OPTIONS (format = 'NEWLINE_DELIMITED_JSON',
  uris = ['gs://nyc-uoip-prod/bronze/raw/SRC-NYPD/*/*/data_*.ndjson']);

-- 天气
CREATE OR REPLACE EXTERNAL TABLE `pace-lab-bdp.explore.raw_weather`
OPTIONS (format = 'NEWLINE_DELIMITED_JSON',
  uris = ['gs://nyc-uoip-prod/bronze/raw/SRC-Open-Meteo/*/*/data_*.ndjson']);

-- Borough 边界
CREATE OR REPLACE EXTERNAL TABLE `pace-lab-bdp.explore.raw_borough_boundaries`
OPTIONS (format = 'NEWLINE_DELIMITED_JSON',
  uris = ['gs://nyc-uoip-prod/bronze/raw/SRC-DCP/*/*/data_*.ndjson']);
```

### 第二步：逐源做数据探索

每个表跑以下几类查询，目的是**摸清字段、质量、分布**：

#### 1. 看结构（字段名/类型）


```sql
SELECT * FROM `pace-lab-bdp.explore.raw_nyc_311` LIMIT 5;
```
#### 2. 看数据量和时间范围

```sql
SELECT
  COUNT(*) AS total_rows,
  MIN(created_date) AS earliest,
  MAX(created_date) AS latest
FROM `pace-lab-bdp.explore.raw_nyc_311`;
```

#### 3. 看NULL率（字段完整性）
```sql
SELECT
  COUNTIF(unique_key IS NULL) / COUNT(*) AS null_rate_key,
  COUNTIF(complaint_type IS NULL) / COUNT(*) AS null_rate_complaint,
  COUNTIF(latitude IS NULL) / COUNT(*) AS null_rate_lat
FROM `pace-lab-bdp.explore.raw_nyc_311`;
```

#### 4. 看字段基数（适不适合做维度/分区）

```sql
SELECT borough, COUNT(*) AS cnt
FROM `pace-lab-bdp.explore.raw_nyc_311`
GROUP BY borough ORDER BY cnt DESC;
```

#### 5. 找重复（主键是否唯一）

```sql
SELECT unique_key, COUNT(*) AS cnt
FROM `pace-lab-bdp.explore.raw_nyc_311`
GROUP BY unique_key HAVING cnt > 1;
```

---

### 第三步：记录探索结论，设计表结构

探索完每个源后，你需要回答这几个问题来决定表设计：


|问题|决定什么|
|---|---|
|主键是什么？是否唯一？|Silver层去重逻辑|
|时间字段是什么格式？|分区键选择|
|哪些字段NULL率高？|是否需要默认值/过滤|
|哪些字段基数低？|适合做聚合维度（borough、complaint_type）|
|4个源之间如何关联？|Gold层宽表的JOIN键设计|

---

### 整体节奏建议

```
外部表（explore）
    ↓ 探索清楚后
Silver 原生表（按日期分区，清洗后）
    ↓
Gold 宽表（跨源JOIN，面向分析）
```