"""Unit tests for the M1 forward chain (H2-R12 batch A): outlook.py + outlook_m1.py.

Needs the ``ml`` extra; run with ``make test-ml``.

The property everything here protects: **a forecast event is featurised by the
training path**, so a past event fed back in as a perfect forecast reproduces
the model's own prediction for it. Measured on production 2026-09-27 — all 59
scheduling-era events, 1,298 cells, max relative difference 6.4e-15 against F5.
"""

from __future__ import annotations

import gzip
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas", reason="needs the `ml` extra — run `make test-ml`")
np = pytest.importorskip("numpy", reason="needs the `ml` extra — run `make test-ml`")

from models.request_forecast import features as feat  # noqa: E402
from models.request_forecast import outlook as ol  # noqa: E402
from models.request_forecast.outlook_weather import (  # noqa: E402
    SeverityBounds,
    SnowfallEvent,
)
from scripts.models import outlook_m1 as cli  # noqa: E402
from scripts.models import train_m1  # noqa: E402

M1_CONFIG = train_m1.load_config()
NAMES = feat.feature_names(M1_CONFIG)
HOLDOUT = "2010-2011"

UNITS = {"A": 1000, "B": 2500}
EVENT_STARTS = [
    date(2008, 11, 20), date(2008, 12, 15), date(2009, 1, 20), date(2009, 3, 2),
    date(2009, 11, 25), date(2009, 12, 30), date(2010, 2, 10), date(2010, 3, 15),
    date(2010, 11, 18), date(2010, 12, 22), date(2011, 1, 30),
]


def _season(d: date) -> str:
    y = d.year if d.month >= 11 else d.year - 1
    return f"{y}-{y + 1}"


def _raw_panel() -> pd.DataFrame:
    """City-named, as the panel query returns it."""
    rng = np.random.default_rng(7)
    rows = []
    for i, start in enumerate(EVENT_STARTS):
        total = 3.0 + 2.0 * i
        tmin = -5.0 - i
        for unit, size in UNITS.items():
            rows.append({
                "plow_zone": unit,
                "snowfall_event_id": f"SNOW-{start:%Y%m%d}",
                "address_count": size,
                "start_date": start,
                "snow_season": _season(start),
                "is_scheduling_era": True,
                "total_snowfall_cm": total,
                "peak_daily_snowfall_cm": total / 2,
                "duration_days": 1 + i % 3,
                "min_temperature_c": tmin,
                "accum_flag": i % 4 == 0,
                "severity_score": 0.0,  # filled below from the cohort bounds
                "request_count": int(rng.integers(0, 40)) + (10 if unit == "B" else 0),
            })
    raw = pd.DataFrame(rows)
    events = raw.drop_duplicates("snowfall_event_id")
    bounds = SeverityBounds.from_cohort(events.total_snowfall_cm, events.min_temperature_c)
    raw["severity_score"] = [
        bounds.score(t, m) for t, m in zip(raw.total_snowfall_cm, raw.min_temperature_c, strict=True)
    ]
    return raw


def _panel() -> pd.DataFrame:
    return train_m1.to_role_names(_raw_panel(), M1_CONFIG)


def _bounds(panel: pd.DataFrame) -> SeverityBounds:
    cohort = panel.drop_duplicates("event_id")
    return SeverityBounds.from_cohort(cohort.total_snowfall_cm, cohort.min_temperature_c)


def _coefficients(seed: int = 3) -> dict[str, float]:
    rng = np.random.default_rng(seed)
    return {name: float(v) for name, v in zip(["const", *NAMES], rng.normal(0, 0.01, len(NAMES) + 1),
                                               strict=True)}


def _event_like(panel: pd.DataFrame, event_id: str) -> SnowfallEvent:
    row = panel[panel.event_id == event_id].iloc[0]
    start = row.event_start.date()
    return SnowfallEvent(
        start_date=start,
        end_date=start + timedelta(days=int(row.duration_days) - 1),
        duration_days=int(row.duration_days),
        total_snowfall_cm=float(row.total_snowfall_cm),
        peak_daily_snowfall_cm=float(row.peak_daily_snowfall_cm),
        min_temperature_c=float(row.min_temperature_c),
        accum_flag=bool(row.accum_flag),
        event_rule_version="v-test",
    )


# ── the core property ─────────────────────────────────────────────────────────


