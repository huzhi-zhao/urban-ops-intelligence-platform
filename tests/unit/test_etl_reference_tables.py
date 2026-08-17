"""End-to-end unit tests for the three static reference-table Silver jobs.

Runs each job's ``run()`` against a local temp directory instead of object
storage, by monkeypatching the path helpers. That is worth the setup here:
these jobs are mostly *assertions*, and an assertion that never fires is
indistinguishable from one that is wired wrong.

Fixture data mirrors the measured upstream shape (19 bans x 22 zones = 418
shifts, 49 bans) because the jobs assert on those exact numbers.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

pyspark = pytest.importorskip("pyspark")
shapely = pytest.importorskip("shapely")

from pyspark.sql import SparkSession  # noqa: E402

from spark.jobs import etl_parking_ban, etl_plow_shift, etl_snow_clearing_address  # noqa: E402


@pytest.fixture(scope="module")
def spark():
    session = (
        SparkSession.builder.master("local[1]")
        .appName("test_etl_reference_tables")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )
    yield session
    session.stop()


def _write_ndjson(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records))
    return str(path)


# 22 scheduled zones, matching the measured cardinality the job asserts on.
_ZONES = [chr(ord("A") + i) for i in range(22)]


def _shift_records(bans=19, zones=_ZONES):
    records = []
    for ban in range(bans):
        for index, zone in enumerate(zones):
            shift_number = index % 5 + 1
            records.append(
                {
                    "id": f"{ban}-{zone}",
                    "snow_ban_id": str(ban),
                    "shift_number": str(shift_number),
                    # Floating timestamps: local wall clock, no offset.
                    "shift_start": f"2024-01-{ban % 28 + 1:02d}T06:00:00.000",
                    "shift_end": f"2024-01-{ban % 28 + 1:02d}T18:00:00.000",
                    "plow_zone": zone,
                }
            )
    return records


def _ban_records(count=49):
    return [
        {
            "id": str(i),
            "description": "ANNUAL SNOW ROUTE",
            "description_french": "ROUTE DE DENEIGEMENT ANNUELLE",
            "ban_type_id": "1",
            "ban_start": f"2024-01-{i % 28 + 1:02d}T00:00:00.000",
            "ban_end": f"2024-02-{i % 28 + 1:02d}T00:00:00.000",
        }
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# etl_plow_shift
# ---------------------------------------------------------------------------


@pytest.fixture
def plow_shift_paths(tmp_path, monkeypatch):
    def install(records):
        bronze = _write_ndjson(tmp_path / "bronze" / "shifts.json", records)
        monkeypatch.setattr(etl_plow_shift, "_bronze_path", lambda bucket: bronze)
        monkeypatch.setattr(
            etl_plow_shift, "_silver_path", lambda bucket: str(tmp_path / "silver")
        )
        monkeypatch.setattr(
            etl_plow_shift, "_rejects_path", lambda bucket: str(tmp_path / "rejects")
        )
        return tmp_path / "silver"

    return install


def test_plow_shift_writes_418_rows(spark, plow_shift_paths):
    silver = plow_shift_paths(_shift_records())

    etl_plow_shift.run(spark, bucket="test")

    out = spark.read.parquet(str(silver))
    assert out.count() == 418
    assert out.select("plow_zone").distinct().count() == 22


def test_plow_shift_localizes_floating_timestamps_to_utc(spark, plow_shift_paths):
    """06:00 local in January (CST, UTC-6) is 12:00 UTC.

    If the raw value were taken as already-UTC, this would read back as 06:00
    and every shift in the table would sit six hours early — which no row
    count or uniqueness check would ever catch.
    """
    silver = plow_shift_paths(_shift_records())

    etl_plow_shift.run(spark, bucket="test")

    row = spark.read.parquet(str(silver)).orderBy("id").first()
    assert row["shift_start_utc"] == datetime(2024, 1, 1, 12, 0)
    assert row["shift_end_utc"] == datetime(2024, 1, 2, 0, 0)


def test_plow_shift_casts_shift_number_to_int(spark, plow_shift_paths):
    silver = plow_shift_paths(_shift_records())

    etl_plow_shift.run(spark, bucket="test")

    out = spark.read.parquet(str(silver))
    assert dict(out.dtypes)["shift_number"] == "int"
    assert out.agg({"shift_number": "max"}).collect()[0][0] == 5


def test_plow_shift_fails_on_a_short_pull(spark, plow_shift_paths):
    """A whole-table pull that came back short is a truncated fetch, and 418
    is a hard gate three tables downstream."""
    plow_shift_paths(_shift_records(bans=18))

    with pytest.raises(RuntimeError, match="expected exactly 418"):
        etl_plow_shift.run(spark, bucket="test")


def test_plow_shift_accepts_any_22_zone_labels(spark, plow_shift_paths):
    """The check is on cardinality, not on which labels — an upstream rename
    within the same 22 is not this job's business."""
    plow_shift_paths(_shift_records(bans=19, zones=_ZONES[:21] + ["Downtown"]))

    etl_plow_shift.run(spark, bucket="test")


