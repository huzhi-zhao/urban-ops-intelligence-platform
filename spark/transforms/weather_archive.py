"""Bronze → Silver transforms for the daily weather archive, plus BO-3 event segmentation.

Pipeline order (see spark/jobs/etl_weather_archive.py for orchestration):
  1. normalize_archive_dates  — parse the bare YYYY-MM-DD `time` into a date
  2. split_by_validity        — range/null checks; valid rows vs. quarantined rejects
  3. segment_snowfall_events  — collapse runs of snowy days into events (BO-3)

Separate from ``spark/transforms/weather.py`` because the two weather datasets
have genuinely different shapes, not just different columns: the forecast is
hourly, arrives as overlapping revisions and needs freshness dedup; the archive
is daily, authoritative and needs none of that.

No threshold or keyword is hardcoded here. ``segment_snowfall_events`` takes its
threshold as an argument: "how much snow counts as an event" is a policy that
changes per city, which by the CLAUDE.md guardrail §1 test makes it
configuration rather than code. It lands in ``config/semantics/`` in batch 5 of
docs/dev/design/20260802-city-instance-switchover.md; until then the caller
passes it explicitly.
"""

from __future__ import annotations

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql.window import Window

# Physical plausibility bounds, not business policy — a value outside these is a
# broken record rather than an unusual day. Winnipeg's record daily snowfall is
# around 35 cm and its record low around -45 °C, so these leave ample headroom.
VALID_RANGES: dict[str, tuple[float | None, float | None]] = {
    "snowfall_sum_cm": (0.0, 200.0),
    "temperature_2m_min_c": (-60.0, 50.0),
    "temperature_2m_max_c": (-60.0, 50.0),
}


def normalize_archive_dates(df: DataFrame, source_id: str) -> DataFrame:
    """Parse the raw ``time`` string into a date and derive the partition column.

    There is no timezone conversion here, unlike the hourly path. A daily
    aggregate has no clock time to convert, and the API is queried in the city's
    own timezone, so ``time`` is already the local calendar day. Running it
    through a UTC conversion would shift roughly a third of the year's rows by
    one day for no gain.
    """
    parsed = df.withColumn("weather_date", F.to_date(F.col("time"), "yyyy-MM-dd"))
    return (
        parsed
        .withColumn("date", F.date_format(F.col("weather_date"), "yyyy-MM-dd"))
        .withColumnRenamed("snowfall_sum", "snowfall_sum_cm")
        .withColumnRenamed("temperature_2m_min", "temperature_2m_min_c")
        .withColumnRenamed("temperature_2m_max", "temperature_2m_max_c")
        .withColumn("source_id", F.lit(source_id))
        .withColumn("loaded_at", F.current_timestamp())
        .drop("time")
    )


def split_by_validity(df: DataFrame) -> tuple[DataFrame, DataFrame]:
    """Split into (valid, rejected).

    A row is rejected when its date failed to parse, or when a measurement is
    outside its physical range. A *null* measurement is not a rejection: the
    archive genuinely has gaps, and dropping a whole day because one column is
    missing would silently shorten the snowfall series.
    """
    conditions = [F.col("weather_date").isNotNull()]
    for column, (low, high) in VALID_RANGES.items():
        if column not in df.columns:
            continue
        in_range = F.lit(True)
        if low is not None:
            in_range = in_range & (F.col(column) >= F.lit(low))
        if high is not None:
            in_range = in_range & (F.col(column) <= F.lit(high))
        conditions.append(F.col(column).isNull() | in_range)

    is_valid = conditions[0]
    for cond in conditions[1:]:
        is_valid = is_valid & cond

    return df.filter(is_valid), df.filter(~is_valid)


