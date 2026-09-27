"""Generate H2-R13 event-cluster replicas for one existing M1 version.

The command writes additional immutable files beside ``predictions.csv`` and
``metrics.json``. It never creates a Gold table and never changes the base M1
artefact. A model version is required because a bootstrap describes one fitted
estimator; silently deriving a new date-stamped version would attach the result
to the wrong historical run.
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from models.request_forecast import bootstrap as boot
from models.request_forecast import features as feat
from scripts._env import load_cli_env
from scripts.ddl.apply_ddl import TrinoConfigError
from scripts.models import train_m1

if TYPE_CHECKING:  # pragma: no cover - import-time only
    import pandas as pd

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BOOTSTRAP_CONFIG_PATH = REPO_ROOT / "config" / "models" / "m1_bootstrap.yaml"

BOOTSTRAP_PREDICTIONS_FILE = "bootstrap_predictions.csv"
BOOTSTRAP_COEFFICIENTS_FILE = "bootstrap_coefficients.csv"
BOOTSTRAP_METADATA_FILE = "bootstrap_metadata.json"

ARTEFACT_PREDICTION_COLUMNS = (
    "replicate_id",
    "snowfall_event_id",
    "plow_zone",
    "predicted_mean",
)


class BootstrapRunError(RuntimeError):
    """The CLI inputs or output identity do not satisfy the R13 contract."""


@dataclass(frozen=True)
class BootstrapArtefact:
    """The three files produced for one existing model version."""

    model_version: str
    predictions: pd.DataFrame
    coefficients: pd.DataFrame
    metadata: dict[str, Any]


def load_bootstrap_config(path: Path | None = None) -> dict[str, Any]:
    import yaml

    target = path or BOOTSTRAP_CONFIG_PATH
    with target.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise BootstrapRunError(f"{target} did not parse to a mapping")

    required = {
        "replicates": int,
        "random_seed": int,
        "cluster_unit": str,
        "features_fixed": bool,
        "resample_scope": str,
        "expected_training_clusters": int,
        "expected_units_per_cluster": int,
    }
    for key, kind in required.items():
        if key not in config or not isinstance(config[key], kind):
            raise BootstrapRunError(f"{target} needs {key!r} as {kind.__name__}")
    positive = ("replicates", "expected_training_clusters", "expected_units_per_cluster")
    if any(config[key] <= 0 for key in positive):
        raise BootstrapRunError(f"bootstrap counts must be positive: {positive}")
    frozen = {
        "cluster_unit": "event",
        "features_fixed": True,
        "resample_scope": "training_split",
    }
    wrong = {key: (config[key], value) for key, value in frozen.items() if config[key] != value}
    if wrong:
        raise BootstrapRunError(f"R13 sampling invariants cannot be overridden: {wrong}")
    return config


def assert_model_version_matches(
    model_version: str, model_config: dict, role_panel: pd.DataFrame
) -> str:
    """Return the panel fingerprint after checking the explicit version."""
    prefix = model_config.get("model_version_prefix") or "m1"
    fingerprint = train_m1.panel_fingerprint(model_config, role_panel)
    if not model_version.startswith(f"{prefix}-") or not model_version.endswith(
        f"-{fingerprint}"
    ):
        raise BootstrapRunError(
            f"model_version {model_version!r} does not match prefix {prefix!r} and "
            f"panel fingerprint {fingerprint}; refusing to attach replicas to another run"
        )
    return fingerprint


def build_bootstrap_artefact(
    model_config: dict,
    bootstrap_config: dict[str, Any],
    raw_panel: pd.DataFrame,
    model_version: str,
) -> BootstrapArtefact:
    """Prepare the canonical M1 split, run R13 and attach complete metadata."""
    data = train_m1.prepare_training_data(model_config, raw_panel)
    fingerprint = assert_model_version_matches(model_version, model_config, data.panel)
    family = str(model_config.get("target", {}).get("family", "")).lower()
    if family != "poisson":
        raise BootstrapRunError(
            f"model family {family!r} has no R13 refit adapter; never fall back to Poisson"
        )

    training_events, units_per_cluster = boot.validate_event_clusters(data.split.train)
    expected_clusters = int(bootstrap_config["expected_training_clusters"])
    expected_units = int(bootstrap_config["expected_units_per_cluster"])
    if len(training_events) != expected_clusters or units_per_cluster != expected_units:
        raise BootstrapRunError(
            "R13 training universe drifted: "
            f"got {len(training_events)} event clusters x {units_per_cluster} units, "
            f"expected {expected_clusters} x {expected_units}"
        )

    scoring_panel = train_m1.scoring_era_panel(model_config, data.prepared)
    run = boot.fit_event_cluster_bootstrap(
        training_panel=data.split.train,
        prediction_panel=scoring_panel,
        feature_names=data.feature_names,
        replicate_count=int(bootstrap_config["replicates"]),
        random_seed=int(bootstrap_config["random_seed"]),
    )

    predictions = run.predictions.rename(
        columns={"event_id": "snowfall_event_id", "unit_id": "plow_zone"}
    )[list(ARTEFACT_PREDICTION_COLUMNS)]
    metadata = {
        "schema_version": 1,
        "model_version": model_version,
        "panel_fingerprint": fingerprint,
        "model_family": family,
        "holdout_season": data.split.holdout_season,
        "replicate_count_requested": int(bootstrap_config["replicates"]),
        "replicate_count_completed": len(run.replicate_seeds),
        "random_seed": int(bootstrap_config["random_seed"]),
        "replicate_seeds": list(run.replicate_seeds),
        "cluster_unit": bootstrap_config["cluster_unit"],
        "features_fixed": bootstrap_config["features_fixed"],
        "resample_scope": bootstrap_config["resample_scope"],
        "training_cluster_count": run.training_cluster_count,
        "units_per_cluster": run.units_per_cluster,
        "prediction_cell_count": run.prediction_cell_count,
        "prediction_columns": list(ARTEFACT_PREDICTION_COLUMNS),
        "coefficient_columns": list(boot.COEFFICIENT_COLUMNS),
        "feature_names": list(data.feature_names),
        "rank_tie_breaker": "predicted_rate_desc_then_plow_zone_asc",
    }
    return BootstrapArtefact(
        model_version=model_version,
        predictions=predictions,
        coefficients=run.coefficients,
        metadata=metadata,
    )


def write_bootstrap_artefacts(artefact: BootstrapArtefact, out_dir: Path) -> list[Path]:
    target = out_dir / artefact.model_version
    target.mkdir(parents=True, exist_ok=True)
    predictions_path = target / BOOTSTRAP_PREDICTIONS_FILE
    coefficients_path = target / BOOTSTRAP_COEFFICIENTS_FILE
    metadata_path = target / BOOTSTRAP_METADATA_FILE
    artefact.predictions.to_csv(predictions_path, index=False)
    artefact.coefficients.to_csv(coefficients_path, index=False)
    metadata_path.write_text(
        json.dumps(artefact.metadata, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
    return [predictions_path, coefficients_path, metadata_path]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--panel-file", type=Path)
    parser.add_argument("--config", type=Path, default=None, help="Override m1.yaml path.")
    parser.add_argument("--bootstrap-config", type=Path, default=None)
    parser.add_argument(
        "--out-dir", type=Path, default=REPO_ROOT / "var" / "forecast-runs"
    )
    parser.add_argument("--upload", action="store_true")
    parser.add_argument("--bucket", default=None, help="Overrides S3_BUCKET_NAME.")
    parser.add_argument("--location-prefix", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    import os

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    load_cli_env()
    try:
        model_config = train_m1.load_config(args.config)
        bootstrap_config = load_bootstrap_config(args.bootstrap_config)
        if args.panel_file:
            raw_panel = train_m1.read_panel_file(args.panel_file)
        else:
            raw_panel = train_m1.fetch_panel(model_config, args.location_prefix)
        artefact = build_bootstrap_artefact(
            model_config, bootstrap_config, raw_panel, args.model_version
        )
        paths = write_bootstrap_artefacts(artefact, args.out_dir)
        logger.info(
            "%s: %d replicas x %d cells",
            artefact.model_version,
            artefact.metadata["replicate_count_completed"],
            artefact.metadata["prediction_cell_count"],
        )
        if args.upload:
            bucket = args.bucket or os.environ.get("S3_BUCKET_NAME")
            if not bucket:
                raise BootstrapRunError("--upload needs --bucket or S3_BUCKET_NAME")
            train_m1.upload_artefacts(paths, artefact.model_version, bucket)
    except (
        BootstrapRunError,
        boot.BootstrapError,
        feat.PanelError,
        train_m1.TrainingError,
        TrinoConfigError,
    ) as error:
        logger.error("%s", error)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
