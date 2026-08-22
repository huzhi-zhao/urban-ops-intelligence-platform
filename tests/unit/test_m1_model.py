"""Unit tests for M1's fit / split / evaluate layer.

Skipped unless the `ml` extra is installed — see `make test-ml`.
"""

from __future__ import annotations

import datetime as dt

import pytest

pd = pytest.importorskip("pandas", reason="needs the `ml` extra — run `make test-ml`")
pytest.importorskip("statsmodels", reason="needs the `ml` extra — run `make test-ml`")

import numpy as np  # noqa: E402

from models.request_forecast import features as feat  # noqa: E402
from models.request_forecast import model as mdl  # noqa: E402


def make_panel(n_units: int = 4, n_seasons: int = 6, seed: int = 0) -> pd.DataFrame:
    """A panel with a real signal in it, so a fitted model can beat a constant.

    Counts scale with the unit's size and with snowfall, which is the structure
    the GLM's offset and event features are meant to pick up.
    """
    rng = np.random.default_rng(seed)
    rows = []
    for u in range(n_units):
        size = 500 * (u + 1)
        for s in range(n_seasons):
            snowfall = 5.0 + 2.0 * ((s + u) % 4)
            rate = 0.01 * snowfall
            rows.append(
                {
                    "unit_id": f"Z{u}",
                    "event_id": f"E{s}",
                    "event_start": dt.date(2015 + s, 12, 1),
                    "season": f"{2015 + s}-{2016 + s}",
                    "unit_size": size,
                    "target": int(rng.poisson(size * rate)),
                    "total_snowfall_cm": snowfall,
                    "peak_daily_snowfall_cm": snowfall * 0.6,
                    "duration_days": 1,
                    "min_temperature_c": -10.0,
                    "accum_flag": s % 2 == 0,
                    "severity_score": 0.1 * s,
                }
            )
    return pd.DataFrame(rows)


NAMES = ["total_snowfall_cm", "prev_target", "expanding_mean"]


def prepared(panel: pd.DataFrame) -> pd.DataFrame:
    return feat.drop_rows_without_history(feat.build_panel_features(panel))


class TestSplit:
    def test_holds_out_the_most_recent_season(self):
        split = mdl.split_holdout_last_season(prepared(make_panel()))
        assert split.holdout_season == "2020-2021"
        assert (split.test["season"] == "2020-2021").all()
        assert (split.train["season"] != "2020-2021").all()

    def test_refuses_a_panel_with_one_season(self):
        panel = prepared(make_panel(n_seasons=6))
        one = panel[panel["season"] == panel["season"].min()]
        with pytest.raises(feat.PanelError, match="at least 2"):
            mdl.split_holdout_last_season(one)

    def test_split_is_time_ordered_not_merely_disjoint(self):
        split = mdl.split_holdout_last_season(prepared(make_panel()))
        mdl.assert_no_history_leak(split.train, split.test)

    def test_a_random_split_is_caught(self):
        # The point of assert_no_history_leak: a shuffled split shares no rows
        # yet still leaks through the lag features, so "disjoint" is not enough.
        panel = prepared(make_panel())
        shuffled = panel.sample(frac=1.0, random_state=3).reset_index(drop=True)
        train, test = shuffled.iloc[:15], shuffled.iloc[15:]
        with pytest.raises(feat.PanelError, match="not time-ordered"):
            mdl.assert_no_history_leak(train, test)


class TestBaseline:
    def test_is_the_expanding_mean(self):
        panel = prepared(make_panel())
        assert mdl.seasonal_naive(panel).equals(panel["expanding_mean"].astype("float64"))

    def test_requires_the_feature_to_have_been_built(self):
        with pytest.raises(feat.PanelError, match="expanding_mean"):
            mdl.seasonal_naive(make_panel())


class TestMetrics:
    def test_mae_is_zero_on_a_perfect_prediction(self):
        actual = pd.Series([1.0, 2.0, 3.0])
        assert mdl.mean_absolute_error(actual, actual) == pytest.approx(0.0)

    def test_poisson_deviance_is_zero_on_a_perfect_prediction(self):
        actual = pd.Series([1.0, 2.0, 3.0])
        assert mdl.poisson_deviance(actual, actual) == pytest.approx(0.0, abs=1e-9)

    def test_poisson_deviance_survives_zero_counts(self):
        # ~30% of the real panel's scheduling-era cells are exactly zero, so a
        # NaN here would poison the headline metric rather than a corner of it.
        actual = pd.Series([0.0, 0.0, 5.0])
        predicted = pd.Series([1.0, 2.0, 4.0])
        value = mdl.poisson_deviance(actual, predicted)
        assert np.isfinite(value)

    def test_poisson_deviance_grows_as_the_prediction_worsens(self):
        actual = pd.Series([10.0, 10.0])
        near = mdl.poisson_deviance(actual, pd.Series([9.0, 11.0]))
        far = mdl.poisson_deviance(actual, pd.Series([2.0, 30.0]))
        assert far > near


