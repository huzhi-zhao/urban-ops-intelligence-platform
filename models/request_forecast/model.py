"""Fit, predict and evaluate M1 — and its baseline, which is not optional.

BO §4.4: *a model result without a baseline is not accepted*. That is enforced
structurally here — :func:`evaluate` returns model and baseline metrics in one
object and there is no function that returns only the model's.

The model is a Poisson GLM with ``log(unit_size)`` as offset. Chosen over a
gradient-boosted tree for three reasons that all point the same way at this
size: the target is a count, the panel is 2,178 cells, and the coefficients
have to survive being explained on a slide. Design §8 records the trade; if the
held-out season says the GLM loses to the baseline, the escalation path is a
negative binomial or a GBM **under a new model_version**, keeping this one's
predictions queryable (ADR 0010 D5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from models.request_forecast.features import HISTORY_FEATURES, PanelError

if TYPE_CHECKING:  # pragma: no cover - import-time only
    import pandas as pd


@dataclass(frozen=True)
class Split:
    """A time-ordered train/test split. Never random — BO §4.4."""

    train: pd.DataFrame
    test: pd.DataFrame
    holdout_season: str


@dataclass(frozen=True)
class Metrics:
    """One method's scores on one set of rows."""

    mae: float
    poisson_deviance: float
    n_rows: int


@dataclass(frozen=True)
class Evaluation:
    """Model and baseline, together, because neither means anything alone."""

    model: Metrics
    baseline: Metrics
    holdout_season: str
    # Kept as a field rather than a property so it lands in the artefact's JSON
    # exactly as it was computed, instead of being recomputed by whoever reads it.
    beats_baseline: bool = field(default=False)

    def summary(self) -> str:
        """One line for the launch doc and the build notification.

        Deliberately states both numbers even when the model loses. Gate a4 says
        losing does not block the release; misreporting it does.
        """
        verdict = "beats" if self.beats_baseline else "does NOT beat"
        return (
            f"holdout {self.holdout_season}: "
            f"model MAE {self.model.mae:.3f} / deviance {self.model.poisson_deviance:.3f}; "
            f"seasonal-naive MAE {self.baseline.mae:.3f} / "
            f"deviance {self.baseline.poisson_deviance:.3f} "
            f"({self.model.n_rows} rows) — model {verdict} baseline"
        )


def split_holdout_last_season(panel: pd.DataFrame) -> Split:
    """Hold out the most recent snow season; train on everything before it.

    🔴 Time-ordered, never random (BO §4.4). A random split would put events
    from the held-out season into training and, through the lag features, let
    the test season's own history predict itself — the exact leak the whole
    §4.3 boundary exists to prevent.

    Raises:
        PanelError: if that leaves nothing to train on. With one season of data
            the honest answer is "not evaluable", not a metric computed on an
            empty frame.
    """
    seasons = sorted(panel["season"].dropna().unique())
    if len(seasons) < 2:
        raise PanelError(
            f"need at least 2 snow seasons to hold one out, panel has {len(seasons)}"
        )
    holdout = seasons[-1]
    train = panel[panel["season"] != holdout].reset_index(drop=True)
    test = panel[panel["season"] == holdout].reset_index(drop=True)
    if train.empty or test.empty:
        raise PanelError(f"holdout season {holdout!r} left an empty side")
    return Split(train=train, test=test, holdout_season=holdout)


def seasonal_naive(panel: pd.DataFrame) -> pd.Series:
    """The baseline: each unit's mean request count over its **prior** events.

    This is BO §4.4's "historical average request volume" method, and it is
    literally the ``expanding_mean`` feature — the same quantity, so the two can
    never drift apart.

    🟡 Design §4.1 words the baseline as "the mean over all *training-period*
    events for that unit". The causal expanding mean is that number for every
    held-out-season row (all of a test row's prior events are training events)
    while also being defined for training rows, which the design's wording is
    not. F5's ``baseline_count`` is declared "null only for the earliest events
    with no prior history to average over" — that sentence only makes sense
    under the expanding form, so it is the one implemented. Recorded in the L3
    launch doc §4.
    """
    if "expanding_mean" not in panel.columns:
        raise PanelError(
            "panel has no expanding_mean column — call features.build_panel_features first"
        )
    return panel["expanding_mean"].astype("float64")


