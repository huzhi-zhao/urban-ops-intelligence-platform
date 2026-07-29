

### 为数据源创建表，来explore_raw_data

> Raw Data应当必须使用NDJSON格式而非标准JSON

**① `Nested arrays not allowed`** GeoJSON 坐标是多层嵌套数组，BigQuery 外部表的 JSON 解析器不支持。 → 外部表无法直接读取含嵌套数组的字段，需改用原生表 + `LOAD DATA`。

---

**② `No such field: borocode`** 表的 Schema 里没有声明 `borocode`，外部表默认严格模式，遇到未知字段直接报错。 → Schema 必须覆盖文件里所有字段，或加 `ignore_unknown_values = TRUE`。

---

**③ `borough/geometry` 全部返回 null** Schema 字段名（`borough`, `geometry`）与文件实际字段名（`boroname`, `the_geom`）不匹配，加了 `ignore_unknown_values` 后不报错但全是 null。 → 先用 `gsutil cat ... | head -1` 确认文件真实字段名再建表。

---

**④ `JSON object specified for non-record field: the_geom`** 外部表不支持将 JSON 对象字段直接映射为 `GEOGRAPHY` 类型。 → 外部表无法做类型转换，改用原生表 + `LOAD DATA`。

---

**⑤ `Field shape_leng has changed type from STRING to FLOAT`** `LOAD DATA` 自动推断 `shape_leng` 为 `FLOAT`，与建表声明的 `STRING` 冲突。 → 建表时数值字段直接用 `FLOAT64`，不要用 `STRING`。

---

**⑥ `Field the_geom has changed type from STRING/JSON to RECORD`** `LOAD DATA` 自动推断 JSON 对象为 `RECORD`，覆盖了建表时声明的类型。 → 在 `LOAD DATA INTO 表名 (schema...)` 括号里显式声明字段类型，禁用自动推断。