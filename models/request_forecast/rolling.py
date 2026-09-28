"""Rolling-origin candidate evaluation for the request forecast (H2-R1 + R2).

The production M1 trainer deliberately remains a single Poisson fit.  This
module is the comparison layer: expanding-window seasonal folds, Poisson and
NB2 GLMs, a heavily regularised Poisson GBM, and metrics shared by every
candidate.  Keeping it separate prevents an experiment from silently changing
the model that serves ``fact_request_forecast``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from models.request_forecast import model as mdl

if TYPE_CHECKING:  # pragma: no cover - import-time only
    from collections.abc import Iterable

    import pandas as pd


class RollingEvaluationError(RuntimeError):
    """A registered fold or candidate cannot be evaluated faithfully."""


@dataclass(frozen=True)
class RollingFold:
    """One expanding-window split, identified by its held-out season."""

    fold_id: int
    train: pd.DataFrame
    test: pd.DataFrame
    holdout_season: str


@dataclass(frozen=True)
class NegativeBinomialFit:
    """NB2 fit plus the registered dispersion estimate and Wald interval."""

    results: Any
    alpha: float
    alpha_ci_low: float
    alpha_ci_high: float


@dataclass(frozen=True)
class GbmFit:
    """Poisson-rate GBM and the feature order it was fitted with."""

    estimator: Any
    feature_names: tuple[str, ...]


def rolling_origin_splits(panel: pd.DataFrame) -> list[RollingFold]:
    """Create one expanding-window fold per scheduling-era snow season.

    ``panel`` must already have causal lag features and have had the first
    history-less event removed.  Every test row belongs to exactly one fold;
    every training row is strictly earlier than that fold's first test event.
    """
    required = {"season", "event_start", "is_scheduling_era"}
    missing = sorted(required - set(panel.columns))
    if missing:
        raise RollingEvaluationError(f"rolling panel is missing column(s) {missing}")

    seasons = sorted(
        panel.loc[panel["is_scheduling_era"].astype(bool), "season"].dropna().unique()
    )
    if not seasons:
        raise RollingEvaluationError("rolling panel has no scheduling-era seasons")

    folds: list[RollingFold] = []
    for fold_id, season in enumerate(seasons, start=1):
        test = panel[panel["season"] == season].reset_index(drop=True)
        if test.empty:
            raise RollingEvaluationError(f"holdout season {season!r} is empty")
        first_test = test["event_start"].min()
        train = panel[panel["event_start"] < first_test].reset_index(drop=True)
        if train.empty:
            raise RollingEvaluationError(f"holdout season {season!r} has no prior training rows")
        mdl.assert_no_history_leak(train, test)
        folds.append(
            RollingFold(
                fold_id=fold_id,
                train=train,
                test=test,
                holdout_season=str(season),
            )
        )

    held_out = [index for fold in folds for index in fold.test.index]
    expected = int(panel["is_scheduling_era"].astype(bool).sum())
    if len(held_out) != expected:
        raise RollingEvaluationError(
            f"rolling folds hold out {len(held_out)} rows, scheduling era has {expected}"
        )
    return folds


def fit_negative_binomial(
    X: pd.DataFrame, y: pd.Series, offset: pd.Series
) -> NegativeBinomialFit:
    """Fit the registered NB2 model, estimating alpha by maximum likelihood."""
    import warnings

    import numpy as np
    from statsmodels.discrete.discrete_model import NegativeBinomial

    model = NegativeBinomial(y, X, offset=offset, loglike_method="nb2")
    # The optimizer probes invalid trial values for alpha and statsmodels emits
    # RuntimeWarning from those rejected likelihood evaluations.  Convergence
    # and finite fitted values are checked immediately below, so surfacing the
    # transient probes would make a successful batch look failed.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            category=RuntimeWarning,
            module=r"statsmodels\.discrete\.discrete_model",
        )
        results = model.fit(maxiter=200, disp=0)
    converged = bool(results.mle_retvals.get("converged", False))
    if not converged:
        raise RollingEvaluationError("NB2 maximum-likelihood fit did not converge")
    if "alpha" not in results.params.index:
        raise RollingEvaluationError("NB2 fit did not return an alpha parameter")

    alpha = float(results.params["alpha"])
    interval = results.conf_int(alpha=0.05).loc["alpha"]
    values = np.asarray([alpha, float(interval.iloc[0]), float(interval.iloc[1])])
    if not np.isfinite(values).all() or alpha <= 0.0:
        raise RollingEvaluationError(f"NB2 returned invalid dispersion values {values.tolist()}")
    return NegativeBinomialFit(
        results=results,
        alpha=alpha,
        alpha_ci_low=float(interval.iloc[0]),
        alpha_ci_high=float(interval.iloc[1]),
    )


def poisson_pearson_chi2_df(
    actual: pd.Series, predicted: pd.Series, parameter_count: int
) -> float:
    """Pearson chi-square divided by residual degrees of freedom."""
    import numpy as np

    y = actual.astype("float64").to_numpy()
    mu = np.clip(predicted.astype("float64").to_numpy(), 1.0e-10, None)
    residual_df = len(y) - parameter_count
    if residual_df <= 0:
        raise RollingEvaluationError(
            f"Pearson dispersion needs positive residual df, got {residual_df}"
        )
    return float(np.sum((y - mu) ** 2 / mu) / residual_df)


def prediction_interval(
    predicted_mean: pd.Series,
    *,
    family: str,
    level: float,
    alpha: float | None = None,
) -> tuple[pd.Series, pd.Series]:
    """Plug-in count interval for Poisson or NB2 means.

    NB2 uses ``Var(Y) = mu + alpha * mu**2``, represented as a scipy negative
    binomial with ``n = 1/alpha`` and ``p = n/(n+mu)``.
    """
    import numpy as np
    import pandas as pd
    from scipy import stats

    if not 0.0 < level < 1.0:
        raise RollingEvaluationError(f"interval level must be between 0 and 1, got {level}")
    mu = np.clip(predicted_mean.astype("float64").to_numpy(), 0.0, None)
    tail = (1.0 - level) / 2.0
    if family == "poisson":
        low = stats.poisson.ppf(tail, mu)
        high = stats.poisson.ppf(1.0 - tail, mu)
    elif family == "negative_binomial":
        if alpha is None or alpha <= 0.0:
            raise RollingEvaluationError("negative-binomial intervals require positive alpha")
        size = 1.0 / alpha
        probability = size / (size + mu)
        low = stats.nbinom.ppf(tail, size, probability)
        high = stats.nbinom.ppf(1.0 - tail, size, probability)
    else:
        raise RollingEvaluationError(f"unsupported interval family {family!r}")
    return (
        pd.Series(low, index=predicted_mean.index, dtype="float64"),
        pd.Series(high, index=predicted_mean.index, dtype="float64"),
    )


def interval_coverage(
    actual: pd.Series, lower: pd.Series, upper: pd.Series
) -> float:
    """Fraction of actual counts within inclusive interval bounds."""
    covered = (actual.astype("float64") >= lower) & (actual.astype("float64") <= upper)
    return float(covered.mean())


def severity_from_training_bounds(
    training: pd.DataFrame, frame: pd.DataFrame
) -> pd.Series:
    """Recompute severity using only a fold's training-side min/max bounds."""
    import pandas as pd

    for name in ("total_snowfall_cm", "min_temperature_c"):
        if name not in training or name not in frame:
            raise RollingEvaluationError(f"severity recomputation needs {name!r}")

    snow_train = training["total_snowfall_cm"].astype("float64")
    cold_train = (-training["min_temperature_c"].fillna(0.0).astype("float64")).clip(lower=0.0)
    snow = frame["total_snowfall_cm"].astype("float64")
    cold = (-frame["min_temperature_c"].fillna(0.0).astype("float64")).clip(lower=0.0)

    def scale(values: pd.Series, low: float, high: float) -> pd.Series:
        if high == low:
            return pd.Series(0.0, index=values.index, dtype="float64")
        return (values - low) / (high - low)

    return 0.5 * scale(snow, float(snow_train.min()), float(snow_train.max())) + 0.5 * scale(
        cold, float(cold_train.min()), float(cold_train.max())
    )


