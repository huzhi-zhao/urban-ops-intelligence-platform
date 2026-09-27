"""Event-cluster bootstrap for M1 sampling uncertainty (H2-R13).

This module speaks only role names. The city boundary is crossed by
``scripts.models.bootstrap_m1`` when the artefact columns are written.

Features arrive already prepared and stay fixed. Each refit resamples complete
events from the original training split, then predicts the unchanged real
scoring panel. Recomputing lag features inside the loop would answer a different
question and is deliberately impossible through this API.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from models.request_forecast import features as feat
from models.request_forecast import model as mdl

if TYPE_CHECKING:  # pragma: no cover - import-time only
    import pandas as pd


PREDICTION_COLUMNS = ("replicate_id", "event_id", "unit_id", "predicted_mean")
COEFFICIENT_COLUMNS = ("replicate_id", "term", "estimate")


class BootstrapError(RuntimeError):
    """The requested bootstrap would violate R13's sampling contract."""


@dataclass(frozen=True)
class BootstrapRun:
    """Raw replicas and enough run facts to write complete metadata."""

    predictions: pd.DataFrame
    coefficients: pd.DataFrame
    replicate_seeds: tuple[int, ...]
    training_cluster_count: int
    units_per_cluster: int
    prediction_cell_count: int


def validate_event_clusters(panel: pd.DataFrame) -> tuple[list[Any], int]:
    """Return ordered event IDs and cluster width after checking a full panel.

    Every event must contain exactly the same unit set. Checking only equal row
    counts would let one duplicated unit conceal one missing unit, which is the
    exact partial-cluster failure this boundary exists to prevent.
    """
    missing = [c for c in ("event_id", "unit_id") if c not in panel.columns]
    if missing:
        raise BootstrapError(f"bootstrap panel is missing cluster column(s) {missing}")
    if panel.empty:
        raise BootstrapError("bootstrap panel is empty")
    if bool(panel.duplicated(["event_id", "unit_id"]).any()):
        raise BootstrapError("bootstrap panel has duplicate (event_id, unit_id) cells")

    event_ids = list(panel["event_id"].drop_duplicates())
    reference = set(panel.loc[panel["event_id"] == event_ids[0], "unit_id"])
    if not reference:
        raise BootstrapError("the first event cluster contains no units")
    for event_id, group in panel.groupby("event_id", sort=False):
        units = set(group["unit_id"])
        if units != reference:
            missing_units = sorted(reference - units)
            extra_units = sorted(units - reference)
            raise BootstrapError(
                f"event {event_id!r} is not a complete cluster; "
                f"missing units {missing_units[:3]}, extra units {extra_units[:3]}"
            )
    return event_ids, len(reference)


def replicate_seeds(root_seed: int, count: int) -> tuple[int, ...]:
    """Derive stable independent seeds, one per one-based replicate."""
    if count <= 0:
        raise BootstrapError(f"replicate count must be positive, got {count}")
    import numpy as np

    children = np.random.SeedSequence(int(root_seed)).spawn(count)
    return tuple(int(child.generate_state(1, dtype="uint64")[0]) for child in children)


def sample_event_clusters(
    training_panel: pd.DataFrame, child_seed: int
) -> pd.DataFrame:
    """Draw as many complete event clusters as the training panel contains."""
    import numpy as np
    import pandas as pd

    event_ids, _units_per_cluster = validate_event_clusters(training_panel)
    groups = {
        event_id: group.reset_index(drop=True)
        for event_id, group in training_panel.groupby("event_id", sort=False)
    }
    rng = np.random.default_rng(child_seed)
    drawn = rng.choice(event_ids, size=len(event_ids), replace=True)
    return pd.concat([groups[event_id] for event_id in drawn], ignore_index=True)


