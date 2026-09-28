"""R6 derivation tests over H2-R13 raw bootstrap replicas."""

from __future__ import annotations

import pytest

pd = pytest.importorskip("pandas", reason="needs the `ml` extra — run `make test-ml`")
np = pytest.importorskip("numpy", reason="needs the `ml` extra — run `make test-ml`")

from scripts.presentation import demand_uncertainty as du  # noqa: E402

VERSION = "m1-poisson-test"
COLUMNS = [
    "model_version",
    "snowfall_event_id",
    "fit_role",
    "plow_zone",
    "address_count",
    "predicted_count",
    "actual_count",
    "shift_number",
]


def _panel() -> dict:
    rows = []
    for event, role in (("E0", "in_sample"), ("E1", "holdout")):
        for zone, point, shift in (("Z0", 12.0, 3), ("Z1", 8.0, 2), ("Z2", 4.0, 1)):
            rows.append([VERSION, event, role, zone, 1000, point, int(point), shift])
    return {"fig_id": "FIG-BO8-03", "columns": COLUMNS, "rows": rows}


def _replicas(count: int = 20):
    rows = []
    for replicate in range(1, count + 1):
        jitter = (replicate % 3 - 1) * 0.2
        for event in ("E0", "E1"):
            rows.extend(
                [
                    [replicate, event, "Z0", 12.0 + jitter],
                    [replicate, event, "Z1", 8.0 - jitter],
                    [replicate, event, "Z2", 4.0],
                ]
            )
    return pd.DataFrame(
        rows,
        columns=["replicate_id", "snowfall_event_id", "plow_zone", "predicted_mean"],
    )


def _metadata(count: int = 20) -> dict:
    return {
        "model_version": VERSION,
        "model_family": "poisson",
        "replicate_count_requested": count,
        "replicate_count_completed": count,
        "random_seed": 27,
        "cluster_unit": "event",
        "features_fixed": True,
        "resample_scope": "training_split",
        "panel_fingerprint": "fp-test",
        "prediction_cell_count": 6,
        "prediction_columns": [
            "replicate_id",
            "snowfall_event_id",
            "plow_zone",
            "predicted_mean",
        ],
    }


def _config() -> dict:
    return {
        "noise": {"family": "poisson"},
        "interval": {"level": 0.9, "predictive_seed": 28},
        "ranking": {
            "top_k_values": [1, 2, 3],
            "tie_breaker": "plow_zone_ascending",
        },
        "review_prompt": {
            "top_k": 2,
            "minimum_shift": 2,
            "minimum_stability": 0.8,
        },
        "sensitivity": {
            "top_k_values": [1, 2, 3],
            "minimum_shifts": [2, 3],
            "minimum_stabilities": [0.7, 0.8, 0.9],
        },
    }


def test_summary_contains_intervals_rank_stability_and_all_18_grid_cells() -> None:
    summary = du.build_uncertainty_summary(
        _panel(), _replicas(), _metadata(), _config(), VERSION
    )
    assert len(summary["cells"]) == 6
    assert len(summary["sensitivity_grid"]) == 18
    assert summary["interval"] == {
        "kind": "request_count_prediction",
        "level": 0.9,
        "lower_quantile": pytest.approx(0.05),
        "upper_quantile": pytest.approx(0.95),
    }
    z0 = next(
        cell
        for cell in summary["cells"]
        if cell["snowfall_event_id"] == "E1" and cell["plow_zone"] == "Z0"
    )
    assert z0["top_k_probabilities"]["1"] == pytest.approx(1.0)
    assert z0["prompt"] is True
    assert z0["prediction_low"] <= z0["prediction_high"]


def test_in_sample_cells_never_receive_a_prompt() -> None:
    summary = du.build_uncertainty_summary(
        _panel(), _replicas(), _metadata(), _config(), VERSION
    )
    assert summary["review_prompt"]["in_sample_prompt_count"] == 0
    assert not any(
        cell["prompt"] for cell in summary["cells"] if cell["snowfall_event_id"] == "E0"
    )