def with_training_severity(training: pd.DataFrame, frame: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with fold-causal severity, used by the GBM candidate."""
    out = frame.copy()
    out["severity_score"] = severity_from_training_bounds(training, frame)
    return out


def fit_gbm(
    training: pd.DataFrame,
    feature_names: Iterable[str],
    *,
    random_seed: int,
) -> GbmFit:
    """Fit the pre-registered heavily regularised Poisson rate GBM."""
    from sklearn.ensemble import HistGradientBoostingRegressor

    names = tuple(feature_names)
    X = training[list(names)].astype("float64")
    unit_size = training["unit_size"].astype("float64")
    rate = training["target"].astype("float64") / unit_size
    estimator = HistGradientBoostingRegressor(
        loss="poisson",
        max_depth=2,
        max_leaf_nodes=4,
        min_samples_leaf=110,
        learning_rate=0.05,
        max_iter=200,
        l2_regularization=1.0,
        early_stopping=False,
        random_state=random_seed,
    )
    estimator.fit(X, rate, sample_weight=unit_size)
    return GbmFit(estimator=estimator, feature_names=names)


def predict_gbm(fit: GbmFit, frame: pd.DataFrame) -> pd.Series:
    """Predict counts from the GBM's rate response."""
    import pandas as pd

    rates = fit.estimator.predict(frame[list(fit.feature_names)].astype("float64"))
    counts = rates * frame["unit_size"].astype("float64").to_numpy()
    return pd.Series(counts, index=frame.index, dtype="float64").clip(lower=0.0)


def event_metrics(frame: pd.DataFrame, predicted: pd.Series) -> dict[str, float | int]:
    """Median/max event MAE and median within-event Spearman/top-5 overlap."""
    import numpy as np

    scored = frame[["event_id", "unit_id", "target"]].copy()
    scored["predicted"] = predicted.to_numpy()
    maes: list[float] = []
    correlations: list[float] = []
    overlaps: list[int] = []
    for _event_id, event in scored.groupby("event_id", sort=False):
        maes.append(mdl.mean_absolute_error(event["target"], event["predicted"]))
        if event["target"].nunique() > 1 and event["predicted"].nunique() > 1:
            correlation = event["target"].corr(event["predicted"], method="spearman")
            if not np.isnan(correlation):
                correlations.append(float(correlation))
        actual_top = set(event.nlargest(5, "target")["unit_id"])
        predicted_top = set(event.nlargest(5, "predicted")["unit_id"])
        overlaps.append(len(actual_top & predicted_top))
    return {
        "event_mae_median": float(np.median(maes)),
        "event_mae_max": float(np.max(maes)),
        "event_spearman_median": (
            float(np.median(correlations)) if correlations else float("nan")
        ),
        "event_top5_overlap_median": float(np.median(overlaps)),
        "event_count": len(maes),
    }


def tail_mae(training: pd.DataFrame, test: pd.DataFrame, predicted: pd.Series) -> tuple[float, float, int]:
    """MAE on test cells at or above the training-side target P90."""
    threshold = float(training["target"].astype("float64").quantile(0.9))
    mask = test["target"].astype("float64") >= threshold
    if not bool(mask.any()):
        return threshold, float("nan"), 0
    return (
        threshold,
        mdl.mean_absolute_error(test.loc[mask, "target"], predicted.loc[mask]),
        int(mask.sum()),
    )
