"""Behavioral probes for one fitted request-forecast model (H2-R3).

The probes perturb one feature at a time while keeping every other design
column and the unit-size offset fixed.  They deliberately have no labels:
synthetic weather can reveal a model's shape, not its accuracy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from models.request_forecast import outlook

if TYPE_CHECKING:  # pragma: no cover - import-time only
    from collections.abc import Mapping, Sequence

    import pandas as pd


class BehaviorCheckError(ValueError):
    """A behavior probe is structurally invalid and cannot be interpreted."""


@dataclass(frozen=True)
class MonotonicityResult:
    """Observed response ordering for one one-at-a-time feature sweep."""

    feature: str
    input_order: str
    input_start: float
    input_end: float
    grid_points: int
    anchor_rows: int
    comparisons: int
    violations: int
    non_finite_predictions: int
    max_relative_drop: float
    min_prediction: float | None
    max_prediction: float | None

    @property
    def has_finding(self) -> bool:
        return self.violations > 0 or self.non_finite_predictions > 0


@dataclass(frozen=True)
class ExtrapolationResult:
    """Response growth from the training boundary to one registered extreme."""

    feature: str
    training_max: float
    extreme_value: float
    training_max_multiplier: float
    explosion_ratio: float
    anchor_rows: int
    non_finite_or_non_positive_predictions: int
    max_response_ratio: float | None
    max_boundary_prediction: float | None
    max_extreme_prediction: float | None

    @property
    def has_finding(self) -> bool:
        ratio_exceeds = (
            self.max_response_ratio is not None
            and self.max_response_ratio > self.explosion_ratio
            and not math.isclose(self.max_response_ratio, self.explosion_ratio, rel_tol=1.0e-12)
        )
        return self.non_finite_or_non_positive_predictions > 0 or ratio_exceeds


def check_monotonicity(
    anchors: pd.DataFrame,
    coefficients: Mapping[str, float],
    feature_names: Sequence[str],
    *,
    feature: str,
    input_order: Literal["increasing", "decreasing"],
    grid_points: int,
    relative_tolerance: float,
) -> MonotonicityResult:
    """Sweep ``feature`` through its observed range at every training anchor.

    Values are ordered from less to more of the stress named by the check:
    snowfall increases, while temperature decreases (becomes colder).  In both
    cases the registered common-sense expectation is a nondecreasing response.
    """
    import numpy as np

    _validate_probe_inputs(
        anchors,
        feature_names,
        feature=feature,
        grid_points=grid_points,
        relative_tolerance=relative_tolerance,
    )
    if input_order not in {"increasing", "decreasing"}:
        raise BehaviorCheckError(
            f"{feature}: input_order must be 'increasing' or 'decreasing', got {input_order!r}"
        )

    low = float(anchors[feature].min())
    high = float(anchors[feature].max())
    if low == high:
        raise BehaviorCheckError(
            f"{feature}: training range is constant at {low}; monotonicity is not testable"
        )
    start, end = (low, high) if input_order == "increasing" else (high, low)
    values = np.linspace(start, end, grid_points)
    predictions = _prediction_grid(anchors, coefficients, feature_names, feature, values)

    finite = np.isfinite(predictions)
    non_finite = int(predictions.size - np.count_nonzero(finite))
    before = predictions[:, :-1]
    after = predictions[:, 1:]
    comparable = np.isfinite(before) & np.isfinite(after)
    denominator = np.maximum(np.abs(before), np.finfo("float64").tiny)
    relative_drop = np.where(comparable, (before - after) / denominator, np.nan)
    violations = comparable & (relative_drop > relative_tolerance)
    finite_predictions = predictions[finite]
    finite_drops = relative_drop[np.isfinite(relative_drop)]

    return MonotonicityResult(
        feature=feature,
        input_order=input_order,
        input_start=start,
        input_end=end,
        grid_points=grid_points,
        anchor_rows=len(anchors),
        comparisons=int(np.count_nonzero(comparable)),
        violations=int(np.count_nonzero(violations)),
        non_finite_predictions=non_finite,
        max_relative_drop=float(max(0.0, finite_drops.max(initial=0.0))),
        min_prediction=(float(finite_predictions.min()) if finite_predictions.size else None),
        max_prediction=(float(finite_predictions.max()) if finite_predictions.size else None),
    )


def check_extrapolation(
    anchors: pd.DataFrame,
    coefficients: Mapping[str, float],
    feature_names: Sequence[str],
    *,
    feature: str,
    training_max_multiplier: float,
    explosion_ratio: float,
) -> ExtrapolationResult:
    """Compare the response at the training maximum with a fixed extreme."""
    import numpy as np

    _validate_probe_inputs(anchors, feature_names, feature=feature)
    if training_max_multiplier <= 1.0:
        raise BehaviorCheckError("training_max_multiplier must be greater than 1")
    if explosion_ratio <= 1.0:
        raise BehaviorCheckError("explosion_ratio must be greater than 1")

    training_max = float(anchors[feature].max())
    if training_max <= 0.0:
        raise BehaviorCheckError(
            f"{feature}: training maximum must be positive, got {training_max}"
        )
    extreme = training_max * training_max_multiplier
    predictions = _prediction_grid(
        anchors,
        coefficients,
        feature_names,
        feature,
        np.asarray([training_max, extreme], dtype="float64"),
    )
    boundary = predictions[:, 0]
    extrapolated = predictions[:, 1]
    valid = (
        np.isfinite(boundary) & np.isfinite(extrapolated) & (boundary > 0.0) & (extrapolated > 0.0)
    )
    ratios = extrapolated[valid] / boundary[valid]
    finite_boundary = boundary[np.isfinite(boundary)]
    finite_extrapolated = extrapolated[np.isfinite(extrapolated)]

    return ExtrapolationResult(
        feature=feature,
        training_max=training_max,
        extreme_value=extreme,
        training_max_multiplier=training_max_multiplier,
        explosion_ratio=explosion_ratio,
        anchor_rows=len(anchors),
        non_finite_or_non_positive_predictions=int(len(anchors) - np.count_nonzero(valid)),
        max_response_ratio=(float(ratios.max()) if ratios.size else None),
        max_boundary_prediction=(float(finite_boundary.max()) if finite_boundary.size else None),
        max_extreme_prediction=(
            float(finite_extrapolated.max()) if finite_extrapolated.size else None
        ),
    )


def _prediction_grid(
    anchors: pd.DataFrame,
    coefficients: Mapping[str, float],
    feature_names: Sequence[str],
    feature: str,
    values,
):
    import numpy as np

    columns = []
    with np.errstate(over="ignore", under="ignore", invalid="ignore"):
        for value in values:
            probe = anchors.copy()
            probe[feature] = float(value)
            columns.append(
                np.asarray(
                    outlook.predict_mean(probe, coefficients, feature_names),
                    dtype="float64",
                )
            )
    return np.column_stack(columns)


def _validate_probe_inputs(
    anchors: pd.DataFrame,
    feature_names: Sequence[str],
    *,
    feature: str,
    grid_points: int | None = None,
    relative_tolerance: float | None = None,
) -> None:
    if anchors.empty:
        raise BehaviorCheckError("behavior probes need at least one training anchor row")
    if feature not in feature_names:
        raise BehaviorCheckError(f"{feature!r} is not in the fitted feature list")
    if feature not in anchors:
        raise BehaviorCheckError(f"training anchors have no {feature!r} column")
    if anchors[feature].isna().any():
        raise BehaviorCheckError(f"training anchors contain null {feature!r} values")
    if grid_points is not None and grid_points < 2:
        raise BehaviorCheckError("grid_points must be at least 2")
    if relative_tolerance is not None and relative_tolerance < 0.0:
        raise BehaviorCheckError("relative_tolerance cannot be negative")