def fit_event_cluster_bootstrap(
    training_panel: pd.DataFrame,
    prediction_panel: pd.DataFrame,
    feature_names: list[str],
    replicate_count: int,
    random_seed: int,
    fit_fn: Callable[[pd.DataFrame, pd.Series, pd.Series], Any] = mdl.fit_poisson_glm,
    predict_fn: Callable[[Any, pd.DataFrame, pd.Series], pd.Series] = mdl.predict,
) -> BootstrapRun:
    """Refit on event-cluster replicas and predict the unchanged real panel.

    ``training_panel`` and ``prediction_panel`` must already contain features.
    The function never calls ``build_panel_features``; this makes the frozen-
    feature boundary structural rather than a convention hidden in a loop.
    """
    import numpy as np
    import pandas as pd

    training_events, units_per_cluster = validate_event_clusters(training_panel)
    validate_event_clusters(prediction_panel)
    if set(prediction_panel["unit_id"]) != set(training_panel["unit_id"]):
        raise BootstrapError("training and prediction panels contain different unit sets")

    x_prediction, _y_prediction, offset_prediction = feat.build_design_matrix(
        prediction_panel, feature_names
    )
    seeds = replicate_seeds(random_seed, replicate_count)
    prediction_parts: list[pd.DataFrame] = []
    coefficient_rows: list[dict[str, Any]] = []

    for replicate_id, child_seed in enumerate(seeds, start=1):
        sampled = sample_event_clusters(training_panel, child_seed)
        expected_rows = len(training_events) * units_per_cluster
        if len(sampled) != expected_rows:  # pragma: no cover - guarded by validation
            raise BootstrapError(
                f"replicate {replicate_id} has {len(sampled)} rows, expected {expected_rows}"
            )

        try:
            x_train, y_train, offset_train = feat.build_design_matrix(sampled, feature_names)
            results = fit_fn(x_train, y_train, offset_train)
            predicted = predict_fn(results, x_prediction, offset_prediction).astype("float64")
        except Exception as error:
            raise BootstrapError(
                f"replicate {replicate_id} (seed {child_seed}) failed; no replicas were skipped"
            ) from error

        params = results.params.astype("float64")
        if not np.isfinite(params.to_numpy()).all():
            raise BootstrapError(f"replicate {replicate_id} produced a non-finite coefficient")
        if not np.isfinite(predicted.to_numpy()).all() or bool((predicted < 0).any()):
            raise BootstrapError(
                f"replicate {replicate_id} produced a non-finite or negative prediction"
            )

        prediction_parts.append(
            pd.DataFrame(
                {
                    "replicate_id": replicate_id,
                    "event_id": prediction_panel["event_id"].to_numpy(),
                    "unit_id": prediction_panel["unit_id"].to_numpy(),
                    "predicted_mean": predicted.to_numpy(),
                }
            )
        )
        coefficient_rows.extend(
            {"replicate_id": replicate_id, "term": str(term), "estimate": float(value)}
            for term, value in params.items()
        )

    predictions = pd.concat(prediction_parts, ignore_index=True)
    predictions = predictions.sort_values(
        ["replicate_id", "event_id", "unit_id"], kind="stable"
    ).reset_index(drop=True)
    coefficients = pd.DataFrame(coefficient_rows, columns=COEFFICIENT_COLUMNS).sort_values(
        ["replicate_id", "term"], kind="stable"
    ).reset_index(drop=True)

    if bool(predictions.duplicated(["replicate_id", "event_id", "unit_id"]).any()):
        raise BootstrapError("bootstrap output has duplicate replica cells")
    expected_output = replicate_count * len(prediction_panel)
    if len(predictions) != expected_output:
        raise BootstrapError(
            f"bootstrap output has {len(predictions)} rows, expected {expected_output}"
        )

    return BootstrapRun(
        predictions=predictions[list(PREDICTION_COLUMNS)],
        coefficients=coefficients,
        replicate_seeds=seeds,
        training_cluster_count=len(training_events),
        units_per_cluster=units_per_cluster,
        prediction_cell_count=len(prediction_panel),
    )
