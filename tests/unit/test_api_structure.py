"""
Test 1 — API Structure Test (contract regression against the live Socrata API)

Fetches a sample from the 311 endpoint and validates the response shape against
the field list this project depends on.

 "记录 API 在某个时间点的响应格式"，用作回归检测——如果未来 API 字段变了，这个测试会失败。

NOTE — this file is the one exception in tests/unit/ that touches the network:
CLAUDE.md defines tests/unit/ as pure Python with no cloud deps, and a live HTTP
call means `make test-unit` fails offline and can flake on upstream hiccups.
It is marked `@pytest.mark.network` so it can be excluded:

    python -m pytest tests/unit/ -m "not network"     # offline-safe
    python -m pytest tests/unit/test_api_structure.py -v   # just this file

It arguably belongs in tests/integration/; left here deliberately so that a
routine `make test-unit` still surfaces upstream schema drift, which CLAUDE.md
lists as an escalate-to-human event.

No credentials required (public endpoint).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure project root on path for absolute imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ingestion.clients.socrata_client import SocrataClient

# Expected Socrata fields for SRC-NYC-311 (311).
#
# "Required" here means: the field must appear on EVERY record. Socrata omits a
# key entirely rather than sending null, so any field that is legitimately
# sometimes-absent belongs in OPTIONAL_FIELDS or this test becomes a coin flip.
REQUIRED_FIELDS = [
    "unique_key",
    "created_date",
    "complaint_type",
    "descriptor",
    "borough",
    "incident_zip",
    "status",
]

# latitude/longitude are NOT required: records that were never geocoded arrive
# with the keys missing altogether (~1 in 50 sampled live, and closed_date is
# absent on ~5% per reports/bronze_quality_report.md). They used to sit in
# REQUIRED_FIELDS, which made this test fail whenever the single sampled record
# happened to be un-geocoded — that is a data property, not a schema change.
OPTIONAL_FIELDS = [
    "latitude",
    "longitude",
    "closed_date",
    "agency",
    "agency_name",
    "location",
]

# Sample more than one record: the point of this test is to detect upstream
# field renames/removals, and a single arbitrary record can't distinguish
# "field removed from the dataset" from "field null on this one row".
SAMPLE_SIZE = 50

EXPECTED_BOROUGHS = {
    "MANHATTAN", "BROOKLYN", "BRONX", "QUEENS", "STATEN ISLAND", "Unspecified"
}


@pytest.mark.network
def test_311_api_returns_valid_structure():
    """Fetch a sample from the live 311 API and validate its shape."""
    client = SocrataClient(resource_id="erm2-nwe9", domain="data.cityofnewyork.us")

    records = client.fetch_page(limit=SAMPLE_SIZE)
    assert len(records) >= 1, "API should return at least 1 record"

    # Every required field must be present on every record. Reported in
    # aggregate so an upstream rename shows up as "absent on all 50" rather
    # than as a single confusing row failure.
    for field in REQUIRED_FIELDS:
        present = sum(1 for r in records if field in r)
        assert present == len(records), (
            f"Required field '{field}' missing on {len(records) - present}/{len(records)} "
            f"records. If it is 0/{len(records)}, the upstream Socrata schema likely "
            f"changed — escalate per CLAUDE.md instead of relaxing this test."
        )

    # At least one sampled record should carry coordinates; if geocoding
    # vanished entirely that is a real upstream regression worth catching.
    geocoded = sum(1 for r in records if r.get("latitude") and r.get("longitude"))
    assert geocoded > 0, (
        f"No record in a {len(records)}-record sample had latitude/longitude — "
        "possible upstream geocoding outage."
    )

    from datetime import datetime

    for record in records:
        # created_date should be parseable as ISO datetime
        try:
            dt = datetime.fromisoformat(record["created_date"].replace("Z", "+00:00"))
        except ValueError as e:
            raise AssertionError(f"created_date not ISO parseable: {e}") from e
        assert dt.year >= 2010, f"created_date year should be >= 2010, got {dt.year}"

        # latitude/longitude, when present, must be numeric
        lat, lon = record.get("latitude", ""), record.get("longitude", "")
        if lat and lon:
            float(lat), float(lon)  # raises if not numeric

        # borough value should be a known NYC borough or Unspecified
        boro = record.get("borough", "").upper()
        assert boro in EXPECTED_BOROUGHS, f"Unknown borough: {boro}"

    print(f"✓ API structure valid — {len(records)} records, {geocoded} geocoded")
    print(f"  created_date : {records[0]['created_date']}")
    print(f"  borough      : {records[0].get('borough')}")
    print(f"  complaint    : {records[0].get('complaint_type')}")


def test_sample_fixture_structure():
    """Validate the local fixture file matches expected 311 schema."""
    fixture_path = Path(__file__).parent.parent / "fixtures" / "sample_311_response.json"
    assert fixture_path.exists(), f"Fixture not found: {fixture_path}"

    records = json.loads(fixture_path.read_text())
    assert len(records) == 2, "Fixture should have 2 sample records"

    for record in records:
        missing = [f for f in REQUIRED_FIELDS if f not in record]
        assert not missing, f"Fixture record missing fields: {missing}"

    print(f"✓ Fixture structure valid — {len(records)} records")


if __name__ == "__main__":
    print("=" * 60)
    print("Test 1 — API Structure Test")
    print("=" * 60)
    test_311_api_returns_valid_structure()
    test_sample_fixture_structure()
    print("\nAll structure tests passed ✓")
