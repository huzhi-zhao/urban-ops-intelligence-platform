"""Bronze month-prefix enumeration and existence filtering.

Both windowed Bronze readers (``etl_service_request`` and
``etl_weather_archive``) hand Spark a list of *month* folders rather than one
path per day. That choice is load-bearing and documented at both call sites:
Spark calls ``exists()`` on every path before reading anything
(``DataSource.checkAndGlobPathIfNecessary``), so enumerating the ~4,900-day
history issues that many HEAD requests up front, and s3a treats 403 as
non-retryable — one transient fault anywhere in the burst aborts the job.

🔴 **What months did not fix is that a missing path still fails the read.**
Object storage has no empty folders, so a month with no Bronze object has no
prefix, and Spark's ``exists()`` check raises on it. Moving the granularity
from day to month made that rarer *and gave it a schedule*: the 1st of every
month, when the new month's folder does not exist yet — the 311 upstream
publishes about a day late (O17), so "thin" becomes "no object, no folder".
It also blocks backfill by construction: Bronze holds winters only before
2016-08, so every summer month in that era is absent.

So the driver filters the enumerated prefixes down to the ones that actually
exist and passes only those to the reader. An empty result is the one case
that still raises — a window with no Bronze at all is a real gap, not a short
month, and silently writing zero rows over a Silver partition is worse than
failing.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from pyspark.sql import SparkSession

logger = logging.getLogger(__name__)


def month_prefixes(bucket: str, source_id: str, dataset: str, start: date, end: date) -> list[str]:
    """Every Bronze month folder overlapping ``[start, end)``, existing or not.

    Pure and total — it describes the window, it does not consult storage.
    """
    prefixes = []
    month = start.replace(day=1)
    while month < end:
        prefixes.append(f"s3a://{bucket}/bronze/raw/{source_id}/{dataset}/{month:%Y-%m}/")
        # Day 28 + 4 days lands in the next month for every month length.
        month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    return prefixes


def keep_existing(spark: SparkSession, prefixes: list[str]) -> list[str]:
    """The subset of ``prefixes`` that exists in object storage.

    Uses the Hadoop ``FileSystem`` API from the driver — the same check Spark
    would run itself, but one this side can act on instead of being aborted by.
    """
    jvm = spark.sparkContext._jvm
    hadoop_conf = spark.sparkContext._jsc.hadoopConfiguration()
    path_cls = jvm.org.apache.hadoop.fs.Path
    present = []
    for prefix in prefixes:
        jpath = path_cls(prefix)
        if jpath.getFileSystem(hadoop_conf).exists(jpath):
            present.append(prefix)
    return present


def existing_month_prefixes(
    spark: SparkSession, bucket: str, source_id: str, dataset: str, start: date, end: date
) -> list[str]:
    """``month_prefixes`` minus the months with no Bronze object.

    Raises when *nothing* survives: that is a window with no Bronze at all.
    """
    enumerated = month_prefixes(bucket, source_id, dataset, start, end)
    present = keep_existing(spark, enumerated)
    missing = [p for p in enumerated if p not in present]
    if missing:
        logger.warning(
            "skipping %d Bronze month prefix(es) with no objects: %s",
            len(missing),
            ", ".join(missing),
        )
    if not present:
        raise ValueError(
            f"no Bronze month folder exists for {source_id}/{dataset} over "
            f"[{start}, {end}) - looked for: {', '.join(enumerated)}"
        )
    return present
