"""StructType definitions for the Gold fact tables (TBL-F1..F8, ADR 0010
D1 / design/20260809-gold-silver-schema-derivation.md §4.3).

Every fact carries three audit columns — ``etl_run_id``, ``built_at`` (UTC),
``source_max_ingest_date`` — per ADR 0010 D7, repeated verbatim per schema for
the same reason as gold_dim_schemas.py.

None of these StructTypes may include ``ward`` / ``neighbourhood`` /
``region_type`` (ADR 0010 D2 — administrative units never enter a
scoring-chain fact table's key). ``fact_winter_request_daily_by_label`` is the
one deliberate exception: it is a standalone descriptive slice that shares no
column with the scoring chain, not a scoring-chain fact.
"""

from __future__ import annotations

from pyspark.sql.types import (
    DateType,
    DoubleType,
    IntegerType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

# TBL-F2. The core supply-side quantity (BO-2). Grain (plow_event_id, plow_zone).
FACT_EVENT_ZONE_RANK_GOLD_SCHEMA = StructType(
    [
        # One of the 19 city-wide plow operations, distinct from event_id
        # (the ~99 snowfall events).
        StructField("plow_event_id", StringType(), nullable=False),
        StructField("plow_zone", StringType(), nullable=False),  # cardinality 22
        StructField("shift_number", IntegerType(), nullable=False),  # domain [1, 2, 3, 4, 5]
        # Formula: shift_number / 5 (fixed denominator, not min-max over
        # observed shift numbers). Range [0.2, 1], never 0 — shift_number's
        # domain floor of 1 makes 0 structurally unreachable. NULL for any
        # plow-event outside the 19 known ones or for dates before 2015-12 —
        # a NULL here is the *only* correct missing representation (BO-6).
        StructField("rank_factor", DoubleType(), nullable=True),
        # NULL for the two known-unaligned operations (2021-01-07,
        # 2026-02-26) — expected, must be surfaced not hidden. Denormalized
        # copy of dim_plow_event.matched_snowfall_event_id; a consumer
        # joining fact_winter_event_zone_load in via this column must join
        # through dim_plow_event, not this table directly.
        StructField("matched_snowfall_event_id", StringType(), nullable=True),
        StructField("etl_run_id", StringType(), nullable=False),
        StructField("built_at", TimestampType(), nullable=False),
        StructField("source_max_ingest_date", DateType(), nullable=False),
    ]
)

# TBL-F4. Independent table, left-joined to fact_plow_shift — never merged
# into it (BO-2).
FACT_PARKING_BAN_GOLD_SCHEMA = StructType(
    [
        StructField("ban_id", StringType(), nullable=False),
        StructField("ban_start_utc", TimestampType(), nullable=False),
        StructField("ban_end_utc", TimestampType(), nullable=True),
        StructField("ban_type_id", StringType(), nullable=False),
        # NULL for 30/49 rows — a ban without a city-wide clearing operation.
        # This is semantics, not a data gap. Never inner-join on this.
        StructField("matched_plow_event_id", StringType(), nullable=True),
        StructField("etl_run_id", StringType(), nullable=False),
        StructField("built_at", TimestampType(), nullable=False),
        StructField("source_max_ingest_date", DateType(), nullable=False),
    ]
)

# TBL-F3. Traceability detail behind fact_event_zone_rank's rank (BO-2) —
# mostly a Gold passthrough of silver_plow_shift plus audit columns.
FACT_PLOW_SHIFT_GOLD_SCHEMA = StructType(
    [
        StructField("shift_id", StringType(), nullable=False),
        StructField("plow_event_id", StringType(), nullable=False),
        StructField("plow_zone", StringType(), nullable=False),
        StructField("shift_number", IntegerType(), nullable=False),  # domain [1, 2, 3, 4, 5]
        StructField("shift_start_utc", TimestampType(), nullable=False),
        # Planned end of window, not completion. Do not derive a duration or
        # completion-time metric from this — ADR 0008.
        StructField("shift_end_utc", TimestampType(), nullable=False),
        StructField("etl_run_id", StringType(), nullable=False),
        StructField("built_at", TimestampType(), nullable=False),
        StructField("source_max_ingest_date", DateType(), nullable=False),
    ]
)

# TBL-F7. BO-8's output — carries its own baseline and delta so a
# "beats baseline" claim never requires a separate recomputation (ADR 0010 D5).
FACT_RECOMMENDATION_GOLD_SCHEMA = StructType(
    [
        StructField("event_id", StringType(), nullable=False),
        StructField("plow_zone", StringType(), nullable=False),
        # Which M1 version's forecast drove this recommendation — never
        # overwritten in place, same discipline as
        # fact_request_forecast.model_version.
        StructField("model_version", StringType(), nullable=False),
        # This event's zones ordered by the model-driven recommendation.
        StructField("rank_model", IntegerType(), nullable=False),
        # Same event's zones ordered by the "historical average request
        # volume" baseline — stored on the same row, never recomputed live.
        StructField("rank_baseline", IntegerType(), nullable=False),
        # rank_baseline - rank_model. "Beats baseline" is an internal target
        # only (BO-8 §0.2.2), not an external claim.
        StructField("rank_delta", IntegerType(), nullable=False),
        StructField("attribution_rule_id", StringType(), nullable=False),
        # Must not read as an unfairness claim — BO-8 表述纪律 #3. Attributes to
        # schedule ordering / weather severity / predicted demand only, never
        # to policy failure.
        StructField("attribution_text", StringType(), nullable=False),
        StructField("etl_run_id", StringType(), nullable=False),
        StructField("built_at", TimestampType(), nullable=False),
        StructField("source_max_ingest_date", DateType(), nullable=False),
    ]
)

# TBL-F5. M1's output, stored separately from the score table so a model
# version change never breaks backtest reproducibility (ADR 0010 D5).
FACT_REQUEST_FORECAST_GOLD_SCHEMA = StructType(
    [
        StructField("event_id", StringType(), nullable=False),
        StructField("plow_zone", StringType(), nullable=False),
        # Never overwritten in place — every retrain is a new row set,
        # enabling BO-8's historical backtest requirement.
        StructField("model_version", StringType(), nullable=False),
        # SUM(request_count) across all 6 effective winter_category values
        # for this (event_id, plow_zone) — M1 predicts at this coarser grain
        # than fact_service_request_zone_event trains on (launch doc
        # 20260813 B3).
        StructField("predicted_count", DoubleType(), nullable=False),
        # Seasonal-naive baseline for the same (event_id, plow_zone) — stored
        # on the same row per ADR 0010 D5's "no model metric without a
        # baseline, never recomputed live" discipline. Null only for the
        # earliest events with no prior history to average over.
        StructField("baseline_count", DoubleType(), nullable=True),
        # Backfilled once ground truth is known for the event; null for
        # future/unelapsed events.
        StructField("actual_count", IntegerType(), nullable=True),
        StructField("etl_run_id", StringType(), nullable=False),
        StructField("built_at", TimestampType(), nullable=False),
        StructField("source_max_ingest_date", DateType(), nullable=False),
    ]
)

# TBL-F1. M1's training panel (BO-1). Full panel including zero-request
# cells — zero is a training signal, not a gap to backfill downstream.
FACT_SERVICE_REQUEST_ZONE_EVENT_GOLD_SCHEMA = StructType(
    [
        StructField("event_id", StringType(), nullable=False),
        # 22 values only — has_plow_schedule = true. Not 25; see dim_plow_zone.
        StructField("plow_zone", StringType(), nullable=False),
        StructField("winter_category", StringType(), nullable=False),
        # Zero is a valid, meaningful value — not a placeholder for missing.
        StructField("request_count", IntegerType(), nullable=False),
        # request_count weighted by dim_service_type.priority_weight.
        StructField("weighted_request_count", DoubleType(), nullable=False),
        StructField("etl_run_id", StringType(), nullable=False),
        StructField("built_at", TimestampType(), nullable=False),
        StructField("source_max_ingest_date", DateType(), nullable=False),
    ]
)

# TBL-F6. The flagship deliverable — Winter Operational Load Score (BO-6).
# Full panel, missing expressed via score_status, never via row absence.
FACT_WINTER_EVENT_ZONE_LOAD_GOLD_SCHEMA = StructType(
    [
        StructField("event_id", StringType(), nullable=False),
        StructField("plow_zone", StringType(), nullable=False),
        # Null when score_status != scored — never a fabricated 0.
        StructField("load_score", DoubleType(), nullable=True),
        # Comparable only within the same score_weight_profile — see that
        # column. 71.2% of the panel (partial_no_rank) is scored on a 0.70
        # weight sum, not 1.0, so its load_score/load_level are
        # systematically lower than a scored row's at equal underlying
        # severity.
        StructField("load_level", StringType(), nullable=True),  # domain LOW/MED/HIGH/CRITICAL
        # full_3factor = forecast (0.40) + rank (0.30) + weather (0.30), 1:1
        # with score_status = 'scored'. demand_weather_only = forecast +
        # weather only, weights NOT renormalized to sum to 1 (BO-6 forbids
        # silent renormalization), 1:1 with score_status = 'partial_no_rank'.
        # Never compare load_score/load_level across the two profiles.
        StructField("score_weight_profile", StringType(), nullable=False),
        # Three states: 'scored' = all three factors present (one of the 19
        # known plow operations); 'partial_no_rank' = scheduling-era event
        # (2015-12+) with no matching plow operation — the more common case;
        # 'no_schedule_era' reserved for a future zone that loses schedule
        # coverage entirely. Pre-2015-12 events never appear in this table.
        StructField("score_status", StringType(), nullable=False),
        StructField("request_forecast_factor", DoubleType(), nullable=True),
        # Formula: shift_number / 5 (fixed denominator — see
        # fact_event_zone_rank.rank_factor). NULL when no schedule data
        # exists for this (event, zone); never 0 by construction.
        StructField("rank_factor", DoubleType(), nullable=True),
        # H1: degraded to an event-level constant, equal to
        # dim_snowfall_event.severity_score for every plow_zone under the
        # same event_id — silver_weather_archive is a single citywide point,
        # no zone-grained archive exists yet (launch doc 20260813 A2). Grained
        # at (event_id, plow_zone) for forward compatibility with a future
        # zone-level archive, not because H1's values actually vary by zone.
        StructField("weather_severity_factor", DoubleType(), nullable=True),
        # Foreign key, not an inlined predicted_count — ADR 0010 D5. Lets a
        # model version change be traced without breaking past scores.
        StructField("forecast_model_version", StringType(), nullable=True),
        StructField("etl_run_id", StringType(), nullable=False),
        StructField("built_at", TimestampType(), nullable=False),
        StructField("source_max_ingest_date", DateType(), nullable=False),
    ]
)

# TBL-F8. Pure descriptive slice, deliberately disjoint from the scoring
# chain (ADR 0010 D2's one exception) — winter-attributable requests only,
# not a general-purpose daily-by-label rollup for all 311 traffic.
FACT_WINTER_REQUEST_DAILY_BY_LABEL_GOLD_SCHEMA = StructType(
    [
        StructField("date", DateType(), nullable=False),
        StructField("label_type", StringType(), nullable=False),  # domain ['ward', 'neighbourhood']
        StructField("label_id", StringType(), nullable=False),
        StructField("request_count", IntegerType(), nullable=False),
        StructField("etl_run_id", StringType(), nullable=False),
        StructField("built_at", TimestampType(), nullable=False),
        StructField("source_max_ingest_date", DateType(), nullable=False),
    ]
)
