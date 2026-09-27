"""Orchestration tests for the H2-R1/R2 rolling evaluation command."""

from __future__ import annotations

import datetime as dt
import json

import pytest

pd = pytest.importorskip("pandas", reason="needs the `ml` extra — run `make test-ml`")
np = pytest.importorskip("numpy", reason="needs the `ml` extra — run `make test-ml`")
pytest.importorskip("statsmodels", reason="needs the `ml` extra — run `make test-ml`")
pytest.importorskip("sklearn", reason="needs the `ml` extra — run `make test-ml`")

from models.request_forecast import features as feat  # noqa: E402
from models.request_forecast import model as mdl  # noqa: E402
from scripts.models import evaluate_m1_rolling as cli  # noqa: E402
from scripts.models import train_m1  # noqa: E402


def _config(n_units: int = 30, n_seasons: int = 7, events_per_season: int = 6) -> dict:
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
            "expected_training_cells": n_units * n_seasons * events_per_season,
            "expected_prediction_cells": n_units * 4 * events_per_season,
        },
        "features": {
            "event": ["total_snowfall_cm", "min_temperature_c"],
            "calendar": [],
            "lag": ["prev_target", "expanding_mean"],
        },
        "target": {"family": "poisson"},
        "random_seed": 20260820,
    }


def _raw_panel(
    n_units: int = 30, n_seasons: int = 7, events_per_season: int = 6
) -> pd.DataFrame:
    rng = np.random.default_rng(17)
    rows = []
    for season in range(n_seasons):
        for event in range(events_per_season):
            snow = 2.0 + 1.5 * season + 0.7 * event
            temperature = -3.0 - season - 0.5 * event
            event_number = season * events_per_season + event
            for unit in range(n_units):
                size = 700.0 + 50.0 * unit
                mean = size * np.exp(-5.0 + 0.08 * snow + 0.02 * season)
                rows.append(
                    {
                        "plow_zone": f"Z{unit:02d}",
                        "snowfall_event_id": f"E{event_number:03d}",
                        "address_count": size,
                        "start_date": dt.date(2010 + season, 12, 1 + event),
                        "snow_season": f"{2010 + season}-{2011 + season}",
                        "is_scheduling_era": season >= 3,
                        "request_count": int(
                            rng.negative_binomial(2.0, 2.0 / (2.0 + mean))
                        ),
                        "total_snowfall_cm": snow,
                        "min_temperature_c": temperature,
                        "severity_score": event_number
                        / (n_seasons * events_per_season - 1),
                    }
                )
    return pd.DataFrame(rows)


def _write_reference(config: dict, raw: pd.DataFrame, path) -> None:
    data = train_m1.prepare_training_data(config, raw)
    split = data.split
    x_train, y_train, offset_train = feat.build_design_matrix(
        split.train, data.feature_names
    )
    fit = mdl.fit_poisson_glm(x_train, y_train, offset_train)
    x_test, _y_test, offset_test = feat.build_design_matrix(split.test, data.feature_names)
    pd.DataFrame(
        {
            "snowfall_event_id": split.test["event_id"],
            "plow_zone": split.test["unit_id"],
            "predicted_count": mdl.predict(fit, x_test, offset_test),
            "baseline_count": mdl.seasonal_naive(split.test),
            "actual_count": split.test["target"],
        }
    ).to_csv(path, index=False)


def test_complete_run_writes_four_candidates_and_one_oos_row_per_cell(
    tmp_path, monkeypatch
) -> None:
    config, raw = _config(), _raw_panel()
    reference = tmp_path / "reference.csv"
    _write_reference(config, raw, reference)
    monkeypatch.setattr(
        cli,
        "EXPECTED_SEASONS",
        tuple(f"{year}-{year + 1}" for year in range(2013, 2017)),
    )
    monkeypatch.setattr(cli, "EXPECTED_HOLDOUT_ROWS", 720)
    monkeypatch.setattr(cli, "DEVELOPMENT_FOLDS", 3)
    monkeypatch.setattr(cli, "STABILITY_WINS", 3)

    metrics, candidates, summary, behavior_report = cli.evaluate_rolling(
        config, raw, reference
    )
    paths = cli.write_artefacts(
        metrics, candidates, summary, behavior_report, tmp_path / "out"
    )

    assert len(metrics) == 4 * 4
    assert set(metrics["candidate"]) == set(cli.CANDIDATES)
    assert len(candidates) == 720 * 4
    assert summary["folds_completed"] == 4
    assert summary["failed_fits"] == 0
    assert summary["holdout_cells"] == 720
    assert summary["reproduction_gate"]["max_prediction_difference"] < 1.0e-9
    assert set(behavior_report) == {"negative_binomial", "gbm"}
    assert {path.name for path in paths} == {
        cli.FOLD_METRICS_FILE,
        cli.CANDIDATE_PREDICTIONS_FILE,
        cli.ROLLING_PREDICTIONS_FILE,
        cli.SUMMARY_FILE,
        cli.BEHAVIOR_FILE,
    }
    rolling_rows = pd.read_csv(tmp_path / "out" / cli.ROLLING_PREDICTIONS_FILE)
    assert len(rolling_rows) == 720
    assert not rolling_rows.duplicated(["snowfall_event_id", "plow_zone"]).any()
    assert set(rolling_rows["fit_role"]) == {"rolling_holdout"}
    assert json.loads((tmp_path / "out" / cli.SUMMARY_FILE).read_text())[
        "failed_fits"
    ] == 0
