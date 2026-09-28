"""Run the frozen H2-R1/R2 rolling-origin model comparison.

This command is read-only with respect to production.  It consumes a dumped
M1 panel (or fetches the same panel from Trino) and writes local evaluation
artefacts under ``var/rolling-evaluation``.  Upload is intentionally absent:
the accepted design requires separate authorisation before production writes.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

from models.request_forecast import behavior, rolling
from models.request_forecast import features as feat
from models.request_forecast import model as mdl
from scripts._env import load_cli_env
from scripts.ddl.apply_ddl import TrinoConfigError
from scripts.models import check_m1_behavior, train_m1

if TYPE_CHECKING:  # pragma: no cover - import-time only
    import pandas as pd

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_PANEL = REPO_ROOT / "var" / "m1-panel-20260927.csv"
DEFAULT_REFERENCE = (
    REPO_ROOT
    / "var"
    / "forecast-runs"
    / "m1-poisson-20260822-df31d954"
    / "predictions.csv"
)
DEFAULT_OUT = REPO_ROOT / "var" / "rolling-evaluation"

FOLD_METRICS_FILE = "fold_metrics.csv"
CANDIDATE_PREDICTIONS_FILE = "candidate_predictions.csv"
ROLLING_PREDICTIONS_FILE = "rolling_predictions.csv"
SUMMARY_FILE = "summary.json"
BEHAVIOR_FILE = "candidate_behavior_checks.json"

CANDIDATES = ("seasonal_naive", "poisson", "negative_binomial", "gbm")
MODEL_CANDIDATES = ("poisson", "negative_binomial", "gbm")
EXPECTED_SEASONS = tuple(f"{year}-{year + 1}" for year in range(2015, 2026))
EXPECTED_HOLDOUT_ROWS = 1298
INTERVAL_LEVEL = 0.90
DEVELOPMENT_FOLDS = 10
STABILITY_WINS = 9
REPRODUCTION_TOLERANCE = 1.0e-9


class RollingRunError(RuntimeError):
    """A frozen R1/R2 acceptance gate failed."""


def _candidate_metrics(
    fold: rolling.RollingFold,
    candidate: str,
    predicted: pd.Series,
) -> dict[str, Any]:
    event = rolling.event_metrics(fold.test, predicted)
    threshold, tail_mae, tail_rows = rolling.tail_mae(fold.train, fold.test, predicted)
    return {
        "fold_id": fold.fold_id,
        "holdout_season": fold.holdout_season,
        "candidate": candidate,
        "train_events": int(fold.train["event_id"].nunique()),
        "test_events": int(fold.test["event_id"].nunique()),
        "train_rows": len(fold.train),
        "test_rows": len(fold.test),
        "mae": mdl.mean_absolute_error(fold.test["target"], predicted),
        "poisson_deviance": mdl.poisson_deviance(fold.test["target"], predicted),
        "tail_threshold_p90": threshold,
        "tail_mae": tail_mae,
        "tail_rows": tail_rows,
        **event,
    }


def _prediction_rows(
    fold: rolling.RollingFold,
    candidate: str,
    predicted: pd.Series,
) -> pd.DataFrame:
    import pandas as pd

    return pd.DataFrame(
        {
            "fold_id": fold.fold_id,
            "holdout_season": fold.holdout_season,
            "snowfall_event_id": fold.test["event_id"].to_numpy(),
            "plow_zone": fold.test["unit_id"].to_numpy(),
            "candidate": candidate,
            "predicted_count": predicted.to_numpy(),
            "actual_count": fold.test["target"].astype("int64").to_numpy(),
        }
    )


def _fit_glm_with_recomputed_severity(
    fold: rolling.RollingFold, names: list[str]
) -> pd.Series:
    changed_train = rolling.with_training_severity(fold.train, fold.train)
    changed_test = rolling.with_training_severity(fold.train, fold.test)
    X_train, y_train, offset_train = feat.build_design_matrix(changed_train, names)
    results = mdl.fit_poisson_glm(X_train, y_train, offset_train)
    X_test, _y_test, offset_test = feat.build_design_matrix(changed_test, names)
    return mdl.predict(results, X_test, offset_test)


def _reproduction_gate(
    last_fold: rolling.RollingFold,
    poisson_prediction: pd.Series,
    baseline_prediction: pd.Series,
    reference_path: Path,
) -> dict[str, float]:
    import numpy as np
    import pandas as pd

    if not reference_path.exists():
        raise RollingRunError(f"reproduction reference does not exist: {reference_path}")
    reference = pd.read_csv(reference_path)
    expected = pd.DataFrame(
        {
            "snowfall_event_id": last_fold.test["event_id"].to_numpy(),
            "plow_zone": last_fold.test["unit_id"].to_numpy(),
            "predicted": poisson_prediction.to_numpy(),
            "baseline": baseline_prediction.to_numpy(),
            "actual": last_fold.test["target"].to_numpy(),
        }
    )
    merged = expected.merge(
        reference[
            [
                "snowfall_event_id",
                "plow_zone",
                "predicted_count",
                "baseline_count",
                "actual_count",
            ]
        ],
        on=["snowfall_event_id", "plow_zone"],
        how="inner",
        validate="one_to_one",
    )
    if len(merged) != len(expected):
        raise RollingRunError(
            f"reproduction matched {len(merged)}/{len(expected)} last-fold rows"
        )
    maximum_difference = float(
        np.max(np.abs(merged["predicted"] - merged["predicted_count"]))
    )
    baseline_difference = float(
        np.max(np.abs(merged["baseline"] - merged["baseline_count"]))
    )
    actual_difference = float(np.max(np.abs(merged["actual"] - merged["actual_count"])))
    if max(maximum_difference, baseline_difference, actual_difference) >= REPRODUCTION_TOLERANCE:
        raise RollingRunError(
            "last-fold reproduction failed: "
            f"prediction {maximum_difference}, baseline {baseline_difference}, "
            f"actual {actual_difference}"
        )
    return {
        "max_prediction_difference": maximum_difference,
        "max_baseline_difference": baseline_difference,
        "max_actual_difference": actual_difference,
        "poisson_mae": mdl.mean_absolute_error(
            last_fold.test["target"], poisson_prediction
        ),
        "baseline_mae": mdl.mean_absolute_error(
            last_fold.test["target"], baseline_prediction
        ),
    }


def _ranking_summary(metrics: pd.DataFrame) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import itertools

    rankings: list[dict[str, Any]] = []
    complete_orders: Counter[tuple[str, ...]] = Counter()
    development = metrics[metrics["fold_id"] <= DEVELOPMENT_FOLDS]
    for (fold_id, season), group in metrics.groupby(["fold_id", "holdout_season"], sort=True):
        ordered = group.sort_values(["mae", "candidate"])["candidate"].tolist()
        complete_orders[tuple(ordered)] += 1
        for rank, candidate in enumerate(ordered, start=1):
            rankings.append(
                {
                    "fold_id": int(fold_id),
                    "holdout_season": season,
                    "candidate": candidate,
                    "rank": rank,
                }
            )

    comparisons = []
    for first, second in itertools.combinations(CANDIDATES, 2):
        pivot = development.pivot(index="fold_id", columns="candidate", values="mae")
        wins = int((pivot[first] < pivot[second]).sum())
        second_wins = DEVELOPMENT_FOLDS - wins
        stable_winner = None
        if wins >= STABILITY_WINS:
            stable_winner = first
        elif second_wins >= STABILITY_WINS:
            stable_winner = second
        comparisons.append(
            {
                "first": first,
                "second": second,
                "first_wins": wins,
                "second_wins": second_wins,
                "folds": DEVELOPMENT_FOLDS,
                "stable_winner": stable_winner,
            }
        )
    summary = {
        "complete_rank_orders": [
            {"order": list(order), "folds": count}
            for order, count in complete_orders.most_common()
        ],
        "pairwise_development_folds": comparisons,
    }
    return rankings, summary


def _top5_agreement(predictions: pd.DataFrame) -> dict[str, int]:
    all_three = 0
    events = 0
    models = predictions[predictions["candidate"].isin(MODEL_CANDIDATES)]
    for _event_id, event in models.groupby("snowfall_event_id", sort=False):
        top_sets = []
        for candidate in MODEL_CANDIDATES:
            rows = event[event["candidate"] == candidate]
            top_sets.append(frozenset(rows.nlargest(5, "predicted_count")["plow_zone"]))
        events += 1
        all_three += int(len(set(top_sets)) == 1)
    return {"events": events, "all_three_top5_identical": all_three}


def _behavior_report(
    last_fold: rolling.RollingFold,
    names: list[str],
    negbin: rolling.NegativeBinomialFit,
    gbm: rolling.GbmFit,
    gbm_train: pd.DataFrame,
) -> dict[str, Any]:
    config = check_m1_behavior.load_behavior_config()

    def nb_predict(frame):
        X, _y, offset = feat.build_design_matrix(frame, names)
        return mdl.predict(negbin.results, X, offset)

    def gbm_predict(frame):
        return rolling.predict_gbm(gbm, frame)

    reports: dict[str, Any] = {}
    for candidate, anchors, predictor in (
        ("negative_binomial", last_fold.train, nb_predict),
        ("gbm", gbm_train, gbm_predict),
    ):
        monotonicity = [
            behavior.check_monotonicity_predictor(
                anchors,
                predictor,
                names,
                feature=check["feature"],
                input_order=check["input_order"],
                grid_points=int(config["monotonicity"]["grid_points"]),
                relative_tolerance=float(config["monotonicity"]["relative_tolerance"]),
            )
            for check in config["monotonicity"]["checks"]
        ]
        extrapolation = behavior.check_extrapolation_predictor(
            anchors,
            predictor,
            names,
            feature=config["extrapolation"]["feature"],
            training_max_multiplier=float(
                config["extrapolation"]["training_max_multiplier"]
            ),
            explosion_ratio=float(config["extrapolation"]["explosion_ratio"]),
        )
        reports[candidate] = {
            "finding_count": sum(result.has_finding for result in monotonicity)
            + int(extrapolation.has_finding),
            "monotonicity": [
                {**dataclasses.asdict(result), "has_finding": result.has_finding}
                for result in monotonicity
            ],
            "extrapolation": {
                **dataclasses.asdict(extrapolation),
                "has_finding": extrapolation.has_finding,
            },
        }
    return reports


def evaluate_rolling(
    config: dict[str, Any], raw_panel: pd.DataFrame, reference_path: Path
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any], dict[str, Any]]:
    """Fit every registered fold/candidate and return deterministic artefacts."""
    import numpy as np
    import pandas as pd

    data = train_m1.prepare_training_data(config, raw_panel)
    folds = rolling.rolling_origin_splits(data.trainable)
    seasons = tuple(fold.holdout_season for fold in folds)
    if seasons != EXPECTED_SEASONS:
        raise RollingRunError(f"fold seasons {seasons} do not match frozen {EXPECTED_SEASONS}")
    if sum(len(fold.test) for fold in folds) != EXPECTED_HOLDOUT_ROWS:
        raise RollingRunError("rolling folds do not cover the frozen 1,298 holdout cells")

    metric_rows: list[dict[str, Any]] = []
    prediction_frames: list[pd.DataFrame] = []
    interval_rows: list[dict[str, Any]] = []
    invariance_rows: list[dict[str, Any]] = []
    last_models: dict[str, Any] = {}
    last_predictions: dict[str, pd.Series] = {}

    for fold in folds:
        X_train, y_train, offset_train = feat.build_design_matrix(
            fold.train, data.feature_names
        )
        X_test, _y_test, offset_test = feat.build_design_matrix(fold.test, data.feature_names)

        baseline_prediction = mdl.seasonal_naive(fold.test)
        poisson_fit = mdl.fit_poisson_glm(X_train, y_train, offset_train)
        poisson_prediction = mdl.predict(poisson_fit, X_test, offset_test)
        negbin_fit = rolling.fit_negative_binomial(X_train, y_train, offset_train)
        negbin_prediction = mdl.predict(negbin_fit.results, X_test, offset_test)

        gbm_train = rolling.with_training_severity(fold.train, fold.train)
        gbm_test = rolling.with_training_severity(fold.train, fold.test)
        gbm_fit = rolling.fit_gbm(
            gbm_train,
            data.feature_names,
            random_seed=int(config.get("random_seed", 20260820)),
        )
        gbm_prediction = rolling.predict_gbm(gbm_fit, gbm_test)

        predictions = {
            "seasonal_naive": baseline_prediction,
            "poisson": poisson_prediction,
            "negative_binomial": negbin_prediction,
            "gbm": gbm_prediction,
        }
        for candidate, prediction in predictions.items():
            row = _candidate_metrics(fold, candidate, prediction)
            if candidate == "poisson":
                train_prediction = mdl.predict(poisson_fit, X_train, offset_train)
                row["pearson_chi2_df"] = rolling.poisson_pearson_chi2_df(
                    y_train, train_prediction, len(X_train.columns)
                )
            if candidate == "negative_binomial":
                row.update(
                    alpha=negbin_fit.alpha,
                    alpha_ci_low=negbin_fit.alpha_ci_low,
                    alpha_ci_high=negbin_fit.alpha_ci_high,
                )
            metric_rows.append(row)
            prediction_frames.append(_prediction_rows(fold, candidate, prediction))

        poisson_low, poisson_high = rolling.prediction_interval(
            poisson_prediction, family="poisson", level=INTERVAL_LEVEL
        )
        negbin_low, negbin_high = rolling.prediction_interval(
            poisson_prediction,
            family="negative_binomial",
            level=INTERVAL_LEVEL,
            alpha=negbin_fit.alpha,
        )
        interval_rows.append(
            {
                "fold_id": fold.fold_id,
                "holdout_season": fold.holdout_season,
                "rows": len(fold.test),
                "poisson_coverage": rolling.interval_coverage(
                    fold.test["target"], poisson_low, poisson_high
                ),
                "negative_binomial_coverage": rolling.interval_coverage(
                    fold.test["target"], negbin_low, negbin_high
                ),
            }
        )

        changed_prediction = _fit_glm_with_recomputed_severity(fold, data.feature_names)
        maximum_difference = float(
            np.max(np.abs(poisson_prediction.to_numpy() - changed_prediction.to_numpy()))
        )
        invariance_rows.append(
            {
                "fold_id": fold.fold_id,
                "holdout_season": fold.holdout_season,
                "max_prediction_difference": maximum_difference,
                "passed": maximum_difference < REPRODUCTION_TOLERANCE,
            }
        )
        if maximum_difference >= REPRODUCTION_TOLERANCE:
            raise RollingRunError(
                f"fold {fold.fold_id} severity invariance difference {maximum_difference}"
            )

        if fold.fold_id == len(folds):
            last_models = {
                "negative_binomial": negbin_fit,
                "gbm": gbm_fit,
                "gbm_train": gbm_train,
            }
            last_predictions = predictions

    metrics = pd.DataFrame(metric_rows)
    candidates = pd.concat(prediction_frames, ignore_index=True)
    metrics["mae_rank"] = metrics.groupby("fold_id")["mae"].rank(
        method="first", ascending=True
    ).astype("int64")
    rankings, ranking_summary = _ranking_summary(metrics)

    development_intervals = [
        row for row in interval_rows if row["fold_id"] <= DEVELOPMENT_FOLDS
    ]
    development_rows = sum(row["rows"] for row in development_intervals)
    poisson_covered = sum(
        row["poisson_coverage"] * row["rows"] for row in development_intervals
    ) / development_rows
    negbin_covered = sum(
        row["negative_binomial_coverage"] * row["rows"] for row in development_intervals
    ) / development_rows
    alpha_rows = metrics[
        (metrics["candidate"] == "negative_binomial")
        & (metrics["fold_id"] <= DEVELOPMENT_FOLDS)
    ]
    alpha_clear_folds = int((alpha_rows["alpha_ci_low"] > 0.0).sum())
    use_negative_binomial = (
        alpha_clear_folds >= STABILITY_WINS
        and abs(negbin_covered - INTERVAL_LEVEL) < abs(poisson_covered - INTERVAL_LEVEL)
    )

    reproduction = _reproduction_gate(
        folds[-1],
        last_predictions["poisson"],
        last_predictions["seasonal_naive"],
        reference_path,
    )
    rolling_poisson = candidates[candidates["candidate"] == "poisson"].copy()
    if len(rolling_poisson) != EXPECTED_HOLDOUT_ROWS or rolling_poisson.duplicated(
        ["snowfall_event_id", "plow_zone"]
    ).any():
        raise RollingRunError("rolling Poisson predictions are not exactly 1,298 unique cells")

    summary = {
        "schema_version": 1,
        "panel_fingerprint": train_m1.panel_fingerprint(config, data.panel),
        "folds_completed": len(folds),
        "candidates_completed": len(CANDIDATES),
        "failed_fits": 0,
        "holdout_cells": len(rolling_poisson),
        "development_folds": DEVELOPMENT_FOLDS,
        "final_report_only_fold": 11,
        "reproduction_gate": reproduction,
        "severity_invariance": invariance_rows,
        "interval_coverage_by_fold": interval_rows,
        "distribution_family_decision": {
            "alpha_ci_excludes_zero_folds": alpha_clear_folds,
            "required_folds": STABILITY_WINS,
            "development_rows": development_rows,
            "poisson_coverage": poisson_covered,
            "negative_binomial_coverage": negbin_covered,
            "target_coverage": INTERVAL_LEVEL,
            "selected_noise_family": (
                "negative_binomial" if use_negative_binomial else "poisson"
            ),
        },
        "ranking_stability": ranking_summary,
        "rankings": rankings,
        "top5_disagreement": _top5_agreement(candidates),
    }
    behavior_report = _behavior_report(
        folds[-1],
        data.feature_names,
        last_models["negative_binomial"],
        last_models["gbm"],
        last_models["gbm_train"],
    )
    return metrics, candidates, summary, behavior_report


def _json_safe(value: Any) -> Any:
    """Replace numpy scalars and non-finite floats before strict JSON output."""
    import math

    import numpy as np

    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def write_artefacts(
    metrics: pd.DataFrame,
    candidates: pd.DataFrame,
    summary: dict[str, Any],
    behavior_report: dict[str, Any],
    out_dir: Path,
) -> list[Path]:
    """Write the complete local comparison and the Poisson rolling sidecar."""
    out_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = out_dir / FOLD_METRICS_FILE
    candidates_path = out_dir / CANDIDATE_PREDICTIONS_FILE
    rolling_path = out_dir / ROLLING_PREDICTIONS_FILE
    summary_path = out_dir / SUMMARY_FILE
    behavior_path = out_dir / BEHAVIOR_FILE

    metrics.to_csv(metrics_path, index=False)
    candidates.to_csv(candidates_path, index=False)
    poisson = candidates[candidates["candidate"] == "poisson"].copy()
    poisson["model_version"] = f"m1-poisson-rolling-{summary['panel_fingerprint']}"
    poisson["fit_role"] = "rolling_holdout"
    poisson[
        [
            "snowfall_event_id",
            "plow_zone",
            "model_version",
            "fit_role",
            "fold_id",
            "holdout_season",
            "predicted_count",
            "actual_count",
        ]
    ].to_csv(rolling_path, index=False)
    summary_path.write_text(
        json.dumps(_json_safe(summary), indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    behavior_path.write_text(
        json.dumps(_json_safe(behavior_report), indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return [metrics_path, candidates_path, rolling_path, summary_path, behavior_path]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel-file", type=Path, default=DEFAULT_PANEL)
    parser.add_argument("--reference-predictions", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--location-prefix", default="")
    parser.add_argument(
        "--fetch-panel",
        action="store_true",
        help="Fetch from Trino instead of reading --panel-file.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    load_cli_env()
    try:
        config = train_m1.load_config(args.config)
        raw_panel = (
            train_m1.fetch_panel(config, args.location_prefix)
            if args.fetch_panel
            else train_m1.read_panel_file(args.panel_file)
        )
        metrics, candidates, summary, behavior_report = evaluate_rolling(
            config, raw_panel, args.reference_predictions
        )
        paths = write_artefacts(
            metrics, candidates, summary, behavior_report, args.out_dir
        )
        logger.info(
            "completed %d folds x %d candidates; noise family: %s",
            summary["folds_completed"],
            summary["candidates_completed"],
            summary["distribution_family_decision"]["selected_noise_family"],
        )
        for path in paths:
            logger.info("artefact: %s", path)
    except (
        RollingRunError,
        rolling.RollingEvaluationError,
        behavior.BehaviorCheckError,
        feat.PanelError,
        train_m1.TrainingError,
        TrinoConfigError,
        OSError,
        ValueError,
    ) as error:
        logger.error("%s", error)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