class TestEvaluate:
    def _evaluation(self):
        panel = prepared(make_panel())
        split = mdl.split_holdout_last_season(panel)
        X_tr, y_tr, off_tr = feat.build_design_matrix(split.train, NAMES)
        X_te, y_te, off_te = feat.build_design_matrix(split.test, NAMES)
        results = mdl.fit_poisson_glm(X_tr, y_tr, off_tr)
        predicted = mdl.predict(results, X_te, off_te)
        return mdl.evaluate(y_te, predicted, mdl.seasonal_naive(split.test), split.holdout_season)

    def test_reports_model_and_baseline_on_the_same_rows(self):
        ev = self._evaluation()
        assert ev.model.n_rows == ev.baseline.n_rows > 0

    def test_summary_states_both_numbers_even_when_the_model_loses(self):
        ev = self._evaluation()
        text = ev.summary()
        assert "seasonal-naive" in text
        assert f"{ev.model.mae:.3f}" in text
        assert f"{ev.baseline.mae:.3f}" in text
        # a4: losing does not block the release, misreporting it does.
        assert ("beats baseline" in text) or ("does NOT beat baseline" in text)

    def test_drops_rows_the_baseline_cannot_see_from_both_sides(self):
        actual = pd.Series([1.0, 2.0, 3.0])
        model_pred = pd.Series([1.0, 2.0, 99.0])
        baseline_pred = pd.Series([1.0, 2.0, float("nan")])
        ev = mdl.evaluate(actual, model_pred, baseline_pred, "2025-2026")
        # The third row is dropped for both, so the model's wild miss is not
        # scored either — comparing two methods on two row sets is not a
        # comparison.
        assert ev.model.n_rows == 2
        assert ev.model.mae == pytest.approx(0.0)

    def test_refuses_when_the_baseline_is_undefined_everywhere(self):
        nan = pd.Series([float("nan")] * 3)
        with pytest.raises(feat.PanelError, match="no metric may be reported"):
            mdl.evaluate(pd.Series([1.0, 2.0, 3.0]), pd.Series([1.0, 2.0, 3.0]), nan, "2025-2026")


class TestFit:
    def test_predictions_are_non_negative(self):
        panel = prepared(make_panel())
        split = mdl.split_holdout_last_season(panel)
        X_tr, y_tr, off_tr = feat.build_design_matrix(split.train, NAMES)
        X_te, _, off_te = feat.build_design_matrix(split.test, NAMES)
        results = mdl.fit_poisson_glm(X_tr, y_tr, off_tr)
        assert (mdl.predict(results, X_te, off_te) >= 0).all()

    def test_refitting_the_same_data_gives_the_same_predictions(self):
        # Gate a6: rerunning one model_version must not move the artefact.
        panel = prepared(make_panel())
        split = mdl.split_holdout_last_season(panel)
        X_tr, y_tr, off_tr = feat.build_design_matrix(split.train, NAMES)
        X_te, _, off_te = feat.build_design_matrix(split.test, NAMES)
        first = mdl.predict(mdl.fit_poisson_glm(X_tr, y_tr, off_tr), X_te, off_te)
        second = mdl.predict(mdl.fit_poisson_glm(X_tr, y_tr, off_tr), X_te, off_te)
        pd.testing.assert_series_equal(first, second)

    def test_the_offset_makes_predictions_scale_with_unit_size(self):
        # The per-address-rate formulation: a unit twice the size, all else
        # equal, should predict roughly twice the count. This is the same fact
        # as BO-6's "the denominator is load-bearing".
        panel = prepared(make_panel())
        X, y, offset = feat.build_design_matrix(panel, ["total_snowfall_cm"])
        results = mdl.fit_poisson_glm(X, y, offset)
        one = mdl.predict(results, X.head(1), offset.head(1)).iloc[0]
        doubled = mdl.predict(results, X.head(1), offset.head(1) + np.log(2)).iloc[0]
        assert doubled == pytest.approx(2 * one, rel=1e-6)
