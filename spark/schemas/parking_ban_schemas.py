"""StructType definitions for SRC-WPG-PARKING-BAN / parking_bans.

Static reference table: 49 rows, full overwrite on every re-pull — see
contracts/api-contracts/winnipeg-parking-bans.yaml and contracts/
silver-contracts/silver_parking_ban.yaml.

Only 19 of 49 bans have a matching plow_shift row (id -> snow_ban_id); the
other 30 are bans without a city-wide clearing operation. That is expected
semantics (BO-2), not a join defect — any consumer must left-join from this
table, never inner-join.
"""

from __future__ import annotations

from pyspark.sql.types import (
    StringType,
    StructField,
    StructType,
    TimestampType,
)

# Bronze NDJSON shape, one record per ban. description_french and the
# 3-valued description text are dropped in Silver — ban_type_id carries the
# same distinction without a bilingual free-text column.
PARKING_BAN_RAW_SCHEMA = StructType(
    [
        StructField("id", StringType(), nullable=False),
        StructField("description", StringType(), nullable=False),
        StructField("description_french", StringType(), nullable=False),
        StructField("ban_type_id", StringType(), nullable=False),  # numeric-looking string; domain 1/2/4
        StructField("ban_start", StringType(), nullable=False),
        StructField("ban_end", StringType(), nullable=True),
    ]
)

# Silver grain: one row per ban id. Not partitioned — static source, full
# overwrite.
PARKING_BAN_SILVER_SCHEMA = StructType(
    [
        StructField("id", StringType(), nullable=False),
        StructField("ban_start_utc", TimestampType(), nullable=False),
        StructField("ban_end_utc", TimestampType(), nullable=True),
        StructField("ban_type_id", StringType(), nullable=False),
        StructField("source_id", StringType(), nullable=False),
        StructField("loaded_at", TimestampType(), nullable=False),
    ]
)
