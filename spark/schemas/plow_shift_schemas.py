"""StructType definitions for SRC-WPG-PLOW-SHIFT / plow_shifts.

Static reference table: 418 rows (19 city-wide plow operations x 22 scheduled
zones), full overwrite on every re-pull — see contracts/api-contracts/
winnipeg-plow-shifts.yaml and contracts/silver-contracts/silver_plow_shift.yaml.

``plow_zone`` here has cardinality 22, not the boundary table's 25: ``X``,
``B/D`` and ``Downtown`` never appear — they have no scheduled shift at all,
not a "last shift" assignment. ``shift_number`` is the core supply-side
quantity (BO-2): ordering by it matches ordering by ``shift_start`` exactly.
"""

from __future__ import annotations

from pyspark.sql.types import (
    IntegerType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

# Bronze NDJSON shape, one record per shift. shift_number arrives as a
# numeric-looking string, per the Socrata contract.
PLOW_SHIFT_RAW_SCHEMA = StructType(
    [
        StructField("id", StringType(), nullable=False),
        StructField("snow_ban_id", StringType(), nullable=False),
        StructField("shift_number", StringType(), nullable=False),
        StructField("shift_start", StringType(), nullable=False),
        StructField("shift_end", StringType(), nullable=False),
        StructField("plow_zone", StringType(), nullable=False),
    ]
)

# Silver grain: one row per shift id. Not partitioned — static source, full
# overwrite.
PLOW_SHIFT_SILVER_SCHEMA = StructType(
    [
        StructField("id", StringType(), nullable=False),
        StructField("snow_ban_id", StringType(), nullable=False),  # left-join key -> parking_ban.id
        StructField("shift_number", IntegerType(), nullable=False),  # domain 1..5
        StructField("shift_start_utc", TimestampType(), nullable=False),
        # Planned end of a 12h template window, not a completion time —
        # identical across all zones in the same shift_number (ADR 0008). Do
        # not derive a duration/completion metric from this.
        StructField("shift_end_utc", TimestampType(), nullable=False),
        StructField("plow_zone", StringType(), nullable=False),  # 22 distinct values
        StructField("source_id", StringType(), nullable=False),
        StructField("loaded_at", TimestampType(), nullable=False),
    ]
)
