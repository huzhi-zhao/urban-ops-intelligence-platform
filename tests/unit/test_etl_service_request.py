"""Unit tests for spark/jobs/etl_service_request.py.

Offline by construction: a local SparkSession over a temp directory, with the
path helpers monkeypatched away from s3a://. What is *not* tested here is
deliberate — real MinIO, the real 82 polygons and the row magnitudes are the
production gates in the launch document, not something a fixture can stand in
for.

What is tested is everything that would be wrong in a way no gate would catch
later: the local-date partition key (a UTC key is off by a few hours, which
looks plausible in a spot check), the three-value split, and the PK assertion
firing rather than silently de-duplicating.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

pyspark = pytest.importorskip("pyspark")
shapely = pytest.importorskip("shapely")

from pyspark.sql import SparkSession  # noqa: E402

from spark.jobs import etl_service_request as job  # noqa: E402
from spark.transforms.zone_assignment import (  # noqa: E402
    MATCH_STATUS_MATCHED,
    MATCH_STATUS_NO_GEO,
    MATCH_STATUS_UNMATCHED,
)

BUCKET = "uoip"
BASE = f"s3a://{BUCKET}/bronze/raw/SRC-WPG-311/service_requests"

# A unit square around (0,0) and a second, disjoint one — two zones, so a
# "matched" result is a real containment test and not "the only polygon there".
ZONE_A_WKT = "POLYGON ((0 0, 0 1, 1 1, 1 0, 0 0))"
ZONE_B_WKT = "POLYGON ((5 5, 5 6, 6 6, 6 5, 5 5))"


@pytest.fixture(scope="module")
def spark():
    session = (
        SparkSession.builder.master("local[1]")
        .appName("test_etl_service_request")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.session.timeZone", "UTC")
        .getOrCreate()
    )
    yield session
    session.stop()


def _record(case_id, interaction_id, open_date, *, point=None, **overrides):
    record = {
        "case_id": case_id,
        "interaction_id": interaction_id,
        # Socrata floating timestamp: local wall clock, no offset, no 'Z'.
        "open_date": open_date,
        "closed_date": None,
        "case_status": "Closed",
        "subject": "Public Works",
        "reason": "Streets Maintenance",
        "type": "Snow Clearing Request",
        "channel_type": "Phone",
        "neighbourhood": "Mcmillan",
        "ward": "Fort Rouge",
    }
    record.update(overrides)
    if point is not None:
        record["geometry"] = {"type": "Point", "coordinates": list(point)}
    return record


@pytest.fixture
def bronze(tmp_path, monkeypatch):
    """Redirect the job's paths into tmp_path and return a writer for Bronze.

    The writer takes {"YYYY-MM": [records]} so a test can put rows in the
    neighbouring month and check that the window filter — not the folder
    listing — is what trims them.
    """
    monkeypatch.setattr(
        job, "bronze_month_prefixes",
        lambda bucket, start, end: [
            str(tmp_path / "bronze" / f"{m:%Y-%m}")
            for m in _months(start, end)
            if (tmp_path / "bronze" / f"{m:%Y-%m}").exists()
        ],
    )
    monkeypatch.setattr(job, "_silver_path", lambda bucket: str(tmp_path / "silver"))
    monkeypatch.setattr(
        job, "_rejects_path",
        lambda bucket, start, end: str(tmp_path / "rejects" / f"window={start}_{end}"),
    )
    monkeypatch.setattr(job, "_boundary_path", lambda bucket: str(tmp_path / "boundary"))
    return tmp_path


def _months(start, end):
    from datetime import timedelta

    month, out = start.replace(day=1), []
    while month < end:
        out.append(month)
        month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    return out


def _write_bronze(root, by_month):
    for month, records in by_month.items():
        folder = root / "bronze" / month
        folder.mkdir(parents=True, exist_ok=True)
        # The name matters: run() reads with pathGlobFilter="data_*.ndjson.gz"
        # so that the manifests sitting beside the shards are not parsed as
        # records. A fixture named otherwise would test nothing.
        (folder / f"data_{month}-01.ndjson.gz").write_bytes(
            _gzip("\n".join(json.dumps(r) for r in records))
        )
        (folder / f"manifest_{month}-01.json").write_text('{"record_count": 1}')


def _gzip(text: str) -> bytes:
    import gzip

    return gzip.compress(text.encode())


def _write_boundary(spark, root, zones=((("A"), ZONE_A_WKT), ("B", ZONE_B_WKT))):
    spark.createDataFrame(
        list(zones), "plow_zone string, geometry_wkt string"
    ).write.mode("overwrite").parquet(str(root / "boundary"))


def _silver(spark, root):
    return spark.read.parquet(str(root / "silver"))


def _utc_string(df, column):
    """Render a timestamp column in UTC, inside Spark.

    ``collect()`` hands back a Python datetime in the driver's local timezone,
    which would make these assertions depend on where the test runs.
    """
    from pyspark.sql import functions as F

    return df.select(
        F.date_format(F.col(column), "yyyy-MM-dd'T'HH:mm").alias("s")
    ).collect()[0]["s"]


# ── The partition key ─────────────────────────────────────────────────────────


def test_partition_key_is_the_local_calendar_date_not_the_utc_date(spark, bronze):
    """23:30 local on the 15th is 05:30 UTC on the 16th. The partition must
    say the 15th: a UTC key moves every late-evening request into the next day
    and tilts every daily aggregate by a few hours' worth of volume.
    """
    _write_bronze(bronze, {"2024-01": [
        _record("c1", "i1", "2024-01-15T23:30:00.000", point=(0.5, 0.5)),
    ]})
    _write_boundary(spark, bronze)

    job.run(spark, BUCKET, date(2024, 1, 1), date(2024, 2, 1))

    silver = _silver(spark, bronze)
    assert silver.collect()[0]["open_date_local"] == date(2024, 1, 15)
    # ...and the timestamp itself really was shifted, i.e. the local date is
    # not just the string parsed as UTC. Formatted inside Spark on purpose:
    # collecting a timestamp renders it in the *driver's* timezone, so a
    # Python-side comparison would pass or fail depending on the developer's
    # laptop rather than on the job.
    assert _utc_string(silver, "open_ts_utc") == "2024-01-16T05:30"


@pytest.mark.parametrize(
    ("local_ts", "expected_utc_hour"),
    [
        # CST (UTC-6) in January, CDT (UTC-5) in July. A fixed offset would get
        # one of these wrong by an hour, which is a whole partition's worth for
        # anything opened near local midnight.
        ("2024-01-15T12:00:00.000", 18),
        ("2024-07-15T12:00:00.000", 17),
    ],
)
def test_dst_offset_follows_the_calendar(spark, bronze, local_ts, expected_utc_hour):
    month = local_ts[:7]
    _write_bronze(bronze, {month: [_record("c1", "i1", local_ts, point=(0.5, 0.5))]})
    _write_boundary(spark, bronze)
    start = date.fromisoformat(f"{month}-01")
    end = date(start.year, start.month + 1, 1)

    job.run(spark, BUCKET, start, end)

    silver = _silver(spark, bronze)
    assert _utc_string(silver, "open_ts_utc").endswith(f"T{expected_utc_hour:02d}:00")
    assert silver.collect()[0]["open_date_local"] == date.fromisoformat(local_ts[:10])


# ── Three-value geo attribution ───────────────────────────────────────────────


def test_three_value_status_and_zone_only_when_matched(spark, bronze):
    _write_bronze(bronze, {"2024-01": [
        _record("c1", "i1", "2024-01-05T09:00:00.000", point=(0.5, 0.5)),   # inside A
        _record("c2", "i2", "2024-01-06T09:00:00.000", point=(90.0, 45.0)),  # outside all
        _record("c3", "i3", "2024-01-07T09:00:00.000"),                      # no geometry
    ]})
    _write_boundary(spark, bronze)

    job.run(spark, BUCKET, date(2024, 1, 1), date(2024, 2, 1))

    rows = {r["case_id"]: r for r in _silver(spark, bronze).collect()}
    assert rows["c1"]["geo_match_status"] == MATCH_STATUS_MATCHED
    assert rows["c1"]["plow_zone"] == "A"
    assert rows["c1"]["has_geo"] is True

    assert rows["c2"]["geo_match_status"] == MATCH_STATUS_UNMATCHED
    assert rows["c2"]["plow_zone"] is None
    assert rows["c2"]["has_geo"] is True

    # The no-geo branch bypasses the UDF entirely (attribute_zones splits
    # first), so it is the one most able to drift into a fourth value.
    assert rows["c3"]["geo_match_status"] == MATCH_STATUS_NO_GEO
    assert rows["c3"]["plow_zone"] is None
    assert rows["c3"]["has_geo"] is False


def test_hit_rate_floor_ignores_rows_without_coordinates(spark, bronze):
    """79% of the upstream carries no coordinates. If those counted against the
    hit rate the guard would fire on every healthy window.
    """
    records = [_record("c0", "i0", "2024-01-05T09:00:00.000", point=(0.5, 0.5))]
    records += [
        _record(f"n{i}", f"i{i}", "2024-01-05T09:00:00.000")
        for i in range(job.MIN_HIT_RATE_SAMPLE * 2)
    ]
    _write_bronze(bronze, {"2024-01": records})
    _write_boundary(spark, bronze)

    job.run(spark, BUCKET, date(2024, 1, 1), date(2024, 2, 1))  # must not raise

    assert _silver(spark, bronze).count() == len(records)


# ── Primary key ───────────────────────────────────────────────────────────────


def test_duplicate_composite_key_raises(spark, bronze):
    _write_bronze(bronze, {"2024-01": [
        _record("c1", "i1", "2024-01-05T09:00:00.000", point=(0.5, 0.5)),
        _record("c1", "i1", "2024-01-05T09:00:00.000", point=(0.5, 0.5)),
    ]})
    _write_boundary(spark, bronze)

    with pytest.raises(RuntimeError, match="primary key"):
        job.run(spark, BUCKET, date(2024, 1, 1), date(2024, 2, 1))


def test_repeated_case_id_with_distinct_interaction_is_kept(spark, bronze):
    """The grain is an interaction. case_id is 1.87% non-unique upstream, and
    those repeats are real rows — de-duplicating on it deletes data.
    """
    _write_bronze(bronze, {"2024-01": [
        _record("c1", "i1", "2024-01-05T09:00:00.000", point=(0.5, 0.5)),
        _record("c1", "i2", "2024-01-05T11:00:00.000", point=(0.5, 0.5)),
    ]})
    _write_boundary(spark, bronze)

    job.run(spark, BUCKET, date(2024, 1, 1), date(2024, 2, 1))

    assert _silver(spark, bronze).count() == 2


# ── Window trimming and rejects ───────────────────────────────────────────────


def test_neighbouring_month_rows_are_trimmed_by_the_window(spark, bronze):
    """A month folder holds days either side of start/end — the folder is the
    read unit, the window is the correctness unit.
    """
    _write_bronze(bronze, {
        "2024-01": [_record("c0", "i0", "2024-01-31T09:00:00.000", point=(0.5, 0.5))],
        "2024-02": [
            _record("c1", "i1", "2024-02-10T09:00:00.000", point=(0.5, 0.5)),
            _record("c2", "i2", "2024-02-28T09:00:00.000", point=(0.5, 0.5)),
        ],
    })
    _write_boundary(spark, bronze)

    job.run(spark, BUCKET, date(2024, 2, 1), date(2024, 2, 15))

    assert [r["case_id"] for r in _silver(spark, bronze).collect()] == ["c1"]


def test_rows_missing_a_required_field_are_rejected_not_dropped(spark, bronze, tmp_path):
    _write_bronze(bronze, {"2024-01": [
        _record("c1", "i1", "2024-01-05T09:00:00.000", point=(0.5, 0.5)),
        _record("c2", "i2", "2024-01-06T09:00:00.000", point=(0.5, 0.5), type=None),
        _record("c3", "i3", "not a timestamp", point=(0.5, 0.5)),
    ]})
    _write_boundary(spark, bronze)

    job.run(spark, BUCKET, date(2024, 1, 1), date(2024, 2, 1))

    assert _silver(spark, bronze).count() == 1
    rejects = spark.read.parquet(
        str(bronze / "rejects" / "window=2024-01-01_2024-02-01")
    )
    assert {r["_reject_reason"] for r in rejects.collect()} == {
        "missing_type",
        "unparseable_open_date",
    }


def test_closed_date_may_be_null(spark, bronze):
    """3.5% of rows are open cases. Requiring closed_date would reject them."""
    _write_bronze(bronze, {"2024-01": [
        _record("c1", "i1", "2024-01-05T09:00:00.000", point=(0.5, 0.5), closed_date=None),
    ]})
    _write_boundary(spark, bronze)

    job.run(spark, BUCKET, date(2024, 1, 1), date(2024, 2, 1))

    assert _silver(spark, bronze).collect()[0]["closed_ts_utc"] is None


# ── Idempotency ───────────────────────────────────────────────────────────────


def test_rerunning_a_window_leaves_other_partitions_alone(spark, bronze):
    """The dynamic-overwrite guard, from the outside. Without it the second run
    would delete January while writing February — and report success.
    """
    _write_bronze(bronze, {
        "2024-01": [_record("c1", "i1", "2024-01-05T09:00:00.000", point=(0.5, 0.5))],
        "2024-02": [_record("c2", "i2", "2024-02-05T09:00:00.000", point=(0.5, 0.5))],
    })
    _write_boundary(spark, bronze)

    job.run(spark, BUCKET, date(2024, 1, 1), date(2024, 2, 1))
    job.run(spark, BUCKET, date(2024, 2, 1), date(2024, 3, 1))
    job.run(spark, BUCKET, date(2024, 2, 1), date(2024, 3, 1))  # again: idempotent

    rows = sorted(r["case_id"] for r in _silver(spark, bronze).collect())
    assert rows == ["c1", "c2"]


def test_empty_boundary_table_raises_rather_than_marking_everything_unmatched(
    spark, bronze
):
    _write_bronze(bronze, {"2024-01": [
        _record("c1", "i1", "2024-01-05T09:00:00.000", point=(0.5, 0.5)),
    ]})
    spark.createDataFrame(
        [], "plow_zone string, geometry_wkt string"
    ).write.mode("overwrite").parquet(str(bronze / "boundary"))

    with pytest.raises(ValueError, match="no polygons"):
        job.run(spark, BUCKET, date(2024, 1, 1), date(2024, 2, 1))


# ── C7 write width ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("days", "expected"),
    [(1, 1), (30, 30), (64, 64), (400, 64)],
)
def test_write_partition_count_is_clamped(days, expected):
    from datetime import timedelta

    start = date(2024, 1, 1)
    assert job.write_partition_count(start, start + timedelta(days=days)) == expected


# ── Bronze path construction ──────────────────────────────────────────────────


def test_month_prefixes_cost_one_path_per_month():
    prefixes = job.bronze_month_prefixes(BUCKET, date(2024, 1, 1), date(2025, 1, 1))

    assert len(prefixes) == 12
    assert prefixes[0] == f"{BASE}/2024-01/"
    assert prefixes[-1] == f"{BASE}/2024-12/"


def test_month_prefixes_end_is_exclusive_at_a_boundary():
    assert job.bronze_month_prefixes(BUCKET, date(2024, 3, 1), date(2024, 4, 1)) == [
        f"{BASE}/2024-03/"
    ]


def test_month_prefixes_roll_over_a_leap_february():
    assert job.bronze_month_prefixes(BUCKET, date(2024, 2, 1), date(2024, 3, 2)) == [
        f"{BASE}/2024-02/",
        f"{BASE}/2024-03/",
    ]


def test_read_schema_declares_every_silver_source_column():
    """No inference (AGENTS.md): interaction_id is a numeric-looking *string*
    and would be typed per-month by the reader.
    """
    fields = {f.name: f.dataType.simpleString() for f in job.read_schema().fields}

    assert fields["interaction_id"] == "string"
    assert fields["geometry"] == "struct<type:string,coordinates:array<double>>"
