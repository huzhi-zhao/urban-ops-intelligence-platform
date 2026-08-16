"""Bronze -> Silver ETL for SRC-Open-Meteo / weather_archive, plus BO-3 snowfall events.

Reads the daily-split Bronze NDJSON files for a `[start, end)` window and writes:

  s3a://{bucket}/silver/weather_archive/date=YYYY-MM-DD/*.parquet          (valid rows)
  s3a://{bucket}/silver/_rejects/weather_archive/date=YYYY-MM-DD/*.parquet (rejects)
  s3a://{bucket}/silver/snowfall_event/*.parquet                          (events)

Idempotent for the daily table: re-running the same window overwrites only the
date partitions it touches (`partitionOverwriteMode=dynamic`).

The event table is different and deliberately so. A snowfall event can span the
window boundary, so it cannot be computed from a slice — segmenting
[Jan 1, Feb 1) and [Feb 1, Mar 1) separately would cut any storm running across
midnight on Jan 31 into two events. `--emit-events` therefore reads the *whole*
Silver archive table and replaces the event table outright. Run it after a
backfill, not on every incremental window.

Usage:
    # incremental window, daily table only
    spark-submit spark/jobs/etl_weather_archive.py \
        --bucket uoip --start 2026-07-01 --end 2026-08-01

    # full history, then rebuild the event table from the whole series
    spark-submit spark/jobs/etl_weather_archive.py \
        --bucket uoip --start 2008-01-01 --end 2026-08-02 \
        --emit-events --snowfall-threshold-cm 2.0
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from spark.schemas.weather_schemas import (
    SNOWFALL_EVENT_SCHEMA,
    WEATHER_ARCHIVE_RAW_SCHEMA,
    WEATHER_ARCHIVE_SILVER_SCHEMA,
)
from spark.transforms.weather_archive import (
    enforce_schema,
    normalize_archive_dates,
    segment_snowfall_events,
    split_by_validity,
)

logger = logging.getLogger(__name__)

SOURCE_ID = "SRC-Open-Meteo"
DATASET = "weather_archive"

# BO-3 rule, standardised 2026-08-09 (metric-feasibility-audit.md task 1):
# single day >= 3cm, OR trailing 10-day rolling total >= 10cm, tolerating one
# below-threshold gap day inside a run. This reproduces N=99 (59 in the
# scheduling era) — see scripts/analysis/snowfall_events.py, the probe these
# defaults are pinned to. A future threshold change gets a new
# EVENT_RULE_VERSION value; it never overwrites the meaning of an existing one.
EVENT_RULE_VERSION = "v1-3cm-or-10d10cm"
DEFAULT_SNOWFALL_THRESHOLD_CM = 3.0
DEFAULT_SNOWFALL_GAP_DAYS = 1
DEFAULT_ACCUM_WINDOW_DAYS = 10
DEFAULT_ACCUM_THRESHOLD_CM = 10.0

# One row per calendar day, so a healthy window has as many rows as it has days.
# Anything below this fraction means whole days are missing, not that the
# weather was quiet.
MIN_EXPECTED_ROW_FRACTION = 0.9


def _bronze_month_prefixes(bucket: str, start: date, end: date) -> list[str]:
    """The Bronze month folders overlapping `[start, end)`.

    Deliberately month folders, not one path per day. Spark calls `exists()`
    on every path handed to the reader before it reads anything
    (`DataSource.checkAndGlobPathIfNecessary`), so enumerating days made an
    18-year backfill issue ~6,800 object-storage HEAD requests up front. Two
    consequences, both bad:

    * s3a classifies 403 as non-retryable, so a *single* transient failure
      anywhere in that burst aborts the whole job — and 6,800 attempts turn a
      rare fault into a likely one. That is how the 2026-08-16 Cloudflare
      HEAD-rewrite incident surfaced.
    * A day genuinely absent from Bronze made the entire window unreadable
      rather than merely short, so one gap anywhere in the history blocked
      every backfill spanning it.

    Reading whole months costs ~220 paths for the same window and drops both
    failure modes. The window is trimmed afterwards in `run()` — a month
    folder holds days on either side of `start`/`end`.
    """
    prefixes = []
    month = start.replace(day=1)
    while month < end:
        prefixes.append(
            f"s3a://{bucket}/bronze/raw/{SOURCE_ID}/{DATASET}/{month:%Y-%m}/"
        )
        # Day 28 + 4 days lands in the next month for every month length.
        month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    return prefixes


def run(
    spark: SparkSession,
    bucket: str,
    start: date,
    end: date,
    *,
    emit_events: bool = False,
    snowfall_threshold_cm: float = DEFAULT_SNOWFALL_THRESHOLD_CM,
    snowfall_gap_days: int = DEFAULT_SNOWFALL_GAP_DAYS,
    accum_window_days: int | None = DEFAULT_ACCUM_WINDOW_DAYS,
    accum_threshold_cm: float | None = DEFAULT_ACCUM_THRESHOLD_CM,
    event_rule_version: str = EVENT_RULE_VERSION,
) -> None:
    silver_path = f"s3a://{bucket}/silver/{DATASET}"
    rejects_path = f"s3a://{bucket}/silver/_rejects/{DATASET}"
    events_path = f"s3a://{bucket}/silver/snowfall_event"

    # pathGlobFilter is load-bearing, not an optimisation: each month folder
    # holds `manifest_YYYY-MM-DD.json` beside the data shards, and reading the
    # folder whole would parse those manifests as if they were weather records.
    # Against a declared schema they yield all-null rows, so they would land in
    # _rejects silently rather than raising.
    raw = (
        spark.read.schema(WEATHER_ARCHIVE_RAW_SCHEMA)
        .option("pathGlobFilter", "data_*.ndjson.gz")
        .json(_bronze_month_prefixes(bucket, start, end))
    )

    normalized = normalize_archive_dates(raw, source_id=SOURCE_ID)
    # Month folders overshoot the requested window on both ends. Null dates are
    # kept so unparseable rows still reach _rejects instead of being dropped —
    # split_by_validity, not this filter, is what decides validity.
    normalized = normalized.filter(
        F.col("weather_date").isNull()
        | ((F.col("weather_date") >= F.lit(start)) & (F.col("weather_date") < F.lit(end)))
    )
    valid, rejected = split_by_validity(normalized)
    valid = enforce_schema(valid, WEATHER_ARCHIVE_SILVER_SCHEMA)

    (
        valid.write.partitionBy("date")
        .option("partitionOverwriteMode", "dynamic")
        .mode("overwrite")
        .parquet(silver_path)
    )
    if rejected.take(1):
        (
            rejected.write.partitionBy("date")
            .option("partitionOverwriteMode", "dynamic")
            .mode("overwrite")
            .parquet(rejects_path)
        )

    window_days = (end - start).days
    row_count = valid.count()
    rejected_count = rejected.count()
    logger.info(
        "%s/%s: window=[%s, %s) days=%d valid_rows=%d rejected_rows=%d",
        SOURCE_ID, DATASET, start, end, window_days, row_count, rejected_count,
    )

    min_expected = int(window_days * MIN_EXPECTED_ROW_FRACTION)
    if row_count < min_expected:
        raise RuntimeError(
            f"{SOURCE_ID}/{DATASET}: only {row_count} valid Silver rows for "
            f"window=[{start}, {end}) covering {window_days} days (expected "
            f">= {min_expected}, one row per day) — days are missing from Bronze, "
            f"or the upstream schema changed. Escalate per CLAUDE.md."
        )

    if not emit_events:
        return

    # Read back the full table rather than reusing `valid`: an event may start
    # before this window and end inside it, and segmenting a slice would report
    # a truncated event as a whole one.
    full_series = spark.read.parquet(silver_path)
    events = segment_snowfall_events(
        full_series,
        source_id=SOURCE_ID,
        threshold_cm=snowfall_threshold_cm,
        event_rule_version=event_rule_version,
        gap_days=snowfall_gap_days,
        accum_window_days=accum_window_days,
        accum_threshold_cm=accum_threshold_cm,
    )
    events = enforce_schema(events, SNOWFALL_EVENT_SCHEMA)
    events.write.mode("overwrite").parquet(events_path)

    event_count = events.count()
    logger.info(
        "%s: %d snowfall events (%s) at threshold=%.1fcm gap_days=%d "
        "accum=%s/%s over the full series",
        SOURCE_ID, event_count, event_rule_version, snowfall_threshold_cm,
        snowfall_gap_days, accum_window_days, accum_threshold_cm,
    )
    if event_count == 0:
        raise RuntimeError(
            f"{SOURCE_ID}: snowfall segmentation produced 0 events at "
            f"threshold={snowfall_threshold_cm}cm. The archive covers winters, so "
            f"zero means the threshold is wrong or snowfall_sum_cm did not load."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--start", required=True, type=date.fromisoformat, help="Inclusive")
    parser.add_argument("--end", required=True, type=date.fromisoformat, help="Exclusive")
    parser.add_argument(
        "--emit-events", action="store_true",
        help="Rebuild the snowfall event table from the whole Silver series.",
    )
    parser.add_argument(
        "--snowfall-threshold-cm", type=float, default=DEFAULT_SNOWFALL_THRESHOLD_CM,
        help=(
            "Daily snowfall at or above which a day counts toward an event. "
            "Policy rather than physics — moves to config/semantics/ in batch 5. "
            "Default matches the frozen BO-3 rule; pass a different value only "
            "under a new --event-rule-version."
        ),
    )
    parser.add_argument(
        "--snowfall-gap-days", type=int, default=DEFAULT_SNOWFALL_GAP_DAYS,
        help="Below-threshold days tolerated inside one event (0 = strictly consecutive).",
    )
    parser.add_argument(
        "--accum-window-days", type=int, default=DEFAULT_ACCUM_WINDOW_DAYS,
        help=(
            "Width of the trailing rolling-accumulation window, in days. Pass "
            "together with --accum-threshold-cm, or pass neither to disable "
            "the rolling criterion. Default matches the frozen BO-3 rule."
        ),
    )
    parser.add_argument(
        "--accum-threshold-cm", type=float, default=DEFAULT_ACCUM_THRESHOLD_CM,
        help="Rolling-window total at or above which a day counts (see --accum-window-days).",
    )
    parser.add_argument(
        "--no-accum", action="store_true",
        help="Disable the rolling-accumulation criterion (single-day threshold only).",
    )
    parser.add_argument(
        "--event-rule-version", default=EVENT_RULE_VERSION,
        help="Semantic version stamped on every event row (ADR 0010 §5 O3).",
    )
    args = parser.parse_args()
    if args.start >= args.end:
        parser.error("--start must be before --end")

    accum_window_days = None if args.no_accum else args.accum_window_days
    accum_threshold_cm = None if args.no_accum else args.accum_threshold_cm

    spark = (
        SparkSession.builder
        .appName(f"etl_weather_archive_{args.start}_{args.end}")
        .getOrCreate()
    )
    try:
        run(
            spark,
            bucket=args.bucket,
            start=args.start,
            end=args.end,
            emit_events=args.emit_events,
            snowfall_threshold_cm=args.snowfall_threshold_cm,
            snowfall_gap_days=args.snowfall_gap_days,
            accum_window_days=accum_window_days,
            accum_threshold_cm=accum_threshold_cm,
            event_rule_version=args.event_rule_version,
        )
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
