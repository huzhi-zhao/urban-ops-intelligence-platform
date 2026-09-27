"""Unit tests for H2-R13's city-agnostic event-cluster bootstrap."""

from __future__ import annotations

import datetime as dt

import pytest

pd = pytest.importorskip("pandas", reason="needs the `ml` extra — run `make test-ml`")
pytest.importorskip("statsmodels", reason="needs the `ml` extra — run `make test-ml`")

import numpy as np  # noqa: E402

from models.request_forecast import bootstrap as boot  # noqa: E402
from models.request_forecast import features as feat  # noqa: E402
from models.request_forecast import model as mdl  # noqa: E402

NAMES = ["total_snowfall_cm", "prev_target", "expanding_mean"]


def _prepared(n_units: int = 4, n_events: int = 8):
    rng = np.random.default_rng(7)
    rows = []
    for unit in range(n_units):
        for event in range(n_events):
            snowfall = 4.0 + event
            rows.append(
                {
                    "unit_id": f"Z{unit}",
                    "event_id": f"E{event:02d}",
                    "event_start": dt.date(2010 + event, 12, 1),
                    "season": f"{2010 + event}-{2011 + event}",
                    "unit_size": 800 + 200 * unit,
                    "target": int(rng.poisson((800 + 200 * unit) * snowfall / 1000)),
                    "total_snowfall_cm": snowfall,
                    "is_scheduling_era": event >= n_events - 4,
                }
            )
    prepared = feat.drop_rows_without_history(feat.build_panel_features(pd.DataFrame(rows)))
    split = mdl.split_holdout_last_season(prepared)
    prediction = prepared[prepared["is_scheduling_era"]].reset_index(drop=True)
    return split.train, split.test, prediction


def test_event_sampler_keeps_each_drawn_cluster_whole() -> None:
    training, _holdout, _prediction = _prepared()
    sampled = boot.sample_event_clusters(training, child_seed=123)
    assert len(sampled) == len(training)
    expected_units = set(training["unit_id"])
    for _event_id, group in sampled.groupby("event_id"):
        # Repeated draws repeat all four rows; they never create a partial event.
        assert len(group) % len(expected_units) == 0
        assert set(group["unit_id"]) == expected_units


def test_incomplete_event_cluster_is_refused() -> None:
    training, _holdout, _prediction = _prepared()
    broken = training.drop(training.index[0]).reset_index(drop=True)
    with pytest.raises(boot.BootstrapError, match="not a complete cluster"):
        boot.validate_event_clusters(broken)


def test_replicate_seeds_are_reproducible_and_one_per_replica() -> None:
    assert boot.replicate_seeds(17, 3) == boot.replicate_seeds(17, 3)
    assert len(set(boot.replicate_seeds(17, 3))) == 3
    with pytest.raises(boot.BootstrapError, match="positive"):
        boot.replicate_seeds(17, 0)


def test_bootstrap_predicts_every_real_cell_for_every_replica() -> None:
    training, _holdout, prediction = _prepared()
    run = boot.fit_event_cluster_bootstrap(training, prediction, NAMES, 3, 99)
    assert list(run.predictions.columns) == list(boot.PREDICTION_COLUMNS)
    assert len(run.predictions) == 3 * len(prediction)
    assert not run.predictions.duplicated(
        ["replicate_id", "event_id", "unit_id"]
    ).any()
    assert set(run.predictions["replicate_id"]) == {1, 2, 3}
    assert (run.predictions["predicted_mean"] >= 0).all()
    assert run.training_cluster_count == training["event_id"].nunique()
    assert run.prediction_cell_count == len(prediction)
    assert set(run.coefficients["term"]) == {"const", *NAMES}


def test_same_seed_gives_the_same_replica_predictions() -> None:
    training, _holdout, prediction = _prepared()
    first = boot.fit_event_cluster_bootstrap(training, prediction, NAMES, 2, 55)
    second = boot.fit_event_cluster_bootstrap(training, prediction, NAMES, 2, 55)
    pd.testing.assert_frame_equal(first.predictions, second.predictions)
    pd.testing.assert_frame_equal(first.coefficients, second.coefficients)


def test_a_failed_replica_stops_the_run_instead_of_being_skipped() -> None:
    training, _holdout, prediction = _prepared()

    def fail_fit(_X, _y, _offset):
        raise RuntimeError("deliberate")

    with pytest.raises(boot.BootstrapError, match="replicate 1.*no replicas were skipped"):
        boot.fit_event_cluster_bootstrap(
            training, prediction, NAMES, 3, 99, fit_fn=fail_fit
        )


def test_holdout_events_are_not_in_the_training_universe() -> None:
    training, holdout, _prediction = _prepared()
    assert set(training["event_id"]).isdisjoint(set(holdout["event_id"]))
    sampled = boot.sample_event_clusters(training, child_seed=5)
    assert set(sampled["event_id"]).isdisjoint(set(holdout["event_id"]))
