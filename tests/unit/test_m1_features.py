"""Unit tests for M1's feature layer.

Skipped unless the `ml` extra is installed — see `make test-ml`, which runs
them in their own environment, and CI's `ml` job, which runs them for real so a
skip never passes for a pass (the O15 lesson).
"""

from __future__ import annotations

import datetime as dt

import pytest

pd = pytest.importorskip("pandas", reason="needs the `ml` extra — run `make test-ml`")

from models.request_forecast import features as feat  # noqa: E402


def make_panel(n_units: int = 2, n_events: int = 4) -> pd.DataFrame:
    """A minimal well-formed panel: `n_units` units observed over `n_events`."""
    rows = []
    for u in range(n_units):
        for e in range(n_events):
            rows.append(
                {
                    "unit_id": f"Z{u}",
                    "event_id": f"E{e}",
                    "event_start": dt.date(2015 + e, 12, 1),
                    "season": f"{2015 + e}-{2016 + e}",
                    "unit_size": 1000 * (u + 1),
                    "target": 10 * (e + 1) + u,
                    "total_snowfall_cm": 5.0 + e,
                    "peak_daily_snowfall_cm": 3.0 + e,
                    "duration_days": 1,
                    "min_temperature_c": -10.0 - e,
                    "accum_flag": e % 2 == 0,
                    "severity_score": 0.1 * e,
                }
            )
    return pd.DataFrame(rows)


CONFIG = {
    "features": {
        "event": ["total_snowfall_cm", "peak_daily_snowfall_cm"],
        "calendar": ["season_index", "month"],
        "lag": ["prev_target", "expanding_mean"],
    }
}


class TestValidatePanel:
    def test_accepts_a_well_formed_panel(self):
        feat.validate_panel(make_panel())

    def test_rejects_a_missing_column(self):
        panel = make_panel().drop(columns=["unit_size"])
        with pytest.raises(feat.PanelError, match="unit_size"):
            feat.validate_panel(panel)

    def test_rejects_a_duplicated_unit_event_pair(self):
        panel = pd.concat([make_panel(), make_panel().head(1)], ignore_index=True)
        with pytest.raises(feat.PanelError, match="duplicated"):
            feat.validate_panel(panel)

    @pytest.mark.parametrize("bad", [0, -1, None])
    def test_rejects_a_non_positive_unit_size(self, bad):
        # log(unit_size) is the GLM offset, so a zero would surface as -inf
        # inside statsmodels rather than as a data problem.
        panel = make_panel()
        panel.loc[0, "unit_size"] = bad
        with pytest.raises(feat.PanelError, match="unit_size"):
            feat.validate_panel(panel)


class TestLagFeatures:
    def test_first_event_of_each_unit_has_no_history(self):
        out = feat.build_panel_features(make_panel())
        first = out.groupby("unit_id").head(1)
        assert first["prev_target"].isna().all()
        assert first["expanding_mean"].isna().all()

    def test_prev_target_is_the_previous_event_not_the_current_one(self):
        out = feat.build_panel_features(make_panel(n_units=1, n_events=4))
        # targets are 10, 20, 30, 40 in event order
        assert pd.isna(out.loc[0, "prev_target"])
        assert out["prev_target"].tolist()[1:] == [10.0, 20.0, 30.0]

    def test_expanding_mean_uses_only_strictly_prior_events(self):
        out = feat.build_panel_features(make_panel(n_units=1, n_events=4))
        # After 10, 20, 30: means of prior events are nan, 10, 15, 20.
        assert out["expanding_mean"].tolist()[1:] == [10.0, 15.0, 20.0]

    def test_lag_features_do_not_mix_units(self):
        out = feat.build_panel_features(make_panel(n_units=2, n_events=3))
        z1 = out[out["unit_id"] == "Z1"].reset_index(drop=True)
        # Z1's first row must be NaN even though Z0's rows sort before it —
        # a groupby that leaked would carry Z0's last value across.
        assert pd.isna(z1.loc[0, "prev_target"])

    def test_row_order_of_the_input_does_not_change_the_features(self):
        panel = make_panel()
        shuffled = panel.sample(frac=1.0, random_state=1).reset_index(drop=True)
        a = feat.build_panel_features(panel)
        b = feat.build_panel_features(shuffled)
        pd.testing.assert_frame_equal(a, b)


