"""Unit tests for the R12 rehearsal (batch B) and the guards around it.

No pandas, no object storage: the scenarios' synthetic weather is run through
the real hourly -> daily -> event path offline, so a scenario that could never
produce what it claims fails here rather than on the compute node. The model
half is exercised by the rehearsal itself.
"""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json

import pytest

from models.request_forecast.outlook_weather import (
    DailyWeather,
    EventRule,
    hourly_to_daily,
    segment_events,
    splice,
)
from scripts.models import outlook_m1
from scripts.models import outlook_rehearsal as reh

CONFIG = outlook_m1.load_config()
RULE = EventRule.from_config(CONFIG["event_rule"])
PARAMS = outlook_m1.forecast_query_params(CONFIG)
PAST, HORIZON = int(PARAMS["past_days"]), int(PARAMS["forecast_days"])
ISSUE = reh.DEFAULT_ISSUE_DATE
FIELDS = CONFIG["forecast"]

# What each scenario must cut to, independent of the model.
EXPECTED_EVENTS = {
    "no_snow": 0, "moderate": 1, "heavy": 1, "extreme": 1,
    "accum_only": 1, "horizon": 1, "partial_day": 0,
}


def _scenario(name: str) -> reh.Scenario:
    return next(s for s in reh.scenarios(units=22, horizon=HORIZON) if s.name == name)


def _cut(name: str):
    scenario = _scenario(name)
    records = reh.build_records(scenario, ISSUE, PAST, HORIZON, FIELDS)
    forecast = hourly_to_daily(
        records, time_field=FIELDS["time_field"], snowfall_field=FIELDS["snowfall_field"],
        temperature_field=FIELDS["temperature_field"],
        min_hours_per_day=int(FIELDS["min_hours_per_day"]),
    )
    # A dry observed month before the forecast, as at the default issue date.
    observed = [
        DailyWeather(ISSUE - dt.timedelta(days=i), 0.0, -10.0) for i in range(30, 0, -1)
    ]
    days = splice(observed, forecast)
    events = [e for e in segment_events(days, RULE) if e.end_date >= ISSUE]
    return forecast, events


def test_every_design_scenario_is_present():
    assert {s.name for s in reh.scenarios(22, HORIZON)} == set(EXPECTED_EVENTS)


@pytest.mark.parametrize("name", sorted(EXPECTED_EVENTS))
def test_each_scenario_cuts_to_the_events_it_was_built_for(name):
    _forecast, events = _cut(name)

    assert len(events) == EXPECTED_EVENTS[name]
    assert all(e.start_date >= ISSUE for e in events)


def test_the_accumulation_scenario_has_no_single_qualifying_day():
    _forecast, [event] = _cut("accum_only")

    assert event.accum_flag is True
    assert event.peak_daily_snowfall_cm < RULE.threshold_cm


def test_the_horizon_scenario_ends_on_the_last_forecast_day():
    forecast, [event] = _cut("horizon")

    assert event.end_date == forecast[-1].day


def test_the_partial_day_is_dropped_from_the_forecast():
    forecast, _events = _cut("partial_day")

    assert forecast[-1].day == ISSUE + dt.timedelta(days=HORIZON - 2)


def test_the_extreme_scenario_exceeds_the_cohort_maximum():
    _forecast, [event] = _cut("extreme")

    # dim_snowfall_event's largest total, measured 2026-09-27.
    assert event.total_snowfall_cm > 29.05


def test_a_payload_has_the_snapshot_shape():
    records = reh.build_records(_scenario("moderate"), ISSUE, PAST, HORIZON, FIELDS)

    assert len(records) == (PAST + HORIZON) * 24
    assert records[0][FIELDS["time_field"]] == f"{ISSUE - dt.timedelta(days=PAST)}T00:00"
    assert set(records[0]) == {"time", "temperature_2m", "precipitation", "snowfall",
                               "windspeed_10m"}


class _FakeClient:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_object(self, Bucket, Key, Body):  # noqa: N803 - boto3's signature
        self.objects[Key] = Body


def test_a_vintage_is_written_with_a_manifest_that_verifies():
    client = _FakeClient()
    records = reh.build_records(_scenario("heavy"), ISSUE, PAST, HORIZON, FIELDS)

    prefix = reh.put_vintage(client, "uoip", "smoke-r12/x/heavy/bronze/raw", CONFIG, ISSUE, records)

    payload = gzip.decompress(client.objects[f"{prefix}/data.ndjson.gz"])
    manifest = json.loads(client.objects[f"{prefix}/manifest.json"])
    assert prefix.startswith("smoke-r12/x/heavy/bronze/raw/SRC-Open-Meteo/weather_forecast/")
    assert manifest["sha256_checksum"] == hashlib.sha256(payload).hexdigest()
    assert manifest["record_count"] == len(records)
    assert manifest["synthetic"] is True


# ── guards ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("prefix", ["bronze/raw", "gold", "", "r12-smoke", "/bronze"])
def test_the_rehearsal_refuses_a_prefix_that_is_not_smoke(prefix):
    with pytest.raises(SystemExit):
        reh.check_smoke_prefix(prefix)


def test_synthetic_input_cannot_reach_the_production_artefact_root():
    with pytest.raises(outlook_m1.OutlookRunError, match="not production"):
        outlook_m1.resolve_artefact_root(CONFIG, "smoke-r12/x/bronze/raw", None)
    with pytest.raises(outlook_m1.OutlookRunError, match="not production"):
        outlook_m1.resolve_artefact_root(
            CONFIG, "smoke-r12/x/bronze/raw", CONFIG["artefact_root"] + "/smoke"
        )


def test_production_input_uses_the_production_artefact_root():
    assert outlook_m1.resolve_artefact_root(CONFIG, "bronze/raw", None) == CONFIG["artefact_root"]
    assert (
        outlook_m1.resolve_artefact_root(CONFIG, "smoke-r12/x/bronze/raw", "smoke-r12/x/gold")
        == "smoke-r12/x/gold"
    )


# ── scheduling helpers (batch C) ────────────────────────────────────────────────


def test_the_issue_date_is_the_city_local_date_not_the_utc_one():
    # 03:00 UTC on the 16th is still the evening of the 15th in Winnipeg.
    moment = dt.datetime(2026, 11, 16, 3, 0, tzinfo=dt.UTC)

    assert outlook_m1.issue_date_for(moment, CONFIG) == dt.date(2026, 11, 15)


def test_a_naive_moment_is_refused():
    with pytest.raises(outlook_m1.OutlookRunError, match="timezone-aware"):
        outlook_m1.issue_date_for(dt.datetime(2026, 11, 16, 3, 0), CONFIG)


def test_the_scheduled_run_uses_the_configured_version_and_never_overwrites():
    argv = outlook_m1.scheduled_argv(dt.date(2026, 11, 15), CONFIG, "uoip", "/tmp/x")

    assert argv[argv.index("--model-version") + 1] == CONFIG["serving_model_version"]
    assert "--skip-existing" in argv and "--upload" in argv
    assert "nomonth" not in CONFIG["serving_model_version"]
