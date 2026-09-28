"""Fold H2-R13 replicas into the compact uncertainty payload R6 consumes.

This is an offline freeze step, not a request-time calculation. The raw long
table remains the reusable record; this summary is the page-sized derivative
for one explicitly selected model version.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from scripts.models.bootstrap_m1 import (
    ARTEFACT_PREDICTION_COLUMNS,
    BOOTSTRAP_METADATA_FILE,
    BOOTSTRAP_PREDICTIONS_FILE,
)
from scripts.presentation.demand_plan import DEMAND_PLAN_FIG

if TYPE_CHECKING:  # pragma: no cover - import-time only
    import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "presentation" / "demand_plan.yaml"
UNCERTAINTY_FILE = f"{DEMAND_PLAN_FIG}-uncertainty.json"


class UncertaintyError(ValueError):
    """The replicas, panel or preregistered settings disagree."""


def load_config(path: Path | None = None) -> dict[str, Any]:
    import yaml

    target = path or CONFIG_PATH
    with target.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise UncertaintyError(f"{target} did not parse to a mapping")
    return config


def _panel_frame(payload: dict[str, Any], model_version: str) -> pd.DataFrame:
    import pandas as pd

    if payload.get("fig_id") != DEMAND_PLAN_FIG:
        raise UncertaintyError(
            f"panel carries fig_id {payload.get('fig_id')!r}, expected {DEMAND_PLAN_FIG}"
        )
    frame = pd.DataFrame(payload["rows"], columns=payload["columns"])
    frame = frame[frame["model_version"] == model_version].copy()
    if frame.empty:
        raise UncertaintyError(f"panel has no rows for model_version {model_version!r}")
    key = ["snowfall_event_id", "plow_zone"]
    if bool(frame.duplicated(key).any()):
        raise UncertaintyError("panel has duplicate (snowfall_event_id, plow_zone) cells")
    numeric = ["address_count", "predicted_count", "actual_count", "shift_number"]
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if bool(frame["address_count"].isna().any()) or bool((frame["address_count"] <= 0).any()):
        raise UncertaintyError("panel contains a null or non-positive address_count")
    return frame


def _validate_inputs(
    panel: pd.DataFrame,
    replicas: pd.DataFrame,
    metadata: dict[str, Any],
    model_version: str,
) -> int:
    import numpy as np
    import pandas as pd

    if metadata.get("model_version") != model_version:
        raise UncertaintyError(
            f"bootstrap metadata belongs to {metadata.get('model_version')!r}, "
            f"not {model_version!r}"
        )
    invariants = {
        "cluster_unit": "event",
        "features_fixed": True,
        "resample_scope": "training_split",
    }
    wrong = {
        key: (metadata.get(key), expected)
        for key, expected in invariants.items()
        if metadata.get(key) != expected
    }
    if wrong:
        raise UncertaintyError(f"bootstrap metadata violates R13 invariants: {wrong}")
    if list(replicas.columns) != list(ARTEFACT_PREDICTION_COLUMNS):
        raise UncertaintyError(
            f"bootstrap columns {list(replicas.columns)} do not match "
            f"{list(ARTEFACT_PREDICTION_COLUMNS)}"
        )
    declared_columns = metadata.get("prediction_columns")
    if declared_columns != list(ARTEFACT_PREDICTION_COLUMNS):
        raise UncertaintyError(
            f"bootstrap metadata declares prediction columns {declared_columns!r}, "
            f"expected {list(ARTEFACT_PREDICTION_COLUMNS)}"
        )
    replicate_ids = pd.to_numeric(replicas["replicate_id"], errors="raise")
    if not np.isfinite(replicate_ids.to_numpy()).all() or bool(
        (replicate_ids % 1 != 0).any()
    ):
        raise UncertaintyError("replicate_id must contain finite whole numbers")
    replicas["replicate_id"] = replicate_ids.astype(int)
    replicas["predicted_mean"] = pd.to_numeric(
        replicas["predicted_mean"], errors="raise"
    ).astype(float)
    key = ["replicate_id", "snowfall_event_id", "plow_zone"]
    if bool(replicas.duplicated(key).any()):
        raise UncertaintyError("bootstrap predictions contain duplicate replica cells")
    if not np.isfinite(replicas["predicted_mean"].to_numpy()).all() or bool(
        (replicas["predicted_mean"] < 0).any()
    ):
        raise UncertaintyError("bootstrap predictions contain a non-finite or negative mean")

    completed = int(metadata["replicate_count_completed"])
    requested = int(metadata["replicate_count_requested"])
    if completed <= 0 or requested != completed:
        raise UncertaintyError(
            f"bootstrap metadata reports {completed} completed of {requested} requested; "
            "partial bootstrap runs are not valid"
        )
    ids = sorted(replicas["replicate_id"].unique())
    if ids != list(range(1, completed + 1)):
        raise UncertaintyError(f"replicate IDs are {ids[:5]}...; expected 1..{completed}")
    expected_cells = set(map(tuple, panel[["snowfall_event_id", "plow_zone"]].to_numpy()))
    for replicate_id, group in replicas.groupby("replicate_id", sort=False):
        cells = set(map(tuple, group[["snowfall_event_id", "plow_zone"]].to_numpy()))
        if cells != expected_cells:
            raise UncertaintyError(
                f"replicate {replicate_id} does not cover the frozen panel exactly"
            )
    expected_rows = completed * len(panel)
    if len(replicas) != expected_rows:
        raise UncertaintyError(
            f"bootstrap predictions have {len(replicas)} rows, expected {expected_rows}"
        )
    if int(metadata.get("prediction_cell_count", -1)) != len(panel):
        raise UncertaintyError(
            f"bootstrap metadata reports {metadata.get('prediction_cell_count')!r} "
            f"prediction cells, frozen panel has {len(panel)}"
        )
    return completed


def _noise(config: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    """The count-noise family laid over the replica means, from the preregistered config.

    🔴 This is deliberately **not** `metadata["model_family"]`: that names the
    *mean* model the replicas were fitted with (Poisson GLM), while R1+R2 chose
    the *noise* family separately (negative binomial, keeping the Poisson mean;
    R1+R2 design §8 item 3). Reading one for the other is how a Poisson interval
    would silently survive the R1 verdict.
    """
    noise = config.get("noise")
    if not isinstance(noise, dict) or "family" not in noise:
        raise UncertaintyError("config has no noise.family; the interval family must be explicit")
    family = str(noise["family"]).lower()
    if family != "poisson":
        # An alpha is a fitted number: it belongs to one panel. Refuse one taken
        # from a different panel than the replicas were drawn on.
        expected = noise.get("panel_fingerprint")
        actual = metadata.get("panel_fingerprint")
        if expected is None or expected != actual:
            raise UncertaintyError(
                f"noise.panel_fingerprint {expected!r} does not match the bootstrap "
                f"panel {actual!r}"
            )
    return noise


def _predictive_draws(
    means: Any, noise: dict[str, Any], seed: int
) -> Any:
    import numpy as np

    rng = np.random.default_rng(seed)
    family = str(noise.get("family", "")).lower()
    if family == "poisson":
        return rng.poisson(means)
    if family in {"negative_binomial", "negative-binomial", "nb2"}:
        alpha = noise.get("dispersion_alpha")
        if alpha is None or float(alpha) <= 0:
            raise UncertaintyError(
                "negative-binomial predictive intervals require a positive dispersion_alpha"
            )
        size = 1.0 / float(alpha)
        probability = size / (size + means)
        return rng.negative_binomial(size, probability)
    raise UncertaintyError(
        f"model family {family!r} has no predictive-count sampler; no fallback is allowed"
    )


def build_uncertainty_summary(
    panel_payload: dict[str, Any],
    replicas: pd.DataFrame,
    metadata: dict[str, Any],
    config: dict[str, Any],
    model_version: str,
) -> dict[str, Any]:
    """Calculate intervals, top-K probabilities, prompts and sensitivity."""
    panel = _panel_frame(panel_payload, model_version)
    completed = _validate_inputs(panel, replicas, metadata, model_version)
    noise = _noise(config, metadata)

    interval = config["interval"]
    level = float(interval["level"])
    if not 0 < level < 1:
        raise UncertaintyError(f"interval level must be between 0 and 1, got {level}")
    lower_q = (1.0 - level) / 2.0
    upper_q = 1.0 - lower_q

    ranking = config["ranking"]
    top_k_values = sorted({int(k) for k in ranking["top_k_values"]})
    if not top_k_values or min(top_k_values) <= 0:
        raise UncertaintyError("ranking.top_k_values must contain positive integers")
    if ranking.get("tie_breaker") != "plow_zone_ascending":
        raise UncertaintyError("the registered tie breaker is plow_zone_ascending")

    address = panel[["snowfall_event_id", "plow_zone", "address_count"]]
    ranked = replicas.merge(
        address, on=["snowfall_event_id", "plow_zone"], how="left", validate="many_to_one"
    )
    ranked["predicted_rate"] = ranked["predicted_mean"] / ranked["address_count"] * 1000.0
    ranked = ranked.sort_values(
        ["replicate_id", "snowfall_event_id", "predicted_rate", "plow_zone"],
        ascending=[True, True, False, True],
        kind="stable",
    )
    ranked["rank"] = ranked.groupby(
        ["replicate_id", "snowfall_event_id"], sort=False
    ).cumcount() + 1
    for top_k in top_k_values:
        ranked[f"top_{top_k}"] = ranked["rank"] <= top_k

    ranked = ranked.sort_values(
        ["replicate_id", "snowfall_event_id", "plow_zone"], kind="stable"
    ).reset_index(drop=True)
    ranked["predictive_count"] = _predictive_draws(
        ranked["predicted_mean"].to_numpy(),
        noise,
        int(interval["predictive_seed"]),
    )
    group_key = ["snowfall_event_id", "plow_zone"]
    aggregate = {
        "mean_low": ("predicted_mean", lambda s: float(s.quantile(lower_q))),
        "mean_high": ("predicted_mean", lambda s: float(s.quantile(upper_q))),
        "prediction_low": ("predictive_count", lambda s: float(s.quantile(lower_q))),
        "prediction_high": ("predictive_count", lambda s: float(s.quantile(upper_q))),
    }
    for top_k in top_k_values:
        aggregate[f"top_{top_k}_probability"] = (f"top_{top_k}", "mean")
    stats = ranked.groupby(group_key, sort=False).agg(**aggregate).reset_index()

    panel["predicted_rate"] = panel["predicted_count"] / panel["address_count"] * 1000.0
    panel = panel.sort_values(
        ["snowfall_event_id", "predicted_rate", "plow_zone"],
        ascending=[True, False, True],
        kind="stable",
    )
    panel["point_rank"] = panel.groupby("snowfall_event_id", sort=False).cumcount() + 1
    combined = panel.merge(stats, on=group_key, how="left", validate="one_to_one")

    default = config["review_prompt"]
    default_k = int(default["top_k"])
    default_shift = int(default["minimum_shift"])
    default_stability = float(default["minimum_stability"])
    if default_k not in top_k_values:
        raise UncertaintyError("review_prompt.top_k must be present in ranking.top_k_values")
    probability_column = f"top_{default_k}_probability"
    combined["prompt"] = (
        (combined["fit_role"] == "holdout")
        & combined["shift_number"].notna()
        & (combined["point_rank"] <= default_k)
        & (combined["shift_number"] >= default_shift)
        & (combined[probability_column] >= default_stability)
    )

    cell_rows: list[dict[str, Any]] = []
    for row in combined.sort_values(group_key, kind="stable").to_dict("records"):
        cell_rows.append(
            {
                "snowfall_event_id": row["snowfall_event_id"],
                "plow_zone": row["plow_zone"],
                "mean_low": row["mean_low"],
                "mean_high": row["mean_high"],
                "prediction_low": row["prediction_low"],
                "prediction_high": row["prediction_high"],
                "predicted_rate_per_1000": row["predicted_rate"],
                "point_rank": int(row["point_rank"]),
                "top_k_probabilities": {
                    str(k): float(row[f"top_{k}_probability"]) for k in top_k_values
                },
                "prompt": bool(row["prompt"]),
            }
        )

    sensitivity = config["sensitivity"]
    grid: list[dict[str, Any]] = []
    grid_sets: list[set[str]] = []
    for top_k in sensitivity["top_k_values"]:
        top_k = int(top_k)
        probability = f"top_{top_k}_probability"
        if probability not in combined:
            raise UncertaintyError(f"sensitivity K={top_k} was not calculated")
        for minimum_shift in sensitivity["minimum_shifts"]:
            for minimum_stability in sensitivity["minimum_stabilities"]:
                selected = combined[
                    (combined["fit_role"] == "holdout")
                    & combined["shift_number"].notna()
                    & (combined["point_rank"] <= top_k)
                    & (combined["shift_number"] >= int(minimum_shift))
                    & (combined[probability] >= float(minimum_stability))
                ]
                keys = {
                    f"{event_id}|{zone}"
                    for event_id, zone in selected[group_key].itertuples(index=False, name=None)
                }
                grid_sets.append(keys)
                grid.append(
                    {
                        "top_k": top_k,
                        "minimum_shift": int(minimum_shift),
                        "minimum_stability": float(minimum_stability),
                        "prompt_count": len(keys),
                    }
                )
    robust = sorted(set.intersection(*grid_sets) if grid_sets else set())

    holdout = combined[(combined["fit_role"] == "holdout") & combined["actual_count"].notna()]
    covered = (
        (holdout["actual_count"] >= holdout["prediction_low"])
        & (holdout["actual_count"] <= holdout["prediction_high"])
    )
    # Per event, because one storm can carry most of the shortfall (R6 B batch,
    # 2026-09-27: 19 of 34 misses in SNOW-20260312). Width is reported with it:
    # a family that covers by being wide is not informative (R1+R2 launch §7.4).
    holdout = holdout.assign(
        covered=covered,
        above=holdout["actual_count"] > holdout["prediction_high"],
        below=holdout["actual_count"] < holdout["prediction_low"],
        width=holdout["prediction_high"] - holdout["prediction_low"],
    )
    by_event = [
        {
            "snowfall_event_id": event_id,
            "n_cells": int(len(group)),
            "covered_cells": int(group["covered"].sum()),
            "above": int(group["above"].sum()),
            "below": int(group["below"].sum()),
            "median_width": float(group["width"].median()),
        }
        for event_id, group in holdout.groupby("snowfall_event_id", sort=True)
    ]
    return {
        "schema_version": 1,
        "model_version": model_version,
        "replicate_count": completed,
        "bootstrap_seed": int(metadata["random_seed"]),
        "predictive_seed": int(interval["predictive_seed"]),
        "cluster_unit": metadata["cluster_unit"],
        "features_fixed": bool(metadata["features_fixed"]),
        "model_family": metadata["model_family"],
        "noise": {
            key: noise[key]
            for key in ("family", "dispersion_alpha", "source", "panel_fingerprint")
            if key in noise
        },
        "interval": {
            "kind": "request_count_prediction",
            "level": level,
            "lower_quantile": lower_q,
            "upper_quantile": upper_q,
        },
        "ranking": {
            "top_k_values": top_k_values,
            "tie_breaker": "predicted_rate_desc_then_plow_zone_asc",
        },
        "review_prompt": {
            "top_k": default_k,
            "minimum_shift": default_shift,
            "minimum_stability": default_stability,
            "prompt_count": int(combined["prompt"].sum()),
            "in_sample_prompt_count": int(
                combined.loc[combined["fit_role"] != "holdout", "prompt"].sum()
            ),
        },
        "coverage": {
            "scope": "holdout",
            "n_cells": int(len(holdout)),
            "covered_cells": int(covered.sum()),
            "median_width": None if holdout.empty else float(holdout["width"].median()),
            "by_event": by_event,
            "rate": None if holdout.empty else float(covered.mean()),
        },
        "sensitivity_grid": grid,
        "robust_prompt_cells": robust,
        "cells": cell_rows,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", type=Path, required=True, help="Frozen FIG-BO8-03 JSON.")
    parser.add_argument("--bootstrap-dir", type=Path, required=True)
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    import pandas as pd

    args = build_parser().parse_args(argv)
    panel = json.loads(args.panel.read_text(encoding="utf-8"))
    metadata = json.loads(
        (args.bootstrap_dir / BOOTSTRAP_METADATA_FILE).read_text(encoding="utf-8")
    )
    replicas = pd.read_csv(args.bootstrap_dir / BOOTSTRAP_PREDICTIONS_FILE)
    summary = build_uncertainty_summary(
        panel, replicas, metadata, load_config(args.config), args.model_version
    )
    target = args.out or args.panel.with_name(UNCERTAINTY_FILE)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"demand uncertainty: {len(summary['cells'])} cells -> {target}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
