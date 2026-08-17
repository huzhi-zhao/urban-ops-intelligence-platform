"""Bronze -> Silver transforms shared by the small static reference tables.

Role-generic: these helpers know about required fields and target schemas,
nothing about which dataset supplies them. The city-specific field names and
row-count expectations live in the calling job (CLAUDE.md §城市无关护栏),
the same split spark/transforms/geography_boundary.py already uses.

A "reference table" here means a source that is pulled whole on every run and
overwritten whole — no date window, no incremental partition. It is the
``static`` partition strategy's Silver counterpart.
"""

from __future__ import annotations

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql.types import StructType


def split_by_validity(
    df: DataFrame, required_fields: tuple[str, ...]
) -> tuple[DataFrame, DataFrame]:
    """Split into ``(valid, rejected)`` on any NULL in a required field.

    Rejected rows carry ``_reject_reason`` naming the first field that failed,
    so a rejects file can be triaged without re-deriving why each row is
    there. Rows are rejected rather than dropped: a reference table that
    quietly shrinks is the failure mode these tables are least able to
    survive, since every row count downstream is asserted against an exact
    expectation.
    """
    reject_reason = F.lit(None).cast("string")
    for field in required_fields:
        reject_reason = F.when(F.col(field).isNull(), F.lit(f"missing_{field}")).otherwise(
            reject_reason
        )

    df = df.withColumn("_reject_reason", reject_reason)
    valid = df.filter(F.col("_reject_reason").isNull()).drop("_reject_reason")
    rejected = df.filter(F.col("_reject_reason").isNotNull())
    return valid, rejected


def enforce_schema(df: DataFrame, schema: StructType) -> DataFrame:
    """Project df to exactly the columns declared in the target StructType.

    Extra columns from the raw pipeline are dropped by the select; missing
    ones raise, because a missing column means a transform step was skipped.
    This is the failure AGENTS.md §"Data contract obligations" step 4 warns
    about — declaring a column in the schema, the DDL and the contract does
    not make anything emit it.
    """
    expected = {f.name for f in schema.fields}
    missing = expected - set(df.columns)
    if missing:
        raise ValueError(
            f"Silver output columns don't match target schema: missing={sorted(missing)}"
        )
    return df.select([F.col(f.name).cast(f.dataType) for f in schema.fields])


def assert_unique(df: DataFrame, key_fields: tuple[str, ...], label: str) -> None:
    """Raise if ``key_fields`` is not unique.

    Deliberately an assertion, not a ``dropDuplicates``: a broken primary key
    is a signal that the upstream changed, and de-duplicating would convert
    that signal into a silently shorter table (design §4, the rejected
    options). Callers escalate per CLAUDE.md rather than repairing in place.
    """
    total = df.count()
    distinct = df.select(*key_fields).distinct().count()
    if total != distinct:
        raise RuntimeError(
            f"{label}: primary key {list(key_fields)} is not unique — "
            f"{total} rows, {distinct} distinct. This is an upstream change, "
            f"not something to de-duplicate away. Escalate per CLAUDE.md."
        )


def assert_row_count(df: DataFrame, expected: int, label: str) -> int:
    """Raise unless the table has exactly ``expected`` rows; return the count.

    Exact rather than a lower bound. These tables are whole-table pulls of a
    known size, so a row count that moves in *either* direction is news: fewer
    rows means a truncated fetch, more means the upstream appended history we
    have not reviewed. Both need a human before the number reaches Gold, where
    it becomes a hard gate on 418 / 49 / 25.
    """
    row_count = df.count()
    if row_count != expected:
        raise RuntimeError(
            f"{label}: got {row_count} Silver rows, expected exactly {expected}. "
            f"A whole-table pull that changed size means either a truncated "
            f"fetch or new upstream history. Escalate per CLAUDE.md."
        )
    return row_count