class TestHistoryFiltering:
    def test_drop_rows_without_history_removes_one_row_per_unit(self):
        out = feat.build_panel_features(make_panel(n_units=3, n_events=5))
        kept = feat.drop_rows_without_history(out)
        assert len(kept) == len(out) - 3

    def test_assert_prediction_panel_has_history_raises_on_a_gap(self):
        out = feat.build_panel_features(make_panel())
        with pytest.raises(feat.PanelError, match="no lag history"):
            feat.assert_prediction_panel_has_history(out)

    def test_assert_prediction_panel_has_history_passes_once_filtered(self):
        out = feat.drop_rows_without_history(feat.build_panel_features(make_panel()))
        feat.assert_prediction_panel_has_history(out)


class TestFeatureNames:
    def test_flattens_the_groups_in_config_order(self):
        assert feat.feature_names(CONFIG) == [
            "total_snowfall_cm",
            "peak_daily_snowfall_cm",
            "season_index",
            "month",
            "prev_target",
            "expanding_mean",
        ]

    def test_rejects_a_duplicate(self):
        config = {"features": {"event": ["a", "a"], "calendar": [], "lag": []}}
        with pytest.raises(feat.PanelError, match="more than once"):
            feat.feature_names(config)


class TestForbiddenFeatures:
    """§4.2's red line: schedule order is BO-6's independent second term."""

    @pytest.mark.parametrize("banned", ["shift_number", "rank_factor"])
    def test_config_listing_a_forbidden_feature_is_refused(self, banned):
        config = {"features": {"event": [banned], "calendar": [], "lag": []}}
        with pytest.raises(feat.PanelError, match="BO-6"):
            feat.feature_names(config)

    @pytest.mark.parametrize("banned", ["shift_number", "rank_factor"])
    def test_design_matrix_refuses_a_forbidden_column(self, banned):
        panel = feat.drop_rows_without_history(feat.build_panel_features(make_panel()))
        panel[banned] = 1.0
        with pytest.raises(feat.PanelError, match="refusing"):
            feat.build_design_matrix(panel, [banned])

    def test_the_built_matrix_never_carries_them(self):
        panel = feat.drop_rows_without_history(feat.build_panel_features(make_panel()))
        X, _, _ = feat.build_design_matrix(panel, feat.feature_names(CONFIG))
        assert not set(X.columns) & feat.FORBIDDEN_FEATURES


class TestDesignMatrix:
    def test_carries_an_intercept_first(self):
        panel = feat.drop_rows_without_history(feat.build_panel_features(make_panel()))
        X, _, _ = feat.build_design_matrix(panel, feat.feature_names(CONFIG))
        assert list(X.columns)[0] == "const"
        assert (X["const"] == 1.0).all()

    def test_offset_is_log_unit_size_not_a_division(self):
        import numpy as np

        panel = feat.drop_rows_without_history(feat.build_panel_features(make_panel()))
        _, y, offset = feat.build_design_matrix(panel, feat.feature_names(CONFIG))
        assert np.allclose(offset, np.log(panel["unit_size"].astype(float)))
        # The target stays a count, so MAE reads in real tickets (§4.2).
        assert (y == panel["target"].astype(float)).all()

    def test_boolean_features_become_floats(self):
        panel = feat.drop_rows_without_history(feat.build_panel_features(make_panel()))
        X, _, _ = feat.build_design_matrix(panel, ["accum_flag"])
        assert X["accum_flag"].dtype == "float64"

    def test_missing_feature_column_raises(self):
        panel = feat.drop_rows_without_history(feat.build_panel_features(make_panel()))
        with pytest.raises(feat.PanelError, match="missing feature column"):
            feat.build_design_matrix(panel, ["not_a_column"])