def test_plow_shift_fails_on_changed_zone_cardinality(spark, plow_shift_paths):
    """dim_plow_zone.has_plow_schedule is modelled against exactly 22.

    Row count stays at 418 so the count check passes and the zone check is
    the one that has to fire.
    """
    records = _shift_records(bans=19, zones=_ZONES[:11])
    records += _shift_records(bans=19, zones=_ZONES[:11])
    for index, record in enumerate(records):
        record["id"] = str(index)  # keep the PK unique; only the zone set shrinks
    plow_shift_paths(records)

    with pytest.raises(RuntimeError, match="distinct plow_zone"):
        etl_plow_shift.run(spark, bucket="test")


def test_plow_shift_rejects_row_with_null_id_rather_than_dropping_it(spark, plow_shift_paths):
    records = _shift_records()
    records[0]["id"] = None
    tmp = plow_shift_paths(records)

    # 417 valid rows, so the exact-count assertion fires — which is the point:
    # a reference table that quietly shrinks must not reach Gold.
    with pytest.raises(RuntimeError, match="expected exactly 418"):
        etl_plow_shift.run(spark, bucket="test")

    rejects = spark.read.parquet(str(tmp.parent / "rejects"))
    assert rejects.count() == 1
    assert rejects.first()["_reject_reason"] == "missing_id"


# ---------------------------------------------------------------------------
# etl_parking_ban
# ---------------------------------------------------------------------------


@pytest.fixture
def parking_ban_paths(tmp_path, monkeypatch):
    def install(records):
        bronze = _write_ndjson(tmp_path / "bronze" / "bans.json", records)
        monkeypatch.setattr(etl_parking_ban, "_bronze_path", lambda bucket: bronze)
        monkeypatch.setattr(
            etl_parking_ban, "_silver_path", lambda bucket: str(tmp_path / "silver")
        )
        monkeypatch.setattr(
            etl_parking_ban, "_rejects_path", lambda bucket: str(tmp_path / "rejects")
        )
        return tmp_path / "silver"

    return install


def test_parking_ban_writes_49_rows(spark, parking_ban_paths):
    silver = parking_ban_paths(_ban_records())

    etl_parking_ban.run(spark, bucket="test")

    assert spark.read.parquet(str(silver)).count() == 49


def test_parking_ban_drops_the_bilingual_description_columns(spark, parking_ban_paths):
    """ban_type_id already carries the 3-value distinction; free text in
    Silver is text someone downstream will parse."""
    silver = parking_ban_paths(_ban_records())

    etl_parking_ban.run(spark, bucket="test")

    columns = set(spark.read.parquet(str(silver)).columns)
    assert "description" not in columns
    assert "description_french" not in columns


def test_parking_ban_keeps_ban_type_id_as_a_string(spark, parking_ban_paths):
    """Domain is 1/2/4 with 3 absent — it is a code, not a quantity."""
    silver = parking_ban_paths(_ban_records())

    etl_parking_ban.run(spark, bucket="test")

    assert dict(spark.read.parquet(str(silver)).dtypes)["ban_type_id"] == "string"


def test_parking_ban_accepts_a_null_ban_end(spark, parking_ban_paths):
    """An open ban has no end yet. That is a state, not a defect."""
    records = _ban_records()
    records[0]["ban_end"] = None
    silver = parking_ban_paths(records)

    etl_parking_ban.run(spark, bucket="test")

    out = spark.read.parquet(str(silver))
    assert out.count() == 49
    assert out.filter("ban_end_utc IS NULL").count() == 1


# ---------------------------------------------------------------------------
# etl_snow_clearing_address
# ---------------------------------------------------------------------------

# Two unit squares side by side, matching the boundary table's column names.
_BOUNDARY_ROWS = [
    ("Z1", "POLYGON ((0 0, 0 1, 1 1, 1 0, 0 0))"),
    ("Z2", "POLYGON ((2 0, 2 1, 3 1, 3 0, 2 0))"),
]