# One mid-panel event, and the last event of the last (holdout) season.
@pytest.mark.parametrize("event_id", ["SNOW-20091230", "SNOW-20100210", "SNOW-20110130"])
def test_a_replayed_event_is_featurised_exactly_as_training_did(event_id):
    panel = _panel()
    coef = _coefficients()

    training = feat.build_panel_features(panel)
    want_rows = training[training.event_id == event_id].sort_values("unit_id")
    x, _y, offset = feat.build_design_matrix(want_rows, NAMES)
    want = np.exp(x.to_numpy() @ np.array(list(coef.values())) + offset.to_numpy())

    prepared = ol.prepare_event(
        panel, _event_like(panel, event_id), event_id="OUTLOOK-X",
        bounds=_bounds(panel), season_first_month=11,
    ).sort_values("unit_id")
    got = ol.predict_mean(prepared, coef, NAMES)

    np.testing.assert_allclose(got, want, rtol=1e-12)
    for name in ("season_index", "month", "prev_target", "expanding_mean", "severity_score"):
        np.testing.assert_allclose(prepared[name].to_numpy(), want_rows[name].to_numpy(), rtol=1e-12)


def test_lags_stop_at_the_last_observed_event_for_each_forecast_event():
    """Two forecast events in one window: the second must not take the first's
    unknown count as its prev_target."""
    panel = _panel()
    last = panel.sort_values("event_start").event_id.iloc[-1]
    bounds = _bounds(panel)
    first = SnowfallEvent(date(2011, 12, 1), date(2011, 12, 1), 1, 5.0, 5.0, -8.0, False, "v")
    second = SnowfallEvent(date(2011, 12, 4), date(2011, 12, 4), 1, 6.0, 6.0, -9.0, False, "v")

    for event in (first, second):
        prepared = ol.prepare_event(panel, event, event_id="OUTLOOK-X", bounds=bounds,
                                    season_first_month=11)
        assert prepared.prev_target.notna().all()
        assert set(prepared.lag_asof_event_id) == {last}


def test_history_after_the_event_is_never_used():
    panel = _panel()
    event = _event_like(panel, "SNOW-20090120")

    prepared = ol.prepare_event(panel, event, event_id="OUTLOOK-X", bounds=_bounds(panel),
                                season_first_month=11)

    assert set(prepared.lag_asof_event_id) == {"SNOW-20081215"}


def test_mismatched_coefficients_are_refused():
    panel = _panel()
    prepared = ol.prepare_event(panel, _event_like(panel, "SNOW-20110130"), event_id="OUTLOOK-X",
                                bounds=_bounds(panel), season_first_month=11)
    coef = _coefficients()
    coef.pop("month")

    with pytest.raises(ol.OutlookError, match="feature list"):
        ol.predict_mean(prepared, coef, NAMES)


def test_extrapolation_is_flagged_not_clipped():
    panel = _panel()
    envelope = ol.TrainingEnvelope.from_panel(feat.build_panel_features(panel), HOLDOUT)
    huge = SnowfallEvent(date(2011, 12, 1), date(2011, 12, 1), 1, 500.0, 500.0, -8.0, False, "v")

    prepared = ol.prepare_event(panel, huge, event_id="OUTLOOK-X", bounds=_bounds(panel),
                                season_first_month=11)
    flagged = ol.flag_extrapolation(prepared, envelope)

    assert flagged.outside_total_snowfall_cm.all()
    assert flagged.outside_severity_score.all()
    assert flagged.severity_score.gt(1.0).all()
    # A new season is always beyond training — the flag H2-R3 has to judge.
    assert flagged.outside_season_index.all()


# ── CLI, end to end on files ──────────────────────────────────────────────────


def _write_inputs(tmp_path: Path, issue: date, snow_per_hour: float) -> dict[str, Path]:
    panel_path = tmp_path / "panel.csv"
    _raw_panel().to_csv(panel_path, index=False)

    metrics_path = tmp_path / "metrics.json"
    metrics_path.write_text(json.dumps({
        "model_version": "m1-test", "holdout_season": HOLDOUT,
        "panel_fingerprint": "x", "coefficients": _coefficients(),
    }))

    archive_path = tmp_path / "archive.csv"
    with archive_path.open("w") as handle:
        for i in range(40, 0, -1):
            handle.write(f"{issue - timedelta(days=i)},0.0,-10.0\n")

    records = [
        {"time": f"{issue + timedelta(days=d)}T{h:02d}:00", "snowfall": snow_per_hour,
         "temperature_2m": -12.0}
        for d in range(-3, 16) for h in range(24)
    ]
    forecast_path = tmp_path / "data.ndjson.gz"
    forecast_path.write_bytes(gzip.compress("\n".join(json.dumps(r) for r in records).encode()))
    return {"panel": panel_path, "metrics": metrics_path, "archive": archive_path,
            "forecast": forecast_path}


def _argv(paths: dict[str, Path], issue: date, out: Path) -> list[str]:
    return [
        "--issue-date", issue.isoformat(), "--model-version", "m1-test",
        "--panel-file", str(paths["panel"]), "--metrics-file", str(paths["metrics"]),
        "--archive-file", str(paths["archive"]), "--forecast-file", str(paths["forecast"]),
        "--out-dir", str(out),
    ]