def fit_poisson_glm(X: pd.DataFrame, y: pd.Series, offset: pd.Series) -> Any:
    """Fit the GLM. Returns statsmodels' results object.

    ``maxiter`` is raised from the default because a near-separable column
    (``accum_flag`` in a short panel) converges slowly rather than not at all,
    and a silent non-convergence would be reported as a fitted model.
    """
    import statsmodels.api as sm

    model = sm.GLM(y, X, family=sm.families.Poisson(), offset=offset)
    results = model.fit(maxiter=200)
    if not getattr(results, "converged", True):
        raise RuntimeError(
            "Poisson GLM did not converge. Do not ship the coefficients — check "
            "for a constant or near-constant feature column first."
        )
    return results


def predict(results: Any, X: pd.DataFrame, offset: pd.Series) -> pd.Series:
    """Predicted counts on the response scale, floored at zero.

    The inverse link cannot return a negative, so the floor is defensive rather
    than corrective; it exists so a future model family cannot introduce
    negative "counts" without someone noticing this line.
    """
    predicted = results.predict(X, offset=offset)
    return predicted.clip(lower=0.0)


def mean_absolute_error(actual: pd.Series, predicted: pd.Series) -> float:
    return float((actual.astype("float64") - predicted.astype("float64")).abs().mean())


def poisson_deviance(actual: pd.Series, predicted: pd.Series) -> float:
    """Mean Poisson deviance.

    ``y * log(y / mu)`` is taken as 0 where ``y == 0``, which is its limit and
    not a special case — without it a zero-count cell returns NaN and poisons
    the mean. Zero cells are common here: 908 of the panel's 1,298
    scheduling-era cells are non-zero, so roughly 30% are exactly this case.
    """
    import numpy as np

    y = actual.astype("float64").to_numpy()
    mu = np.clip(predicted.astype("float64").to_numpy(), 1e-10, None)
    # The 1.0 substitution is inside the log, not around it: np.where evaluates
    # *both* branches, so writing the condition around `np.log(y / mu)` still
    # computes log(0) for every zero cell and fills the run with divide-by-zero
    # warnings. The substituted value is then multiplied by y = 0 and discarded.
    safe_y = np.where(y > 0, y, 1.0)
    term = y * np.log(safe_y / mu)
    return float(np.mean(2.0 * (term - (y - mu))))


def evaluate(
    actual: pd.Series,
    model_predicted: pd.Series,
    baseline_predicted: pd.Series,
    holdout_season: str,
) -> Evaluation:
    """Score model and baseline on the same rows.

    Rows where the baseline is undefined are dropped from **both** sides, not
    just the baseline's. Scoring the model on rows the baseline cannot see would
    compare two methods on two different row sets and call it a comparison.
    """
    import numpy as np

    usable = ~baseline_predicted.isna().to_numpy()
    if not usable.any():
        raise PanelError(
            "the baseline is undefined on every holdout row — no comparison is "
            "possible, so no metric may be reported"
        )
    a = actual[usable]
    m = model_predicted[usable]
    b = baseline_predicted[usable]

    model_metrics = Metrics(
        mae=mean_absolute_error(a, m),
        poisson_deviance=poisson_deviance(a, m),
        n_rows=int(np.count_nonzero(usable)),
    )
    baseline_metrics = Metrics(
        mae=mean_absolute_error(a, b),
        poisson_deviance=poisson_deviance(a, b),
        n_rows=int(np.count_nonzero(usable)),
    )
    return Evaluation(
        model=model_metrics,
        baseline=baseline_metrics,
        holdout_season=holdout_season,
        beats_baseline=model_metrics.mae < baseline_metrics.mae,
    )


def assert_no_history_leak(train: pd.DataFrame, test: pd.DataFrame) -> None:
    """Check the split is time-ordered, not merely disjoint.

    Every training event must start no later than the earliest held-out one. A
    random split passes a "the two frames share no rows" check and fails this
    one, which is the difference that matters.
    """
    if train.empty or test.empty:
        raise PanelError("cannot check a leak across an empty split")
    latest_train = train["event_start"].max()
    earliest_test = test["event_start"].min()
    if latest_train >= earliest_test:
        raise PanelError(
            f"split is not time-ordered: a training event starts {latest_train}, "
            f"on or after the earliest holdout event {earliest_test}. The lag "
            f"features ({', '.join(HISTORY_FEATURES)}) make this a real leak, "
            f"not a technicality."
        )