def _address_records(per_zone, outside=0, no_geo=0, ingest_date="2026-08-15"):
    records = []
    for longitude, count in ((0.5, per_zone[0]), (2.5, per_zone[1])):
        records += [
            {"has_street": True, "location": {"type": "Point", "coordinates": [longitude, 0.5]}}
            for _ in range(count)
        ]
    records += [
        {"has_street": True, "location": {"type": "Point", "coordinates": [99.0, 99.0]}}
        for _ in range(outside)
    ]
    records += [{"has_street": True, "location": None} for _ in range(no_geo)]
    return ingest_date, records


@pytest.fixture
def snow_clearing_paths(tmp_path, monkeypatch, spark):
    # The real job asserts 25 zones and a 200k-row floor; the fixture has 2
    # zones and a handful of rows, so both are scaled down. Everything else —
    # the path parsing, the zone join, the schema enforcement — is the
    # production code path.
    monkeypatch.setattr(etl_snow_clearing_address, "EXPECTED_ZONE_COUNT", 2)
    monkeypatch.setattr(etl_snow_clearing_address, "MIN_EXPECTED_ADDRESS_ROWS", 3)

    boundary_dir = tmp_path / "boundary"
    spark.createDataFrame(_BOUNDARY_ROWS, ["plow_zone", "geometry_wkt"]).write.parquet(
        str(boundary_dir)
    )
    monkeypatch.setattr(
        etl_snow_clearing_address, "_boundary_path", lambda bucket: str(boundary_dir)
    )
    monkeypatch.setattr(
        etl_snow_clearing_address, "_silver_path", lambda bucket: str(tmp_path / "silver")
    )

    def install(*day_records):
        for ingest_date, records in day_records:
            _write_ndjson(
                tmp_path / "bronze" / f"ingest_date={ingest_date}" / "data.json", records
            )
        monkeypatch.setattr(
            etl_snow_clearing_address,
            "_bronze_glob",
            lambda bucket: str(tmp_path / "bronze" / "ingest_date=*" / "data.json"),
        )
        return tmp_path / "silver"

    return install


def test_snow_clearing_counts_addresses_per_zone(spark, snow_clearing_paths):
    silver = snow_clearing_paths(_address_records(per_zone=(7, 4)))

    etl_snow_clearing_address.run(spark, bucket="test")

    out = {row["plow_zone"]: row["address_count"] for row in spark.read.parquet(str(silver)).collect()}
    assert out == {"Z1": 7, "Z2": 4}


def test_snow_clearing_excludes_unmatched_and_missing_geometry(spark, snow_clearing_paths):
    """Points outside every polygon and rows with no location must not be
    attributed to a zone — they would inflate a denominator BO-6 divides by."""
    silver = snow_clearing_paths(_address_records(per_zone=(7, 4), outside=5, no_geo=6))

    etl_snow_clearing_address.run(spark, bucket="test")

    out = spark.read.parquet(str(silver))
    assert out.agg({"address_count": "sum"}).collect()[0][0] == 11


def test_snow_clearing_defaults_to_the_freshest_collection(spark, snow_clearing_paths):
    silver = snow_clearing_paths(
        _address_records(per_zone=(7, 4), ingest_date="2026-08-14"),
        _address_records(per_zone=(1, 2), ingest_date="2026-08-15"),
    )

    etl_snow_clearing_address.run(spark, bucket="test")

    out = spark.read.parquet(str(silver))
    assert {row["address_count"] for row in out.collect()} == {1, 2}
    assert {str(row["snapshot_date"]) for row in out.collect()} == {"2026-08-15"}


def test_snow_clearing_can_rebuild_an_older_collection(spark, snow_clearing_paths):
    from datetime import date

    silver = snow_clearing_paths(
        _address_records(per_zone=(7, 4), ingest_date="2026-08-14"),
        _address_records(per_zone=(1, 2), ingest_date="2026-08-15"),
    )

    etl_snow_clearing_address.run(spark, bucket="test", snapshot_date=date(2026, 8, 14))

    out = spark.read.parquet(str(silver))
    assert {row["address_count"] for row in out.collect()} == {7, 4}


def test_snow_clearing_fails_on_a_truncated_collection(spark, snow_clearing_paths):
    """A short collection would understate every zone's denominator at once."""
    snow_clearing_paths(_address_records(per_zone=(1, 1)))

    with pytest.raises(RuntimeError, match="truncated collection"):
        etl_snow_clearing_address.run(spark, bucket="test")


def test_snow_clearing_fails_on_an_uncollected_date(spark, snow_clearing_paths):
    from datetime import date

    snow_clearing_paths(_address_records(per_zone=(7, 4), ingest_date="2026-08-15"))

    with pytest.raises(RuntimeError, match="never collected"):
        etl_snow_clearing_address.run(spark, bucket="test", snapshot_date=date(2026, 8, 1))