def test_point_estimate_is_not_replaced_by_a_bootstrap_statistic() -> None:
    summary = du.build_uncertainty_summary(
        _panel(), _replicas(), _metadata(), _config(), VERSION
    )
    assert all("point" not in cell for cell in summary["cells"])


def test_every_replica_must_cover_the_same_real_panel() -> None:
    replicas = _replicas()
    replicas = replicas.drop(replicas.index[-1]).reset_index(drop=True)
    with pytest.raises(du.UncertaintyError, match="does not cover"):
        du.build_uncertainty_summary(_panel(), replicas, _metadata(), _config(), VERSION)


def test_consumer_refuses_metadata_from_a_row_bootstrap() -> None:
    metadata = _metadata()
    metadata["cluster_unit"] = "row"
    with pytest.raises(du.UncertaintyError, match="violates R13 invariants"):
        du.build_uncertainty_summary(
            _panel(), _replicas(), metadata, _config(), VERSION
        )


def test_consumer_refuses_a_non_finite_prediction() -> None:
    replicas = _replicas()
    replicas.loc[0, "predicted_mean"] = np.nan
    with pytest.raises(du.UncertaintyError, match="non-finite"):
        du.build_uncertainty_summary(
            _panel(), replicas, _metadata(), _config(), VERSION
        )


def test_negative_binomial_requires_its_dispersion_parameter() -> None:
    with pytest.raises(du.UncertaintyError, match="dispersion_alpha"):
        du._predictive_draws(
            np.array([2.0, 3.0]), {"family": "negative_binomial"}, seed=1
        )


def test_predictive_sampling_is_reproducible() -> None:
    means = np.array([2.0, 3.0, 4.0])
    first = du._predictive_draws(means, {"family": "poisson"}, seed=9)
    second = du._predictive_draws(means, {"family": "poisson"}, seed=9)
    assert np.array_equal(first, second)


def _nb_config(alpha: float = 0.9, fingerprint: str = "fp-test") -> dict:
    config = _config()
    config["noise"] = {
        "family": "negative_binomial",
        "dispersion_alpha": alpha,
        "panel_fingerprint": fingerprint,
        "source": "test",
    }
    return config


def test_the_noise_family_comes_from_config_not_the_mean_model() -> None:
    # The replicas say model_family=poisson (the mean model). The interval must
    # still follow the configured noise family.
    poisson = du.build_uncertainty_summary(
        _panel(), _replicas(), _metadata(), _config(), VERSION
    )
    nb = du.build_uncertainty_summary(
        _panel(), _replicas(), _metadata(), _nb_config(alpha=2.0), VERSION
    )
    assert poisson["noise"]["family"] == "poisson"
    assert nb["noise"] == {
        "family": "negative_binomial", "dispersion_alpha": 2.0,
        "source": "test", "panel_fingerprint": "fp-test",
    }
    assert nb["model_family"] == "poisson"
    def width(summary: dict) -> float:
        return sum(c["prediction_high"] - c["prediction_low"] for c in summary["cells"])

    assert width(nb) > width(poisson)


def test_a_missing_noise_family_is_refused() -> None:
    config = _config()
    del config["noise"]
    with pytest.raises(du.UncertaintyError, match="noise.family"):
        du.build_uncertainty_summary(_panel(), _replicas(), _metadata(), config, VERSION)


def test_an_alpha_from_another_panel_is_refused() -> None:
    with pytest.raises(du.UncertaintyError, match="panel_fingerprint"):
        du.build_uncertainty_summary(
            _panel(), _replicas(), _metadata(), _nb_config(fingerprint="other"), VERSION
        )


def test_coverage_is_reported_per_event_with_its_width() -> None:
    summary = du.build_uncertainty_summary(
        _panel(), _replicas(), _metadata(), _config(), VERSION
    )
    by_event = summary["coverage"]["by_event"]
    assert sum(e["n_cells"] for e in by_event) == summary["coverage"]["n_cells"]
    assert sum(e["covered_cells"] for e in by_event) == summary["coverage"]["covered_cells"]
    assert all(e["covered_cells"] + e["above"] + e["below"] == e["n_cells"] for e in by_event)
    assert summary["coverage"]["median_width"] >= 0
