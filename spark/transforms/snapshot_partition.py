"""Reading the ``snapshot`` Bronze layout.

``snapshot`` partitions by **collection date** rather than record date:

    bronze/raw/{sid}/{ds}/ingest_date=YYYY-MM-DD/data.ndjson.gz

Every file in that layout is named ``data.ndjson.gz``, so the collection date
lives only in the directory name and has to be recovered from the input path.
Two Silver jobs read this layout — the weather forecast and the address
snapshot — and they share this module rather than each carrying a copy of the
regex, because a snapshot day that is read wrong cannot be re-collected
(CLAUDE.md §Bronze partitioning strategies).
"""

from __future__ import annotations

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

_INGEST_DATE_PATTERN = r"ingest_date=(\d{4}-\d{2}-\d{2})/"


def parse_ingest_date(
    df: DataFrame,
    source_path_column: str = "_source_file",
    label: str = "this dataset",
) -> DataFrame:
    """Add an ``ingest_date`` column parsed from the Bronze path.

    Raises if any row's path carries no ``ingest_date=`` component, which is
    what a job pointed at a ``daily``-layout dataset would see. Failing loudly
    is the whole point: ``regexp_extract`` returns an empty string rather than
    NULL on no-match, so without this check every row gets ``ingest_date = ""``
    and every downstream operation keyed on it — freshness ordering,
    latest-snapshot selection — silently degenerates to picking arbitrarily
    while still producing a table of exactly the expected shape.
    """
    parsed = df.withColumn(
        "ingest_date",
        F.regexp_extract(F.col(source_path_column), _INGEST_DATE_PATTERN, 1),
    )
    sample = parsed.filter(F.col("ingest_date") == "").select(source_path_column).take(1)
    if sample:
        raise ValueError(
            f"Bronze path carries no 'ingest_date=YYYY-MM-DD/' component: "
            f"{sample[0][source_path_column]!r}. {label} uses the snapshot "
            f"layout; a daily-layout path means this job is pointed at the "
            f"wrong dataset."
        )
    return parsed
