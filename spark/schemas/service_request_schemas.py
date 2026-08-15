"""StructType definitions for SRC-WPG-311 / service_requests.

Grain is an **interaction**, not a case — ``case_id`` is 1.87% non-unique
(measured full-table); the Silver primary key is ``(case_id, interaction_id)``.
Deduplicating on ``case_id`` alone silently drops genuine interaction rows.
See contracts/api-contracts/winnipeg-311.yaml and contracts/silver-contracts/
silver_service_request.yaml.

Both timestamp fields on Bronze are Socrata *floating* timestamps — local
America/Winnipeg wall-clock, no offset, no 'Z'. ``open_date_local`` is derived
from the local calendar date, not the UTC date: partitioning on UTC date would
move late-evening requests into the next day.

``type``, ``channel_raw``, ``ward_raw`` and ``neighbourhood_raw`` are carried
verbatim. Winter-category, priority, channel-normalization and label-casefold
semantics all resolve in Gold (dim_service_type / dim_channel /
dim_admin_label) — the city-agnostic guardrail keeps business semantics out of
Silver and spark/transforms/.
"""

from __future__ import annotations

from pyspark.sql.types import (
    BooleanType,
    DateType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

# Bronze NDJSON shape, one record per interaction. All fields arrive as
# strings from Socrata, including the numeric-looking ones.
SERVICE_REQUEST_RAW_SCHEMA = StructType(
    [
        StructField("case_id", StringType(), nullable=False),
        StructField("interaction_id", StringType(), nullable=False),
        StructField("open_date", StringType(), nullable=False),
        StructField("closed_date", StringType(), nullable=True),
        StructField("case_status", StringType(), nullable=False),
        StructField("subject", StringType(), nullable=False),
        StructField("reason", StringType(), nullable=False),
        StructField("type", StringType(), nullable=False),
        StructField("channel_type", StringType(), nullable=False),
        StructField("neighbourhood", StringType(), nullable=True),
        StructField("ward", StringType(), nullable=True),
        # geometry arrives as a GeoJSON Point object in Bronze; the point-in-
        # polygon join that derives geo_match_status / plow_zone happens in
        # the Silver build job, not in this schema.
    ]
)

# Silver grain: one row per (case_id, interaction_id). Partitioned by
# open_date_local (see contract note above).
SERVICE_REQUEST_SILVER_SCHEMA = StructType(
    [
        StructField("case_id", StringType(), nullable=False),
        StructField("interaction_id", StringType(), nullable=False),
        StructField("open_ts_utc", TimestampType(), nullable=False),
        StructField("open_date_local", DateType(), nullable=False),  # partition column
        StructField("closed_ts_utc", TimestampType(), nullable=True),
        StructField("type", StringType(), nullable=False),
        StructField("channel_raw", StringType(), nullable=False),
        StructField("has_geo", BooleanType(), nullable=False),
        # matched / unmatched / no_geo — three-value, never a bare NULL. See
        # CLAUDE.md "Escalate to human" guardrail: alert denominators must be
        # the has_geo subset, not the full table (79% carry no coordinates).
        StructField("geo_match_status", StringType(), nullable=False),
        StructField("plow_zone", StringType(), nullable=True),  # set iff geo_match_status = matched
        StructField("ward_raw", StringType(), nullable=True),
        StructField("neighbourhood_raw", StringType(), nullable=True),
        StructField("source_id", StringType(), nullable=False),
        StructField("loaded_at", TimestampType(), nullable=False),
    ]
)
