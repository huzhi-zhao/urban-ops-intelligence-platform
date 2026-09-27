"""Unit tests for H2-R11's forecast reconstruction. No network, no object storage."""

from __future__ import annotations

import datetime as dt
import gzip
import json

import pytest

from models.request_forecast.outlook_weather import hourly_to_daily
from scripts.models import outlook_m1, outlook_rehearsal
from scripts.models import reconstruct_forecast as rc

CONFIG = outlook_m1.load_config()
FIELDS = rc.Fields.from_config(CONFIG)
ISSUE = dt.date(2025, 12, 18)


def _hourly(start: dt.date, days: int, missing: set[tuple[dt.date, int]] = frozenset()) -> dict:
    """Every value encodes (lead, day offset) so a test can see which lead was read.

    snowfall = lead + day_offset / 100; temperature = -lead. ``missing`` blanks
    one (day, lead) for every hour of that day.
    """
    hourly: dict[str, list] = {"time": []}
    for f in FIELDS.values:
        for lead in range(1, rc.MAX_LEAD_DAYS + 1):
            hourly[rc.lead_variable(f, lead)] = []
    for d in range(days):
        day = start + dt.timedelta(days=d)
        for hour in range(24):
            hourly["time"].append(f"{day.isoformat()}T{hour:02d}:00")
            for lead in range(1, rc.MAX_LEAD_DAYS + 1):
                gone = (day, lead) in missing
                hourly[rc.lead_variable(FIELDS.snowfall, lead)].append(
                    None if gone else lead + d / 100)
                hourly[rc.lead_variable(FIELDS.temperature, lead)].append(
                    None if gone else -float(lead))
    return hourly


def test_day_k_reads_lead_k_plus_one():
    """🔴 The no-leak rule: day D+k must come from previous_day{k+1}."""
    records = rc.reconstruct(_hourly(ISSUE, 10), ISSUE, FIELDS)

    by_day: dict[str, set[float]] = {}
    for r in records:
        by_day.setdefault(r[FIELDS.time][:10], set()).add(r[FIELDS.temperature])
    assert len(by_day) == rc.MAX_LEAD_DAYS
    for k in range(rc.MAX_LEAD_DAYS):
        day = (ISSUE + dt.timedelta(days=k)).isoformat()
        assert by_day[day] == {-(k + 1.0)}


def test_the_vintage_starts_on_its_issue_date():
    records = rc.reconstruct(_hourly(ISSUE - dt.timedelta(days=3), 12), ISSUE, FIELDS)

    assert records[0][FIELDS.time] == f"{ISSUE.isoformat()}T00:00"


def test_a_missing_lead_ends_the_vintage_rather_than_leaving_a_gap():
    gap = ISSUE + dt.timedelta(days=4)
    records = rc.reconstruct(_hourly(ISSUE, 10, {(gap, 5)}), ISSUE, FIELDS)

    assert rc.horizon_of(records, FIELDS) == 4


def test_a_missing_lead_for_another_day_does_not_matter():
    """Day D+4 reads lead 5; lead 7 missing on that day is irrelevant to it."""
    day = ISSUE + dt.timedelta(days=4)
    records = rc.reconstruct(_hourly(ISSUE, 10, {(day, 7)}), ISSUE, FIELDS)

    assert rc.horizon_of(records, FIELDS) == rc.MAX_LEAD_DAYS


def test_no_coverage_gives_an_empty_vintage():
    records = rc.reconstruct(_hourly(ISSUE, 10, {(ISSUE, 1)}), ISSUE, FIELDS)

    assert records == []


def test_a_window_that_ends_early_truncates_the_horizon():
    records = rc.reconstruct(_hourly(ISSUE, 3), ISSUE, FIELDS)

    assert rc.horizon_of(records, FIELDS) == 3


def test_records_go_through_the_chain_daily_step():
    records = rc.reconstruct(_hourly(ISSUE, 10), ISSUE, FIELDS)
    fc = CONFIG["forecast"]

    days = hourly_to_daily(records, time_field=fc["time_field"],
                           snowfall_field=fc["snowfall_field"],
                           temperature_field=fc["temperature_field"],
                           min_hours_per_day=int(fc["min_hours_per_day"]))

    assert [d.day for d in days] == [ISSUE + dt.timedelta(days=k) for k in range(7)]
    assert days[0].snowfall_cm == pytest.approx(24 * 1.0)


def test_the_payload_uses_the_snapshot_field_names():
    records = rc.reconstruct(_hourly(ISSUE, 10), ISSUE, FIELDS)

    assert set(records[0]) == {FIELDS.time, FIELDS.snowfall, FIELDS.temperature}


def test_coverage_counts_each_lead_separately():
    table = rc.coverage(_hourly(ISSUE, 2, {(ISSUE, 7)}), FIELDS)

    assert table[("2025-12", 1)] == (48, 48)
    assert table[("2025-12", 7)] == (24, 48)


def test_a_season_is_issued_daily_from_the_first_month_to_april():
    issues = rc.season_issue_dates("2025-2026", first_month=11)

    assert issues[0] == dt.date(2025, 11, 1)
    assert issues[-1] == dt.date(2026, 4, 30)
    assert len(issues) == 181


@pytest.mark.parametrize("root", ["bronze/raw", "smoke-r12/x", "gold/_outlook_runs", "", "/research"])
def test_a_root_outside_research_is_refused(root):
    with pytest.raises(rc.ReconstructionError, match="research/"):
        rc.check_research_root(root)


def test_the_location_comes_from_the_forecast_source_yaml():
    params = outlook_m1.forecast_query_params(CONFIG)

    assert rc.location_params(CONFIG) == {k: params[k] for k in rc.LOCATION_PARAMS}


class _FakeClient:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_object(self, Bucket, Key, Body):  # noqa: N803 - boto3's signature
        self.objects[Key] = Body


def test_a_written_vintage_says_it_is_reconstructed_and_not_synthetic():
    client = _FakeClient()
    records = rc.reconstruct(_hourly(ISSUE, 10), ISSUE, FIELDS)

    prefix = outlook_rehearsal.put_vintage(client, "uoip", rc.RESEARCH_ROOT, CONFIG, ISSUE,
                                           records, provenance=rc.provenance(records, FIELDS))

    manifest = json.loads(client.objects[f"{prefix}/manifest.json"])
    payload = gzip.decompress(client.objects[f"{prefix}/data.ndjson.gz"])
    assert prefix.startswith("research/r11/reconstructed/")
    assert manifest["reconstructed"] is True
    assert manifest["method"] == rc.METHOD
    assert manifest["horizon_days"] == 7
    assert "synthetic" not in manifest
    assert payload.count(b"\n") == len(records)


def test_the_reconstructed_root_cannot_reach_the_production_artefact_root():
    with pytest.raises(outlook_m1.OutlookRunError, match="not production"):
        outlook_m1.resolve_artefact_root(CONFIG, rc.RESEARCH_ROOT, None)


@pytest.mark.parametrize("roots", [
    ("bronze/raw", "research/r11/outlook_runs"),
    ("research/r11/reconstructed", "gold/_outlook_runs"),
    ("smoke-r12/x/bronze/raw", "research/r11/outlook_runs"),
])
def test_the_backtest_refuses_any_root_outside_research(roots):
    from scripts.models import outlook_backtest

    with pytest.raises(outlook_m1.OutlookRunError, match="research/"):
        outlook_backtest.check_roots(*roots)
