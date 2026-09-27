"""Run H2-R3 common-sense and extrapolation checks for one M1 version.

The output is descriptive, not a release gate. A behavioral finding is written
to the artefact and exits successfully; malformed inputs, ambiguous versions
and mismatched panels fail before any result is reported.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from models.request_forecast import behavior, outlook
from models.request_forecast import features as feat
from models.request_forecast import model as mdl
from scripts._env import load_cli_env
from scripts.ddl.apply_ddl import TrinoConfigError
from scripts.models import train_m1

if TYPE_CHECKING:  # pragma: no cover - import-time only
    import pandas as pd

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "models" / "m1_behavior.yaml"
BEHAVIOR_FILE = "behavior_checks.json"


class BehaviorRunError(RuntimeError):
    """An input cannot be tied unambiguously to the requested model run."""


def load_behavior_config(path: Path | None = None) -> dict[str, Any]:
    """Load R3's pre-registered parameters and reject drift from the design."""
    import yaml

    target = path or CONFIG_PATH
    with target.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise BehaviorRunError(f"{target} did not parse to a mapping")

    monotonicity = config.get("monotonicity")
    extrapolation = config.get("extrapolation")
    expected_checks = [
        {
            "feature": "total_snowfall_cm",
            "input_order": "increasing",
            "expected_prediction": "nondecreasing",
        },
        {
            "feature": "min_temperature_c",
            "input_order": "decreasing",
            "expected_prediction": "nondecreasing",
        },
    ]
    frozen = {
        "anchor_scope": (config.get("anchor_scope"), "training_split"),
        "other_features_fixed": (config.get("other_features_fixed"), True),
        "monotonicity.grid_points": (
            monotonicity.get("grid_points") if isinstance(monotonicity, dict) else None,
            9,
        ),
        "monotonicity.relative_tolerance": (
            monotonicity.get("relative_tolerance") if isinstance(monotonicity, dict) else None,
            1.0e-12,
        ),
        "monotonicity.checks": (
            monotonicity.get("checks") if isinstance(monotonicity, dict) else None,
            expected_checks,
        ),
        "extrapolation.feature": (
            extrapolation.get("feature") if isinstance(extrapolation, dict) else None,
            "total_snowfall_cm",
        ),
        "extrapolation.training_max_multiplier": (
            extrapolation.get("training_max_multiplier")
            if isinstance(extrapolation, dict)
            else None,
            2.0,
        ),
        "extrapolation.explosion_ratio": (
            extrapolation.get("explosion_ratio") if isinstance(extrapolation, dict) else None,
            100.0,
        ),
    }
    wrong = {name: values for name, values in frozen.items() if values[0] != values[1]}
    if wrong:
        raise BehaviorRunError(f"R3 pre-registered parameters cannot drift: {wrong}")
    return config


def build_behavior_report(
    model_config: dict[str, Any],
    behavior_config: dict[str, Any],
    raw_panel: pd.DataFrame,
    metrics: dict[str, Any],
    model_version: str,
) -> dict[str, Any]:
    """Build a deterministic R3 report without reading or writing external state."""
    panel = train_m1.to_role_names(raw_panel, model_config)
    expected_cells = model_config.get("panel", {}).get("expected_training_cells")
    if expected_cells is not None and len(panel) != int(expected_cells):
        raise BehaviorRunError(
            f"training panel has {len(panel)} cells, config expects {expected_cells}"
        )
    fingerprint = train_m1.panel_fingerprint(model_config, panel)
    _assert_model_identity(metrics, model_version, fingerprint)

    prepared = feat.drop_rows_without_history(feat.build_panel_features(panel))
    split = mdl.split_holdout_last_season(prepared)
    mdl.assert_no_history_leak(split.train, split.test)
    names = feat.feature_names(model_config)
    coefficients = metrics.get("coefficients")
    if not isinstance(coefficients, dict):
        raise BehaviorRunError("metrics.json has no coefficient mapping")
    expected_terms = ["const", *names]
    if list(coefficients) != expected_terms:
        raise BehaviorRunError(
            f"coefficient terms {list(coefficients)} do not match fitted features {expected_terms}"
        )

    mono_config = behavior_config["monotonicity"]
    monotonicity = [
        behavior.check_monotonicity(
            split.train,
            coefficients,
            names,
            feature=check["feature"],
            input_order=check["input_order"],
            grid_points=int(mono_config["grid_points"]),
            relative_tolerance=float(mono_config["relative_tolerance"]),
        )
        for check in mono_config["checks"]
    ]
    ext_config = behavior_config["extrapolation"]
    extrapolation = behavior.check_extrapolation(
        split.train,
        coefficients,
        names,
        feature=ext_config["feature"],
        training_max_multiplier=float(ext_config["training_max_multiplier"]),
        explosion_ratio=float(ext_config["explosion_ratio"]),
    )

    findings = [
        f"{result.feature} monotonicity: {result.violations} violation(s), "
        f"{result.non_finite_predictions} non-finite prediction(s)"
        for result in monotonicity
        if result.has_finding
    ]
    if extrapolation.has_finding:
        findings.append(
            f"{extrapolation.feature} extrapolation: maximum response ratio "
            f"{extrapolation.max_response_ratio!r}, "
            f"{extrapolation.non_finite_or_non_positive_predictions} invalid prediction pair(s)"
        )

    family = str(model_config.get("target", {}).get("family", "")).lower()
    return {
        "schema_version": 1,
        "model_version": model_version,
        "model_family": family,
        "panel_fingerprint": fingerprint,
        "holdout_season": split.holdout_season,
        "anchor_scope": behavior_config["anchor_scope"],
        "anchor_rows": len(split.train),
        "other_features_fixed": behavior_config["other_features_fixed"],
        "synthetic_inputs_are_accuracy_evidence": False,
        "status": "findings" if findings else "no_findings",
        "findings": findings,
        "monotonicity": [
            {**asdict(result), "has_finding": result.has_finding} for result in monotonicity
        ],
        "extrapolation": {
            **asdict(extrapolation),
            "has_finding": extrapolation.has_finding,
        },
    }


