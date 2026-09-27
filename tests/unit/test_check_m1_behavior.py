"""Boundary and artefact tests for the H2-R3 entry point."""

from __future__ import annotations

import datetime as dt
import json

import pytest

pd = pytest.importorskip("pandas", reason="needs the `ml` extra — run `make test-ml`")
pytest.importorskip("numpy", reason="needs the `ml` extra — run `make test-ml`")

from scripts.models import check_m1_behavior as cli  # noqa: E402
from scripts.models import train_m1  # noqa: E402


def _model_config(n_units: int = 2, n_events: int = 6) -> dict:
    return {
        "model_version_prefix": "m1-poisson",
        "panel": {
            "columns": {
                "unit_id": "plow_zone",
                "event_id": "snowfall_event_id",
                "unit_size": "address_count",
                "event_start": "start_date",
                "season": "snow_season",
                "target": "request_count",
            },
            "expected_training_cells": n_units * n_events,
        },
        "target": {"family": "poisson"},
        "features": {
            "event": ["total_snowfall_cm", "min_temperature_c"],
            "calendar": ["season_index"],
            "lag": ["prev_target", "expanding_mean"],
        },
    }


def _raw_panel(n_units: int = 2, n_events: int = 6) -> pd.DataFrame:
    rows = []
    for event in range(n_events):
        for unit in range(n_units):
            rows.append(
                {
                    "plow_zone": f"Z{unit}",
                    "snowfall_event_id": f"E{event:02d}",
                    "address_count": 1000 + unit * 500,
                    "start_date": dt.date(2010 + event, 12, 1),
                    "snow_season": f"{2010 + event}-{2011 + event}",
                    "request_count": 5 + event + unit,
                    "total_snowfall_cm": 4.0 + event,
                    "min_temperature_c": -5.0 - event,
                }
            )
    return pd.DataFrame(rows)


def _metrics(config: dict, raw: pd.DataFrame, *, snow: float = 0.05) -> tuple[str, dict]:
    panel = train_m1.to_role_names(raw, config)
    fingerprint = train_m1.panel_fingerprint(config, panel)
    version = f"m1-poisson-20260927-{fingerprint}"
    return version, {
        "model_version": version,
        "panel_fingerprint": fingerprint,
        "coefficients": {
            "const": -5.0,
            "total_snowfall_cm": snow,
            "min_temperature_c": -0.02,
            "season_index": 0.01,
            "prev_target": 0.001,
            "expanding_mean": 0.001,
        },
    }


def test_report_is_attached_to_an_explicit_matching_model() -> None:
    config, raw = _model_config(), _raw_panel()
    version, metrics = _metrics(config, raw)
    report = cli.build_behavior_report(config, cli.load_behavior_config(), raw, metrics, version)

    assert report["model_version"] == version
    assert report["panel_fingerprint"] == metrics["panel_fingerprint"]
    assert report["anchor_scope"] == "training_split"
    # First event has no lag; last season is held out: 4 events x 2 units.
    assert report["anchor_rows"] == 8
    assert len(report["monotonicity"]) == 2
    assert report["synthetic_inputs_are_accuracy_evidence"] is False


def test_behavioral_findings_do_not_turn_the_report_into_an_error() -> None:
    config, raw = _model_config(), _raw_panel()
    version, metrics = _metrics(config, raw, snow=-0.2)
    report = cli.build_behavior_report(config, cli.load_behavior_config(), raw, metrics, version)

    assert report["status"] == "findings"
    assert any("total_snowfall_cm monotonicity" in item for item in report["findings"])


def test_another_model_version_or_panel_is_refused_before_checks() -> None:
    config, raw = _model_config(), _raw_panel()
    version, metrics = _metrics(config, raw)
    with pytest.raises(cli.BehaviorRunError, match="metrics are for"):
        cli.build_behavior_report(
            config, cli.load_behavior_config(), raw, metrics, "m1-poisson-other"
        )

    changed = raw.copy()
    changed.loc[0, "request_count"] += 1
    with pytest.raises(cli.BehaviorRunError, match="fingerprint"):
        cli.build_behavior_report(config, cli.load_behavior_config(), changed, metrics, version)


def test_registered_parameters_cannot_drift(tmp_path) -> None:
    path = tmp_path / "behavior.yaml"
    text = cli.CONFIG_PATH.read_text().replace("explosion_ratio: 100.0", "explosion_ratio: 10.0")
    path.write_text(text)

    with pytest.raises(cli.BehaviorRunError, match="cannot drift"):
        cli.load_behavior_config(path)


def test_writer_is_byte_stable_and_uses_the_model_directory(tmp_path) -> None:
    config, raw = _model_config(), _raw_panel()
    version, metrics = _metrics(config, raw)
    report = cli.build_behavior_report(config, cli.load_behavior_config(), raw, metrics, version)

    path = cli.write_behavior_report(report, tmp_path)
    first = path.read_bytes()
    same = cli.write_behavior_report(report, tmp_path)

    assert path == tmp_path / version / cli.BEHAVIOR_FILE
    assert same.read_bytes() == first
    assert json.loads(first)["model_version"] == version


def test_cli_writes_a_finding_and_still_succeeds(tmp_path, monkeypatch) -> None:
    import yaml

    config, raw = _model_config(), _raw_panel()
    version, metrics = _metrics(config, raw, snow=-0.2)
    config_path = tmp_path / "m1.yaml"
    panel_path = tmp_path / "panel.csv"
    metrics_path = tmp_path / "metrics.json"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    raw.to_csv(panel_path, index=False)
    metrics_path.write_text(json.dumps(metrics))
    monkeypatch.setattr(cli, "load_cli_env", lambda: None)

    assert (
        cli.main(
            [
                "--model-version",
                version,
                "--config",
                str(config_path),
                "--panel-file",
                str(panel_path),
                "--metrics-file",
                str(metrics_path),
                "--out-dir",
                str(tmp_path / "out"),
            ]
        )
        == 0
    )
    report = json.loads((tmp_path / "out" / version / cli.BEHAVIOR_FILE).read_text())
    assert report["status"] == "findings"
