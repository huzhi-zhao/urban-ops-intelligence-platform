"""Pure behavioral probes for H2-R3."""

from __future__ import annotations

import pytest

pd = pytest.importorskip("pandas", reason="needs the `ml` extra — run `make test-ml`")
np = pytest.importorskip("numpy", reason="needs the `ml` extra — run `make test-ml`")

from models.request_forecast import behavior  # noqa: E402

NAMES = ["total_snowfall_cm", "min_temperature_c"]


def _anchors() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "unit_size": [1000.0, 2500.0],
            "target": [4.0, 9.0],
            "total_snowfall_cm": [5.0, 10.0],
            "min_temperature_c": [-5.0, -15.0],
        }
    )


def _coefficients(*, snow: float = 0.1, temperature: float = -0.05) -> dict[str, float]:
    return {
        "const": -5.0,
        "total_snowfall_cm": snow,
        "min_temperature_c": temperature,
    }


@pytest.mark.parametrize(
    ("feature", "input_order"),
    [("total_snowfall_cm", "increasing"), ("min_temperature_c", "decreasing")],
)
def test_common_sense_directions_are_nondecreasing(feature, input_order) -> None:
    result = behavior.check_monotonicity(
        _anchors(),
        _coefficients(),
        NAMES,
        feature=feature,
        input_order=input_order,
        grid_points=9,
        relative_tolerance=1.0e-12,
    )

    assert result.anchor_rows == 2
    assert result.comparisons == 2 * 8
    assert result.violations == 0
    assert result.non_finite_predictions == 0
    assert not result.has_finding


@pytest.mark.parametrize(
    ("feature", "input_order", "coefficients"),
    [
        ("total_snowfall_cm", "increasing", _coefficients(snow=-0.1)),
        ("min_temperature_c", "decreasing", _coefficients(temperature=0.05)),
    ],
)
def test_wrong_signs_are_recorded_as_findings(feature, input_order, coefficients) -> None:
    result = behavior.check_monotonicity(
        _anchors(),
        coefficients,
        NAMES,
        feature=feature,
        input_order=input_order,
        grid_points=9,
        relative_tolerance=1.0e-12,
    )

    assert result.violations == result.comparisons == 16
    assert result.max_relative_drop > 0.0
    assert result.has_finding


def test_extrapolation_uses_a_scale_free_response_ratio() -> None:
    result = behavior.check_extrapolation(
        _anchors(),
        _coefficients(snow=0.1),
        NAMES,
        feature="total_snowfall_cm",
        training_max_multiplier=2.0,
        explosion_ratio=100.0,
    )

    assert result.training_max == 10.0
    assert result.extreme_value == 20.0
    assert result.max_response_ratio == pytest.approx(np.e)
    assert not result.has_finding


def test_more_than_two_orders_of_magnitude_is_a_finding() -> None:
    coefficient = float(np.log(101.0) / 10.0)
    result = behavior.check_extrapolation(
        _anchors(),
        _coefficients(snow=coefficient),
        NAMES,
        feature="total_snowfall_cm",
        training_max_multiplier=2.0,
        explosion_ratio=100.0,
    )

    assert result.max_response_ratio == pytest.approx(101.0)
    assert result.has_finding


def test_exactly_the_registered_boundary_is_not_a_finding() -> None:
    coefficient = float(np.log(100.0) / 10.0)
    result = behavior.check_extrapolation(
        _anchors(),
        _coefficients(snow=coefficient),
        NAMES,
        feature="total_snowfall_cm",
        training_max_multiplier=2.0,
        explosion_ratio=100.0,
    )

    assert result.max_response_ratio == pytest.approx(100.0)
    assert not result.has_finding


def test_non_finite_extrapolation_is_recorded_instead_of_crashing() -> None:
    result = behavior.check_extrapolation(
        _anchors(),
        _coefficients(snow=1000.0),
        NAMES,
        feature="total_snowfall_cm",
        training_max_multiplier=2.0,
        explosion_ratio=100.0,
    )

    assert result.non_finite_or_non_positive_predictions == len(_anchors())
    assert result.has_finding


def test_a_constant_training_range_is_not_called_a_monotonicity_check() -> None:
    anchors = _anchors().assign(total_snowfall_cm=5.0)
    with pytest.raises(behavior.BehaviorCheckError, match="constant"):
        behavior.check_monotonicity(
            anchors,
            _coefficients(),
            NAMES,
            feature="total_snowfall_cm",
            input_order="increasing",
            grid_points=9,
            relative_tolerance=1.0e-12,
        )


def test_predictor_probe_supports_non_linear_models_on_the_same_grid() -> None:
    def predictor(frame):
        return frame["unit_size"] * (1.0 + frame["total_snowfall_cm"] ** 2)

    result = behavior.check_monotonicity_predictor(
        _anchors(),
        predictor,
        NAMES,
        feature="total_snowfall_cm",
        input_order="increasing",
        grid_points=9,
        relative_tolerance=1.0e-12,
    )

    assert result.comparisons == 16
    assert result.violations == 0
    assert not result.has_finding