def _assert_model_identity(
    metrics: dict[str, Any], model_version: str, panel_fingerprint: str
) -> None:
    if metrics.get("model_version") != model_version:
        raise BehaviorRunError(
            f"metrics are for {metrics.get('model_version')!r}, not {model_version!r}"
        )
    if metrics.get("panel_fingerprint") != panel_fingerprint:
        raise BehaviorRunError(
            f"metrics panel fingerprint {metrics.get('panel_fingerprint')!r} does not match "
            f"input panel {panel_fingerprint!r}"
        )


def write_behavior_report(report: dict[str, Any], out_dir: Path) -> Path:
    """Write the deterministic report beside the selected model artefact."""
    target = out_dir / report["model_version"]
    target.mkdir(parents=True, exist_ok=True)
    path = target / BEHAVIOR_FILE
    path.write_text(
        json.dumps(report, indent=2, sort_keys=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return path


def _read_metrics(path: Path) -> dict[str, Any]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise BehaviorRunError(f"{path} did not parse to a mapping")
    return loaded


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--panel-file", type=Path)
    parser.add_argument("--metrics-file", type=Path)
    parser.add_argument("--config", type=Path, default=None, help="Override m1.yaml path.")
    parser.add_argument("--behavior-config", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=REPO_ROOT / "var" / "forecast-runs")
    parser.add_argument("--upload", action="store_true")
    parser.add_argument("--bucket", default=None, help="Overrides S3_BUCKET_NAME.")
    parser.add_argument("--location-prefix", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    load_cli_env()
    try:
        model_config = train_m1.load_config(args.config)
        behavior_config = load_behavior_config(args.behavior_config)
        raw_panel = (
            train_m1.read_panel_file(args.panel_file)
            if args.panel_file
            else train_m1.fetch_panel(model_config, args.location_prefix)
        )
        if args.metrics_file:
            metrics = _read_metrics(args.metrics_file)
        else:
            from scripts.models.outlook_m1 import fetch_metrics

            bucket = args.bucket or os.environ.get("S3_BUCKET_NAME")
            if not bucket:
                raise BehaviorRunError(
                    "reading metrics from object storage needs --bucket or S3_BUCKET_NAME"
                )
            metrics = fetch_metrics(args.model_version, bucket)
        report = build_behavior_report(
            model_config, behavior_config, raw_panel, metrics, args.model_version
        )
        path = write_behavior_report(report, args.out_dir)
        logger.info(
            "%s: %s (%d finding(s))",
            args.model_version,
            report["status"],
            len(report["findings"]),
        )
        logger.info("artefact: %s", path)
        if args.upload:
            bucket = args.bucket or os.environ.get("S3_BUCKET_NAME")
            if not bucket:
                raise BehaviorRunError("--upload needs --bucket or S3_BUCKET_NAME")
            train_m1.upload_artefacts([path], args.model_version, bucket)
    except (
        BehaviorRunError,
        behavior.BehaviorCheckError,
        feat.PanelError,
        outlook.OutlookError,
        train_m1.TrainingError,
        TrinoConfigError,
        OSError,
        json.JSONDecodeError,
    ) as error:
        logger.error("%s", error)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
