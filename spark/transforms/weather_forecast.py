"""Bronze → Silver transforms for SRC-Open-Meteo / ``weather_forecast`` (hourly).

Pipeline order (see spark/jobs/etl_weather_forecast.py for orchestration):
  1. parse_ingest_date     — recover the collection date from the Bronze path
  2. dedupe_by_freshness   — keep the most recently re-forecast value per hour
  3. normalize_timestamps  — local wall clock -> UTC, derive partition date
  4. split_by_validity     — range/null checks; valid rows vs. quarantined rejects

``ingest_date`` is load-bearing here, not audit decoration: it is the only thing
that orders the forecast revisions for the same hour, so ``dedupe_by_freshness``
is meaningless without it. See ``parse_ingest_date`` for why that makes the Bronze
path layout part of this module's contract.

Valid ranges are frozen in contracts/api-contracts/open-meteo.yaml.
"""

from __future__ import annotations

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql.types import StructType
from pyspark.sql.window import Window

from spark.transforms.timestamp_normalizer import localize_naive_to_utc, to_partition_date

VALID_RANGES = {
    "temperature_2m": (-40.0, 50.0),
    "precipitation": (0.0, None),
    "snowfall": (0.0, None),
    "windspeed_10m": (0.0, 200.0),
}

# The `snapshot` Bronze layout this dataset is written with:
#   bronze/raw/{sid}/{ds}/ingest_date=YYYY-MM-DD/data.ndjson.gz
_INGEST_DATE_PATTERN = r"ingest_date=(\d{4}-\d{2}-\d{2})/"


def parse_ingest_date(df: DataFrame, source_path_column: str = "_source_file") -> DataFrame:
    """Extract the collection date from the Bronze path.

    The forecast dataset is written with ``partition_strategy: snapshot``, so the
    collection date is the partition directory (``ingest_date=YYYY-MM-DD/``) and
    not part of a filename — every snapshot file is named ``data.ndjson.gz``.

    Raises if a row's path carries no ``ingest_date=`` component. Failing loudly
    matters more here than elsewhere: ``regexp_extract`` returns an empty string
    rather than null on no-match, and an empty ``ingest_date`` on every row leaves
    ``dedupe_by_freshness`` ordering by a constant — it would still emit exactly
    one row per hour, silently keeping an arbitrary revision instead of the
    freshest, and produce a plausible-looking Silver table that is wrong.
    """
    parsed = df.withColumn(
        "ingest_date",
        F.regexp_extract(F.col(source_path_column), _INGEST_DATE_PATTERN, 1),
    )
    sample = parsed.filter(F.col("ingest_date") == "").select(source_path_column).take(1)
    if sample:
        raise ValueError(
            f"Bronze path carries no 'ingest_date=YYYY-MM-DD/' component: "
            f"{sample[0][source_path_column]!r}. The forecast dataset uses the "
            f"snapshot layout; a daily-layout path means this job is pointed at "
            f"the wrong dataset."
        )
    return parsed


def dedupe_by_freshness(df: DataFrame) -> DataFrame:
    """Keep one row per raw ``time`` value: the one from the freshest ingest_date.

    Each collection date holds that day's view of the coming week, so the same
    hour appears under several ``ingest_date=`` partitions with different values.
    That is the forecast-revision series rather than duplication — and the later
    collection date always reflects the more recent model run, so it wins.
    """
    ranked = df.withColumn(
        "_rank",
        F.row_number().over(
            Window.partitionBy("time").orderBy(F.col("ingest_date").desc())
        ),
    )
    return ranked.filter(F.col("_rank") == 1).drop("_rank")


def normalize_timestamps(df: DataFrame, source_id: str, source_tz: str) -> DataFrame:
    """Convert raw local-time `time` to UTC `time_utc` and derive partition `date`.

    Adds `source_id` and `loaded_at` (job run time) audit columns.

    ``source_tz`` is a parameter rather than a module constant: Open-Meteo returns
    wall clock in whatever ``timezone`` the source YAML asked for, so the correct
    value is a property of the deployed city's configuration, not of this
    transform (CLAUDE.md 城市无关护栏 §1).
    """
    df = localize_naive_to_utc(df, "time", source_tz)
    df = df.withColumnRenamed("time", "time_utc")
    df = df.withColumn("date", to_partition_date(F.col("time_utc")))
    df = df.withColumn("source_id", F.lit(source_id))
    df = df.withColumn("loaded_at", F.current_timestamp())
    return df


def split_by_validity(df: DataFrame) -> tuple[DataFrame, DataFrame]:
    """Split rows into (valid, rejected) based on null timestamps and range checks.

    Rejected rows are tagged with `_reject_reason` rather than dropped, so they
    can be written to a quarantine path for audit instead of silently lost.
    """
    reasons = [F.when(F.col("time_utc").isNull(), F.lit("null_time_utc"))]
    for column, (lo, hi) in VALID_RANGES.items():
        out_of_range = F.lit(False)
        if lo is not None:
            out_of_range = out_of_range | (F.col(column) < F.lit(lo))
        if hi is not None:
            out_of_range = out_of_range | (F.col(column) > F.lit(hi))
        cond = F.col(column).isNotNull() & out_of_range
        reasons.append(F.when(cond, F.lit(f"{column}_out_of_range")))

    df = df.withColumn("_reject_reason", F.coalesce(*reasons))
    valid = df.filter(F.col("_reject_reason").isNull()).drop("_reject_reason")
    rejected = df.filter(F.col("_reject_reason").isNotNull())
    return valid, rejected


def enforce_schema(df: DataFrame, schema: StructType) -> DataFrame:
    """Align `df` to exactly `schema` before writing — fail fast on drift.

    Raises if the transform pipeline's output columns don't exactly match the
    declared Silver schema (spark/schemas/weather_schemas.py), so a code
    change that silently drops/adds/retypes a column breaks the job instead
    of writing an undocumented table shape.
    """
    expected = {f.name for f in schema.fields}
    actual = set(df.columns)
    missing = expected - actual
    unexpected = actual - expected
    if missing or unexpected:
        raise ValueError(
            f"Silver output columns don't match WEATHER_FORECAST_SILVER_SCHEMA: "
            f"missing={sorted(missing)} unexpected={sorted(unexpected)}"
        )
    return df.select([F.col(f.name).cast(f.dataType) for f in schema.fields])
