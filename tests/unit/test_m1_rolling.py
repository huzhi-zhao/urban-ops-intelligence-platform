"""Unit tests for the H2-R1/R2 rolling-origin comparison layer."""

from __future__ import annotations

import datetime as dt

import pytest

pd = pytest.importorskip("pandas", reason="needs the `ml` extra — run `make test-ml`")
np = pytest.importorskip("numpy", reason="needs the `ml` extra — run `make test-ml`")
pytest.importorskip("statsmodels", reason="needs the `ml` extra — run `make test-ml`")
pytest.importorskip("sklearn", reason="needs the `ml` extra — run `make test-ml`")

from models.request_forecast import features as feat  # noqa: E402
from models.request_forecast import model as mdl  # noqa: E402
from models.request_forecast import rolling  # noqa: E402


def _panel(n_units: int = 8, n_seasons: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(20260820)
    rows = []
    for season in range(n_seasons):
        snow = 3.0 + season
        for unit in range(n_units):
            size = 800.0 + 100.0 * unit
            mean = size * np.exp(-5.0 + 0.12 * snow)
            rows.append(
                {
                    "unit_id": f"Z{unit:02d}",
                    "event_id": f"E{season:02d}",
                    "event_start": dt.datetime(2010 + season, 12, 1),
                    "season": f"{2010 + season}-{2011 + season}",
                    "is_scheduling_era": season >= 3,
                    "unit_size": size,
                    "target": int(rng.negative_binomial(2.0, 2.0 / (2.0 + mean))),
                    "total_snowfall_cm": snow,
                    "peak_daily_snowfall_cm": snow * 0.6,
                    "duration_days": 1.0,
                    "min_temperature_c": -4.0 - season,
                    "accum_flag": season % 2 == 0,
                    "severity_score": season / max(1, n_seasons - 1),
                }
            )
    return feat.drop_rows_without_history(feat.build_panel_features(pd.DataFrame(rows)))


class TestRollingSplits:
    def test_one_expanding_fold_per_scheduling_season(self) -> None:
        panel = _panel()
        folds = rolling.rolling_origin_splits(panel)

        assert [fold.holdout_season for fold in folds] == [
            "2013-2014",
            "2014-2015",
            "2015-2016",
            "2016-2017",
        ]
        assert [fold.train["event_id"].nunique() for fold in folds] == [2, 3, 4, 5]
        assert sum(len(fold.test) for fold in folds) == 4 * 8

    def test_every_training_row_is_strictly_before_its_holdout(self) -> None:
        for fold in rolling.rolling_origin_splits(_panel()):
            assert fold.train["event_start"].max() < fold.test["event_start"].min()


def test_nb2_fit_returns_positive_mle_dispersion_and_interval() -> None:
    panel = _panel(n_units=30, n_seasons=10)
    names = ["total_snowfall_cm", "prev_target", "expanding_mean"]
    X, y, offset = feat.build_design_matrix(panel, names)
    fit = rolling.fit_negative_binomial(X, y, offset)

    assert fit.alpha > 0.0
    assert fit.alpha_ci_low < fit.alpha < fit.alpha_ci_high


def test_negative_binomial_interval_is_wider_than_poisson() -> None:
    mean = pd.Series([10.0, 50.0, 100.0])
    poisson_low, poisson_high = rolling.prediction_interval(
        mean, family="poisson", level=0.9
    )
    nb_low, nb_high = rolling.prediction_interval(
        mean, family="negative_binomial", level=0.9, alpha=1.0
    )

    assert ((nb_high - nb_low) > (poisson_high - poisson_low)).all()


def test_severity_uses_only_training_bounds() -> None:
    training = pd.DataFrame(
        {
            "total_snowfall_cm": [0.0, 10.0],
            "min_temperature_c": [0.0, -20.0],
        }
    )
    test = pd.DataFrame(
        {
            "total_snowfall_cm": [20.0],
            "min_temperature_c": [-40.0],
        }
    )
    severity = rolling.severity_from_training_bounds(training, test)

    assert severity.iloc[0] == pytest.approx(2.0)


def test_glm_predictions_are_invariant_to_training_side_severity_rescaling() -> None:
    panel = _panel(n_units=30, n_seasons=10)
    fold = rolling.rolling_origin_splits(panel)[-1]
    names = [
        "total_snowfall_cm",
        "min_temperature_c",
        "severity_score",
        "prev_target",
        "expanding_mean",
    ]
    X_train, y_train, offset_train = feat.build_design_matrix(fold.train, names)
    X_test, _y_test, offset_test = feat.build_design_matrix(fold.test, names)
    original = mdl.predict(
        mdl.fit_poisson_glm(X_train, y_train, offset_train), X_test, offset_test
    )

    changed_train = rolling.with_training_severity(fold.train, fold.train)
    changed_test = rolling.with_training_severity(fold.train, fold.test)
    X_changed, y_changed, offset_changed = feat.build_design_matrix(changed_train, names)
    X_changed_test, _y, offset_changed_test = feat.build_design_matrix(changed_test, names)
    changed = mdl.predict(
        mdl.fit_poisson_glm(X_changed, y_changed, offset_changed),
        X_changed_test,
        offset_changed_test,
    )

    assert np.max(np.abs(original - changed)) < 1.0e-9


def test_poisson_pearson_dispersion_detects_overdispersion() -> None:
    actual = pd.Series([0.0, 20.0, 0.0, 20.0])
    predicted = pd.Series([10.0] * 4)
    assert rolling.poisson_pearson_chi2_df(actual, predicted, 1) > 1.0


def test_gbm_predictions_are_deterministic_nonnegative_counts() -> None:
    panel = _panel(n_units=30, n_seasons=8)
    names = ["total_snowfall_cm", "min_temperature_c", "prev_target"]
    first = rolling.fit_gbm(panel, names, random_seed=20260820)
    second = rolling.fit_gbm(panel, names, random_seed=20260820)

    first_prediction = rolling.predict_gbm(first, panel)
    second_prediction = rolling.predict_gbm(second, panel)
    pd.testing.assert_series_equal(first_prediction, second_prediction)
    assert (first_prediction >= 0.0).all()