@pytest.fixture
def no_env(monkeypatch):
    monkeypatch.setattr(cli, "load_cli_env", lambda: None)


def test_a_quiet_forecast_still_leaves_a_record(tmp_path, no_env):
    """"No snow forecast" and "the chain did not run" must not look alike."""
    issue = date(2011, 12, 1)
    paths = _write_inputs(tmp_path, issue, snow_per_hour=0.0)

    assert cli.main(_argv(paths, issue, tmp_path / "out")) == 0

    run_dir = tmp_path / "out" / "gold/_outlook_runs" / f"issue_date={issue}" / "m1-test"
    run = json.loads((run_dir / "run.json").read_text())
    assert run["event_count"] == 0
    assert run["interval"] is None
    assert pd.read_csv(run_dir / "outlook.csv").empty


def test_a_snowy_forecast_scores_every_unit_in_city_columns(tmp_path, no_env):
    issue = date(2011, 12, 1)
    paths = _write_inputs(tmp_path, issue, snow_per_hour=0.2)  # 4.8 cm/day

    assert cli.main(_argv(paths, issue, tmp_path / "out")) == 0

    run_dir = tmp_path / "out" / "gold/_outlook_runs" / f"issue_date={issue}" / "m1-test"
    outlook = pd.read_csv(run_dir / "outlook.csv")
    assert set(outlook.plow_zone) == set(UNITS)
    assert "address_count" in outlook.columns
    assert outlook.predicted_count.gt(0).all()
    # Snow every forecast day up to the horizon: the event cannot be complete.
    assert outlook.truncated_at_horizon.all()
    # The forecast's past_days began before issue, so the storm is ongoing.
    assert outlook.started_before_issue.all()


def test_an_issue_date_is_written_once(tmp_path, no_env):
    issue = date(2011, 12, 1)
    paths = _write_inputs(tmp_path, issue, snow_per_hour=0.0)
    out = tmp_path / "out"

    assert cli.main(_argv(paths, issue, out)) == 0
    assert cli.main(_argv(paths, issue, out)) == 1


def test_metrics_for_another_version_are_refused(tmp_path, no_env):
    issue = date(2011, 12, 1)
    paths = _write_inputs(tmp_path, issue, snow_per_hour=0.0)
    argv = _argv(paths, issue, tmp_path / "out")
    argv[argv.index("m1-test")] = "m1-other"

    assert cli.main(argv) == 1


def test_replay_check_refuses_divergence_and_empty_matches():
    outlook = pd.DataFrame({
        "event_start": [pd.Timestamp("2010-02-10")] * 2, "plow_zone": ["A", "B"],
        "predicted_count": [10.0, 20.0],
    })
    f5 = pd.DataFrame({
        "snowfall_event_id": ["SNOW-20100210"] * 2, "plow_zone": ["A", "B"],
        "predicted_count": [10.0, 20.0],
    })
    assert cli.check_replay(outlook, f5, M1_CONFIG) == 2

    with pytest.raises(cli.OutlookRunError, match="diverges"):
        cli.check_replay(outlook, f5.assign(predicted_count=[10.0, 21.0]), M1_CONFIG)
    with pytest.raises(cli.OutlookRunError, match="matched"):
        cli.check_replay(outlook, f5.assign(snowfall_event_id="SNOW-19990101"), M1_CONFIG)


def test_the_event_attributes_are_the_configured_event_features():
    assert list(ol.EVENT_ATTRIBUTES) == M1_CONFIG["features"]["event"]


def test_skip_existing_returns_before_reading_any_input(tmp_path, no_env, monkeypatch):
    """A rerun of a recorded day is a no-op: no input is read, nothing is written."""
    monkeypatch.setattr(cli, "run_exists", lambda prefix, bucket: True)
    missing = tmp_path / "does-not-exist"

    code = cli.main([
        "--issue-date", "2011-12-01", "--model-version", "m1-test",
        "--panel-file", str(missing), "--metrics-file", str(missing),
        "--archive-file", str(missing), "--forecast-file", str(missing),
        "--out-dir", str(tmp_path / "out"), "--bucket", "uoip",
        "--upload", "--skip-existing",
    ])

    assert code == 0
    assert not (tmp_path / "out").exists()


def test_skip_existing_without_upload_is_refused(tmp_path, no_env):
    issue = date(2011, 12, 1)
    paths = _write_inputs(tmp_path, issue, snow_per_hour=0.0)

    assert cli.main([*_argv(paths, issue, tmp_path / "out"), "--skip-existing"]) == 1
