"""StructType definitions for the Gold dimension tables (TBL-D1..D9, ADR 0010
D3 / design/20260809-gold-silver-schema-derivation.md §4.2).

Every dimension carries three audit columns — ``etl_run_id``, ``built_at``
(UTC), ``source_max_ingest_date`` — per ADR 0010 D7. That triple is repeated
verbatim on every StructType below rather than factored into a shared
constant: PySpark StructType concatenation reads worse than the repetition it
would save, and the audit columns are a fixed, frozen contract obligation, not
something call sites are expected to vary.

``dim_admin_label`` and ``dim_region_crosswalk`` carry ``ward`` /
``neighbourhood`` values by design (ADR 0009's one legal exit for converting a
zone-level number to a label) — nothing else in this file may.
"""

from __future__ import annotations

from pyspark.sql.types import (
    BooleanType,
    DateType,
    DoubleType,
    IntegerType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

# TBL-D2. No geometry column at all — not even a nullable one. Ward
# boundaries are not fetchable from the open data portal (dim_admin_label.yaml).
DIM_ADMIN_LABEL_GOLD_SCHEMA = StructType(
    [
        StructField("label_type", StringType(), nullable=False),  # domain ['ward', 'neighbourhood']
        # Casefolded. e.g. 'Daniel Mcintyre' and 'Daniel McIntyre' collapse to one row.
        StructField("label_id", StringType(), nullable=False),
        StructField("etl_run_id", StringType(), nullable=False),
        StructField("built_at", TimestampType(), nullable=False),
        StructField("source_max_ingest_date", DateType(), nullable=False),
    ]
)

# TBL-D6. Closed domain, exact: 15 (contracts/gold-contracts/dim_channel.yaml).
DIM_CHANNEL_GOLD_SCHEMA = StructType(
    [
        StructField("channel_raw", StringType(), nullable=False),
        # Self Service + Mobile + SMS In all map to VOF (2022 reporting-boundary
        # migration, not a behavior change).
        StructField("channel_normalized", StringType(), nullable=False),
        # False for channel_raw in {Self Service, Mobile, SMS In}: their
        # pre/post-2022 channel-structure comparison is invalid, though
        # total-volume comparison remains valid.
        StructField("is_comparable_pre_2022", BooleanType(), nullable=False),
        StructField("etl_run_id", StringType(), nullable=False),
        StructField("built_at", TimestampType(), nullable=False),
        StructField("source_max_ingest_date", DateType(), nullable=False),
    ]
)

# Added post-freeze (launch doc 20260813 B1): the 19 city-wide plow operations
# were previously an implicit entity spread across fact_event_zone_rank's
# composite key, with three downstream FKs pointing at a non-unique column.
DIM_PLOW_EVENT_GOLD_SCHEMA = StructType(
    [
        # Sourced from silver_plow_shift.snow_ban_id (one plow_event per distinct
        # ban that triggered a city-wide operation) — the only place this mapping
        # was previously implicit.
        StructField("plow_event_id", StringType(), nullable=False),
        StructField("ban_id", StringType(), nullable=False),
        # MIN(shift_start_utc) across this plow_event's 22 shifts — the
        # operation's start, for ordering plow_events chronologically.
        StructField("first_shift_start_utc", TimestampType(), nullable=False),
        # NULL for the two known-unaligned operations (2021-01-07, 2026-02-26).
        # Unique when not null — this is what makes it safe to join into
        # fact_winter_event_zone_load without fan-out.
        StructField("matched_snowfall_event_id", StringType(), nullable=True),
        # True iff matched_snowfall_event_id is not null. Explicit column so
        # 'unaligned' is queryable without an IS NULL check on a nullable FK.
        StructField("is_aligned", BooleanType(), nullable=False),
        StructField("etl_run_id", StringType(), nullable=False),
        StructField("built_at", TimestampType(), nullable=False),
        StructField("source_max_ingest_date", DateType(), nullable=False),
    ]
)

# TBL-D1. The only dimension carrying real geometry (ADR 0010 D3, D6).
DIM_PLOW_ZONE_GOLD_SCHEMA = StructType(
    [
        StructField("plow_zone", StringType(), nullable=False),  # cardinality 25
        # Union of silver_plow_zone_boundary rows sharing this plow_zone, kept as
        # a geometry *collection* (internal boundaries remain) — not ST_Union.
        # ADR 0010 D6.
        StructField("geometry_wkt", StringType(), nullable=False),
        # False for exactly 3 values: B/D, X, Downtown. Used to filter, never to
        # physically exclude these zones from fact tables (ADR 0010 §5 Q5).
        StructField("has_plow_schedule", BooleanType(), nullable=False),
        # BO-6's normalization denominator. Sourced from
        # silver_snow_clearing_address.address_count at its latest
        # snapshot_date, not directly from SRC-WPG-SNOW. Point-in-time snapshot
        # used to normalize a decade of historical events — a declared, not a
        # hidden, limitation (see address_count_snapshot_date).
        StructField("address_count", IntegerType(), nullable=False),
        # Not optional (ADR 0010 D3). The date g3p4-h83y was pulled; makes the
        # snapshot-vs-history mismatch visible as data, not a slide footnote.
        StructField("address_count_snapshot_date", DateType(), nullable=False),
        # True for exactly 8/25 zones (A, B, B/D, D, Downtown, E, R, S — the
        # ones with an OGC-invalid source polygon).
        StructField("geometry_repaired", BooleanType(), nullable=False),
        # Null unless geometry_repaired = true.
        StructField("area_delta_pct", DoubleType(), nullable=True),
        StructField("etl_run_id", StringType(), nullable=False),
        StructField("built_at", TimestampType(), nullable=False),
        StructField("source_max_ingest_date", DateType(), nullable=False),
    ]
)

# TBL-D7. Text templates only — never a model. Must never be described as AI
# (BO-8 表述纪律 §2).
DIM_RECOMMENDATION_RULES_GOLD_SCHEMA = StructType(
    [
        StructField("rule_id", StringType(), nullable=False),
        # Human-readable attribution template, e.g. 'this zone scores high
        # mainly due to schedule ordering, not snowfall volume'.
        StructField("template_text", StringType(), nullable=False),
        # True for rules used when M1's output or an input is missing — the
        # deterministic degrade path, not the primary logic.
        StructField("is_fallback", BooleanType(), nullable=False),
        StructField("etl_run_id", StringType(), nullable=False),
        StructField("built_at", TimestampType(), nullable=False),
        StructField("source_max_ingest_date", DateType(), nullable=False),
    ]
)

# TBL-D3 (ADR 0010 D4). Single direction only: zone -> label. The reverse is
# deliberately not modeled — it is the one legal exit for converting a
# zone-level number to a ward/neighbourhood number, which ADR 0009 forbids
# anywhere else.
DIM_REGION_CROSSWALK_GOLD_SCHEMA = StructType(
    [
        StructField("plow_zone", StringType(), nullable=False),
        StructField("label_type", StringType(), nullable=False),  # domain ['ward', 'neighbourhood']
        StructField("label_id", StringType(), nullable=False),
        # Share of this zone's winter service requests falling on this label.
        # sum(weight) over label_type for a fixed plow_zone = 1.
        StructField("weight", DoubleType(), nullable=False),
        # Explicit column, not computed downstream via ORDER BY weight LIMIT 1
        # (ADR 0010 D4) — any downstream recompute of this is a defect.
        StructField("is_dominant", BooleanType(), nullable=False),
        # ADR 0010 §5 O5: initial value is the trailing-5-season assumption,
        # NOT a validated constant. S6 must compare weight stability across
        # window lengths before this value is locked into an accepted-values
        # list.
        StructField("calibration_window", StringType(), nullable=False),
        StructField("etl_run_id", StringType(), nullable=False),
        StructField("built_at", TimestampType(), nullable=False),
        StructField("source_max_ingest_date", DateType(), nullable=False),
    ]
)

# TBL-D5. Seed table — business semantics live in config/, never in
# spark/transforms/ (city-agnostic guardrail §1).
DIM_SERVICE_TYPE_GOLD_SCHEMA = StructType(
    [
        # Raw 311 'type' string, verbatim.
        StructField("type", StringType(), nullable=False),
        # Null for non-winter types. A `type` value can match more than one
        # dim_winter_category keyword_pattern; this column holds exactly one
        # value, so the build job adjudicates first-match-wins in
        # dim_winter_category's row order (SNOW > FROZEN > PLOW > SANDING >
        # WINDROW > ICE_CONTROL > PLOUGH).
        StructField("winter_category", StringType(), nullable=True),
        # Parsed from 'Pr 2' / 'Priority 2' / 'P2' / '_vof' suffix variants
        # embedded in type. P1=3, P2=2, P3=1 per BO-1's weighted-count
        # definition.
        StructField("priority_weight", IntegerType(), nullable=True),  # domain [1, 2, 3]
        StructField("etl_run_id", StringType(), nullable=False),
        StructField("built_at", TimestampType(), nullable=False),
        StructField("source_max_ingest_date", DateType(), nullable=False),
    ]
)

# TBL-D4. The project-wide analysis unit primary key. 1:1 from
# silver.snowfall_events, plus the scheduling-era flag.
DIM_SNOWFALL_EVENT_GOLD_SCHEMA = StructType(
    [
        StructField("snowfall_event_id", StringType(), nullable=False),
        StructField("start_date", DateType(), nullable=False),
        StructField("end_date", DateType(), nullable=False),
        StructField("total_snowfall_cm", DoubleType(), nullable=False),
        # Median 1.0 even under the rolling-accumulation criterion — do not
        # narrate as multi-day.
        StructField("duration_days", IntegerType(), nullable=False),
        # The core number behind BO-3's subthreshold-accumulation finding
        # (21-day accumulation held 76% of the aligned median vs. 26% for
        # single-day peak).
        StructField("peak_daily_snowfall_cm", DoubleType(), nullable=False),
        # Nullable in Silver — see the coalesce rule severity_score's build
        # depends on.
        StructField("min_temperature_c", DoubleType(), nullable=True),
        # 1:1 from silver.snowfall_events.accum_flag. True iff no single day in
        # the event reached the single-day threshold on its own.
        StructField("accum_flag", BooleanType(), nullable=False),
        # Normalized composite of snowfall + low temperature. Its variance is
        # 99.4% between-event / 0.6% within-event — it sets how bad the storm
        # scores overall, does not drive intra-event zone ordering (BO-6
        # caveat 1).
        StructField("severity_score", DoubleType(), nullable=False),
        # Winter season label, e.g. '2015-2016'. Buckets into the pre/post
        # scheduling-era split.
        StructField("snow_season", StringType(), nullable=False),
        # True from 2015-12 (supply-side data start) onward — 59 of 99 events.
        # Pre-era events feed M1's long-horizon training only; they never get a
        # load score (BO-6 'effective analysis window').
        StructField("is_scheduling_era", BooleanType(), nullable=False),
        # domain ['v1-3cm-or-10d10cm']
        StructField("event_rule_version", StringType(), nullable=False),
        StructField("etl_run_id", StringType(), nullable=False),
        StructField("built_at", TimestampType(), nullable=False),
        StructField("source_max_ingest_date", DateType(), nullable=False),
    ]
)

# Added post-freeze (launch doc 20260813 A3): gives
# fact_service_request_zone_event's winter_category key component a real
# referenced domain — it previously FK'd to dim_service_type.winter_category,
# a nullable, non-unique attribute, not a legal FK target in any engine.
DIM_WINTER_CATEGORY_GOLD_SCHEMA = StructType(
    [
        # domain: SNOW, FROZEN, PLOW, SANDING, WINDROW, ICE_CONTROL, PLOUGH
        StructField("winter_category", StringType(), nullable=False),
        # SQL LIKE pattern matched against silver_service_request.type, e.g.
        # '%SNOW%'. One category, one pattern — no compound OR patterns.
        StructField("keyword_pattern", StringType(), nullable=False),
        # False only for PLOUGH (0 observed matches for the currently deployed
        # city) — kept for portability, not dropped.
        StructField("is_effective", BooleanType(), nullable=False),
        StructField("etl_run_id", StringType(), nullable=False),
        StructField("built_at", TimestampType(), nullable=False),
        StructField("source_max_ingest_date", DateType(), nullable=False),
    ]
)