def segment_snowfall_events(
    df: DataFrame,
    *,
    source_id: str,
    threshold_cm: float,
    event_rule_version: str,
    gap_days: int = 0,
    accum_window_days: int | None = None,
    accum_threshold_cm: float | None = None,
) -> DataFrame:
    """Collapse runs of snowy days into one row per snowfall event (BO-3).

    A day qualifies when ``snowfall_sum_cm >= threshold_cm`` (single-day
    criterion), or — if ``accum_window_days``/``accum_threshold_cm`` are given —
    when its trailing ``accum_window_days``-day rolling total reaches
    ``accum_threshold_cm`` (rolling-accumulation criterion). Qualifying days
    that are close enough together form one event.

    The rolling criterion exists because a single-day threshold structurally
    cannot see a storm that spreads the same total snowfall across several
    days with no one day crossing the line — ``scripts.analysis.
    plow_without_snowfall`` found four of nineteen plow operations invisible
    to the single-day criterion for exactly this reason (2026-08-09). It is a
    second way to qualify, layered onto the first, not a replacement for it —
    mirrors ``scripts/analysis/snowfall_events.py::rolling_accumulation_hits``
    so the pipeline reproduces the same N the probe measured.

    Args:
        df: Silver archive rows (``weather_date``, ``snowfall_sum_cm``,
            ``temperature_2m_min_c``).
        source_id: Stamped onto every event row for lineage.
        threshold_cm: Daily snowfall at or above which a day counts. Policy, not
            physics — passed in rather than defined here (see module docstring).
        event_rule_version: Semantic version string for the rule combination in
            effect (ADR 0010 §5 O3) — e.g. ``"v1-3cm-or-10d10cm"``. A future
            threshold change gets a new version value; it never overwrites the
            meaning of an existing one. Stamped onto every event row.
        gap_days: How many consecutive below-threshold days may sit inside one
            event without splitting it. ``0`` means strictly consecutive days.
            A storm that pauses for a day and resumes is one operational event,
            not two, so this exists — but the right value is a policy call and
            the default stays at the conservative, literal reading.
        accum_window_days: Width of the trailing rolling window, in days. Must
            be given together with ``accum_threshold_cm`` — one without the
            other has no defined meaning.
        accum_threshold_cm: Rolling ``accum_window_days``-day total at or above
            which a day counts, even with no single qualifying day inside the
            window.

    Returns:
        One row per event, matching ``SNOWFALL_EVENT_SCHEMA`` minus column
        ordering (the job's ``enforce_schema`` fixes that).
    """
    if threshold_cm < 0:
        raise ValueError(f"threshold_cm must be non-negative, got {threshold_cm}")
    if gap_days < 0:
        raise ValueError(f"gap_days must be non-negative, got {gap_days}")
    if (accum_window_days is None) != (accum_threshold_cm is None):
        raise ValueError(
            "accum_window_days and accum_threshold_cm must be given together"
        )

    if accum_window_days is not None and accum_threshold_cm is not None:
        # Range-based (not row-based) window over the day number, so a missing
        # calendar day contributes 0 rather than silently shrinking the window
        # to fewer than accum_window_days days of coverage. Window functions
        # cannot appear directly in a filter predicate, hence the intermediate
        # column.
        day_num = F.datediff(F.col("weather_date"), F.lit("1970-01-01"))
        rolling = Window.orderBy(day_num).rangeBetween(-(accum_window_days - 1), 0)
        df = df.withColumn("_rolling_total_cm", F.sum("snowfall_sum_cm").over(rolling))
        is_hit = (F.col("snowfall_sum_cm") >= F.lit(threshold_cm)) | (
            F.col("_rolling_total_cm") >= F.lit(accum_threshold_cm)
        )
        snowy = df.filter(is_hit).drop("_rolling_total_cm")
    else:
        snowy = df.filter(F.col("snowfall_sum_cm") >= F.lit(threshold_cm))

    # Walk the qualifying days in date order and open a new event whenever the
    # gap to the previous one is too large to bridge. `days_since_previous` is
    # null on the very first row, which `coalesce` turns into a break so that
    # row starts event 1 rather than being swallowed.
    ordered = Window.orderBy("weather_date")
    flagged = (
        snowy
        .withColumn(
            "_days_since_previous",
            F.datediff(F.col("weather_date"), F.lag("weather_date").over(ordered)),
        )
        .withColumn(
            "_is_event_start",
            (F.coalesce(F.col("_days_since_previous"), F.lit(999)) > F.lit(gap_days + 1))
            .cast("int"),
        )
        .withColumn(
            "_event_seq",
            F.sum("_is_event_start").over(ordered.rowsBetween(Window.unboundedPreceding, 0)),
        )
    )

    return (
        flagged.groupBy("_event_seq")
        .agg(
            F.min("weather_date").alias("start_date"),
            F.max("weather_date").alias("end_date"),
            F.count(F.lit(1)).cast("int").alias("_qualifying_days"),
            F.sum("snowfall_sum_cm").alias("total_snowfall_cm"),
            F.max("snowfall_sum_cm").alias("peak_daily_snowfall_cm"),
            F.min("temperature_2m_min_c").alias("min_temperature_c"),
        )
        # Calendar span, not the count of qualifying days: with gap_days > 0 an
        # event can contain a below-threshold day, and the duration an operations
        # team cares about is how long the event ran, not how many days snowed.
        .withColumn(
            "duration_days",
            (F.datediff(F.col("end_date"), F.col("start_date")) + F.lit(1)).cast("int"),
        )
        .withColumn(
            "event_id",
            F.concat_ws("-", F.lit("SNOW"), F.date_format(F.col("start_date"), "yyyyMMdd")),
        )
        .withColumn("source_id", F.lit(source_id))
        .withColumn("event_rule_version", F.lit(event_rule_version))
        .withColumn("loaded_at", F.current_timestamp())
        .drop("_event_seq", "_qualifying_days")
    )


def enforce_schema(df: DataFrame, schema) -> DataFrame:
    """Project to the target schema's columns, in order, and cast to its types.

    Raises on a missing or unexpected column so a schema drift fails the job
    rather than producing a silently narrower table.
    """
    expected = [f.name for f in schema.fields]
    actual = set(df.columns)

    missing = [c for c in expected if c not in actual]
    if missing:
        raise ValueError(f"DataFrame is missing expected column(s): {missing}")

    unexpected = [c for c in df.columns if c not in expected]
    if unexpected:
        raise ValueError(f"DataFrame carries unexpected column(s): {unexpected}")

    return df.select([
        F.col(f.name).cast(f.dataType).alias(f.name) for f in schema.fields
    ])
