"""Boundary tests for the R13 CLI artefact writer."""

from __future__ import annotations

import datetime as dt
import json

import pytest

pd = pytest.importorskip("pandas", reason="needs the `ml` extra — run `make test-ml`")
pytest.importorskip("statsmodels", reason="needs the `ml` extra — run `make test-ml`")

from scripts.models import bootstrap_m1, train_m1  # noqa: E402


def _config(n_units: int = 3, n_events: int = 7, era_events: int = 4) -> dict:
    return {
        "model_version_prefix": "m1-poisson",
        "panel": {
            "columns": {
                "unit_id": "plow_zone",
                "event_id": "snowfall_event_id",
                "unit_size": "address_count",
                "event_start": "start_date",
                "season": "snow_season",
                "target": "request_count",
            },
            "expected_training_cells": n_units * n_events,
            "expected_prediction_cells": n_units * era_events,
        },
        "target": {"family": "poisson"},
        "features": {
            "event": ["total_snowfall_cm"],
            "calendar": ["season_index"],
            "lag": ["prev_target", "expanding_mean"],
        },
    }


def _raw(n_units: int = 3, n_events: int = 7, era_events: int = 4):
    rows = []
    for unit in range(n_units):
        for event in range(n_events):
            rows.append(
                {
                    "plow_zone": f"Z{unit}",
                    "snowfall_event_id": f"E{event:02d}",
                    "address_count": 1000 + unit * 100,
                    "start_date": dt.date(2010 + event, 12, 1),
                    "snow_season": f"{2010 + event}-{2011 + event}",
                    "request_count": 2 + event + unit,
                    "total_snowfall_cm": 4.0 + event,
                    "is_scheduling_era": event >= n_events - era_events,
                }
            )
    return pd.DataFrame(rows)


def _bootstrap_config(replicates: int = 2) -> dict:
    return {
        "replicates": replicates,
        "random_seed": 27,
        "cluster_unit": "event",
        "features_fixed": True,
        "resample_scope": "training_split",
        "expected_training_clusters": 5,
        "expected_units_per_cluster": 3,
    }


def _version(config, raw):
    role = train_m1.to_role_names(raw, config)
    return train_m1.derive_model_version(config, role, dt.date(2026, 9, 27))


def test_build_attaches_replicas_to_the_explicit_matching_version() -> None:
    config, raw = _config(), _raw()
    version = _version(config, raw)
    artefact = bootstrap_m1.build_bootstrap_artefact(
        config, _bootstrap_config(), raw, version
    )
    assert artefact.model_version == version
    assert len(artefact.predictions) == 2 * config["panel"]["expected_prediction_cells"]
    assert artefact.metadata["replicate_count_completed"] == 2
    assert artefact.metadata["cluster_unit"] == "event"
    assert artefact.metadata["features_fixed"] is True
    assert artefact.metadata["resample_scope"] == "training_split"


def test_a_version_from_another_panel_is_refused_before_bootstrapping() -> None:
    config, raw = _config(), _raw()
    with pytest.raises(bootstrap_m1.BootstrapRunError, match="fingerprint"):
        bootstrap_m1.build_bootstrap_artefact(
            config, _bootstrap_config(), raw, "m1-poisson-20260927-deadbeef"
        )


def test_non_poisson_never_silently_uses_the_poisson_fitter() -> None:
    config, raw = _config(), _raw()
    version = _version(config, raw)
    config["target"]["family"] = "negative_binomial"
    # The config change itself changes the fingerprint, so derive the matching
    # version and reach the explicit family refusal.
    version = _version(config, raw)
    with pytest.raises(bootstrap_m1.BootstrapRunError, match="no R13 refit adapter"):
        bootstrap_m1.build_bootstrap_artefact(config, _bootstrap_config(), raw, version)


def test_training_cluster_count_is_a_production_gate() -> None:
    config, raw = _config(), _raw()
    bootstrap_config = _bootstrap_config()
    bootstrap_config["expected_training_clusters"] = 91
    with pytest.raises(bootstrap_m1.BootstrapRunError, match="training universe drifted"):
        bootstrap_m1.build_bootstrap_artefact(
            config, bootstrap_config, raw, _version(config, raw)
        )


def test_writer_emits_exactly_the_three_r13_files(tmp_path) -> None:
    config, raw = _config(), _raw()
    artefact = bootstrap_m1.build_bootstrap_artefact(
        config, _bootstrap_config(), raw, _version(config, raw)
    )
    paths = bootstrap_m1.write_bootstrap_artefacts(artefact, tmp_path)
    assert {path.name for path in paths} == {
        bootstrap_m1.BOOTSTRAP_PREDICTIONS_FILE,
        bootstrap_m1.BOOTSTRAP_COEFFICIENTS_FILE,
        bootstrap_m1.BOOTSTRAP_METADATA_FILE,
    }
    metadata = json.loads(
        (paths[0].parent / bootstrap_m1.BOOTSTRAP_METADATA_FILE).read_text()
    )
    assert metadata["model_version"] == artefact.model_version


def test_config_refuses_to_make_features_variable(tmp_path) -> None:
    path = tmp_path / "bootstrap.yaml"
    path.write_text(
        "replicates: 2\nrandom_seed: 1\ncluster_unit: event\n"
        "features_fixed: false\nresample_scope: training_split\n"
        "expected_training_clusters: 5\nexpected_units_per_cluster: 3\n"
    )
    with pytest.raises(bootstrap_m1.BootstrapRunError, match="cannot be overridden"):
        bootstrap_m1.load_bootstrap_config(path)
