"""Month-prefix enumeration and the existence filter it is now paired with."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from spark.transforms.bronze_paths import (
    existing_month_prefixes,
    keep_existing,
    month_prefixes,
)

BUCKET = "uoip"
SID = "SRC-WPG-311"
DS = "service_requests"


class _FakePath:
    def __init__(self, uri: str, present: set[str]) -> None:
        self._uri = uri
        self._present = present

    def getFileSystem(self, _conf: object) -> _FakePath:  # noqa: N802 - Hadoop API
        return self

    def exists(self, path: _FakePath) -> bool:
        return path._uri in self._present


class _FakeSpark:
    """Just enough of the JVM gateway for ``keep_existing``.

    Mirrors the real call chain -- ``_jvm.org.apache.hadoop.fs.Path(uri)`` then
    ``.getFileSystem(conf).exists(path)`` -- built from namespaces so the
    Java-cased attribute names stay verbatim.
    """

    def __init__(self, present: set[str]) -> None:
        self._present = present
        fs_ns = SimpleNamespace(Path=lambda uri: _FakePath(uri, present))
        jvm = SimpleNamespace(
            org=SimpleNamespace(apache=SimpleNamespace(hadoop=SimpleNamespace(fs=fs_ns)))
        )
        self.sparkContext = SimpleNamespace(
            _jvm=jvm,
            _jsc=SimpleNamespace(hadoopConfiguration=lambda: object()),
        )


def test_month_prefixes_covers_every_month_the_window_touches() -> None:
    assert month_prefixes(BUCKET, SID, DS, date(2024, 2, 1), date(2024, 3, 2)) == [
        f"s3a://{BUCKET}/bronze/raw/{SID}/{DS}/2024-02/",
        f"s3a://{BUCKET}/bronze/raw/{SID}/{DS}/2024-03/",
    ]


def test_month_prefixes_does_not_consult_storage() -> None:
    """It is pure: an 18-year window enumerates without a single request."""
    prefixes = month_prefixes(BUCKET, SID, DS, date(2008, 1, 1), date(2026, 9, 1))
    assert len(prefixes) == 18 * 12 + 8


def test_keep_existing_drops_the_months_with_no_objects() -> None:
    feb, mar = month_prefixes(BUCKET, SID, DS, date(2024, 2, 1), date(2024, 3, 2))
    spark = _FakeSpark(present={feb})
    assert keep_existing(spark, [feb, mar]) == [feb]


def test_a_window_crossing_into_an_unpublished_month_still_reads() -> None:
    """The 2026-09-01 failure: the new month's folder does not exist yet.

    The window is the DAG's own -- [data_interval_start - 6d, +1d) on the 1st
    of a month -- and the whole point of the fix is that it reads the eight
    days that do exist instead of failing on the one folder that does not.
    """
    aug, sep = month_prefixes(BUCKET, SID, DS, date(2026, 8, 26), date(2026, 9, 2))
    spark = _FakeSpark(present={aug})
    assert existing_month_prefixes(
        spark, BUCKET, SID, DS, date(2026, 8, 26), date(2026, 9, 2)
    ) == [aug]
    assert sep not in {aug}


def test_a_window_with_no_bronze_at_all_raises() -> None:
    """A short month is routine; an empty window is a real gap and must raise.

    Writing zero rows over a Silver partition is worse than failing.
    """
    spark = _FakeSpark(present=set())
    with pytest.raises(ValueError, match="no Bronze month folder exists"):
        existing_month_prefixes(spark, BUCKET, SID, DS, date(2026, 9, 1), date(2026, 9, 2))
