"""Unit tests for spark.transforms.reference_table (no object storage/cluster needed).

Uses a local in-process SparkSession (master=local[1]). Fixtures are
role-generic rows, not any one city's reference table.
"""

from __future__ import annotations

import pytest

pyspark = pytest.importorskip("pyspark")

from pyspark.sql import Row, SparkSession  # noqa: E402
from pyspark.sql.types import (  # noqa: E402
    IntegerType,
    StringType,
    StructField,
    StructType,
)

from spark.transforms.reference_table import (  # noqa: E402
    assert_row_count,
    assert_unique,
    enforce_schema,
    split_by_validity,
)


@pytest.fixture(scope="module")
def spark():
    session = (
        SparkSession.builder.master("local[1]")
        .appName("test_reference_table_transforms")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )
    yield session
    session.stop()


# ---------------------------------------------------------------------------
# split_by_validity
# ---------------------------------------------------------------------------


def test_split_by_validity_keeps_complete_rows(spark):
    df = spark.createDataFrame([Row(id="a", label="x"), Row(id="b", label="y")])

    valid, rejected = split_by_validity(df, required_fields=("id", "label"))

    assert valid.count() == 2
    assert rejected.count() == 0


def test_split_by_validity_rejects_rather_than_drops(spark):
    df = spark.createDataFrame(
        [Row(id="a", label="x"), Row(id=None, label="y"), Row(id="c", label=None)]
    )

    valid, rejected = split_by_validity(df, required_fields=("id", "label"))

    assert valid.count() == 1
    # The rejected rows are retained with a reason, so a rejects file can be
    # triaged without re-deriving why each row is there.
    reasons = {row["_reject_reason"] for row in rejected.collect()}
    assert reasons == {"missing_id", "missing_label"}


def test_split_by_validity_leaves_no_scratch_column_on_valid(spark):
    df = spark.createDataFrame([Row(id="a", label="x")])

    valid, _ = split_by_validity(df, required_fields=("id",))

    assert "_reject_reason" not in valid.columns


# ---------------------------------------------------------------------------
# enforce_schema
# ---------------------------------------------------------------------------


_TARGET = StructType(
    [
        StructField("id", StringType(), nullable=False),
        StructField("amount", IntegerType(), nullable=False),
    ]
)


def test_enforce_schema_projects_and_casts(spark):
    df = spark.createDataFrame([Row(id="a", amount="7", extra="dropped")])

    result = enforce_schema(df, _TARGET)

    assert result.columns == ["id", "amount"]
    assert result.collect()[0]["amount"] == 7


def test_enforce_schema_raises_on_missing_column(spark):
    """A missing column means a transform step was skipped.

    This is the AGENTS.md step-4 failure: declaring a column in the schema,
    the DDL and the contract does not make anything emit it.
    """
    df = spark.createDataFrame([Row(id="a")])

    with pytest.raises(ValueError, match="amount"):
        enforce_schema(df, _TARGET)


# ---------------------------------------------------------------------------
# assert_unique / assert_row_count
# ---------------------------------------------------------------------------


def test_assert_unique_passes_on_unique_key(spark):
    df = spark.createDataFrame([Row(id="a", n=1), Row(id="b", n=2)])

    assert_unique(df, ("id",), "test")  # does not raise


def test_assert_unique_raises_instead_of_deduplicating(spark):
    """A broken PK is an upstream signal, not something to de-duplicate away."""
    df = spark.createDataFrame([Row(id="a", n=1), Row(id="a", n=2)])

    with pytest.raises(RuntimeError, match="not unique"):
        assert_unique(df, ("id",), "test")


def test_assert_unique_supports_composite_keys(spark):
    df = spark.createDataFrame([Row(a="x", b=1), Row(a="x", b=2)])

    assert_unique(df, ("a", "b"), "test")

    with pytest.raises(RuntimeError):
        assert_unique(df, ("a",), "test")


def test_assert_row_count_returns_the_count_when_exact(spark):
    df = spark.createDataFrame([Row(id="a"), Row(id="b")])

    assert assert_row_count(df, 2, "test") == 2


@pytest.mark.parametrize("expected", [1, 3])
def test_assert_row_count_raises_in_both_directions(spark, expected):
    """Fewer rows means a truncated fetch; more means unreviewed new history."""
    df = spark.createDataFrame([Row(id="a"), Row(id="b")])

    with pytest.raises(RuntimeError, match="expected exactly"):
        assert_row_count(df, expected, "test")
