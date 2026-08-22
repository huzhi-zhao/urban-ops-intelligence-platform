"""Unit tests for M1's orchestration layer — scripts/models/train_m1.py.

Skipped unless the `ml` extra is installed; see `make test-ml`.

The two modules under `models/request_forecast/` are already covered on
synthetic panels. What is *only* testable here is the part that crosses
boundaries: the city-column rename, the SQL the panel query builds, the row
gates, the artefact's shape, and the version-identity rule the whole
version-preservation design (§5) rests on.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

pd = pytest.importorskip("pandas", reason="needs the `ml` extra — run `make test-ml`")
pytest.importorskip("statsmodels", reason="needs the `ml` extra — run `make test-ml`")

from scripts.models import train_m1  # noqa: E402

CITY = {
    "unit_id": "plow_zone",
    "event_id": "snowfall_event_id",
    "unit_size": "address_count",
    "event_start": "start_date",
    "season": "snow_season",
    "target": "request_count",
}


def make_config(n_units: int = 3, n_events: int = 6, era_events: int = 4) -> dict:
    return {
        "model_version_prefix": "m1-poisson",
        "panel": {
            "columns": dict(CITY),
            "expected_training_cells": n_units * n_events,
            "expected_prediction_cells": n_units * era_events,
        },
        "features": {
            "event": [
                "total_snowfall_cm",
                "peak_daily_snowfall_cm",
                "duration_days",
                "min_temperature_c",
                "accum_flag",
                "severity_score",
            ],
            "calendar": ["season_index", "month"],
            "lag": ["prev_target", "expanding_mean"],
        },
        "random_seed": 1,
    }


def make_raw_panel(n_units: int = 3, n_events: int = 6, era_events: int = 4) -> pd.DataFrame:
    """A panel in **city** column names, as Trino would return it.

    The last `era_events` events are the scheduling era, mirroring the real
    shape where the era is a recent suffix of the history, never a random
    subset.
    """
    rows = []
    for u in range(n_units):
        for e in range(n_events):
            rows.append(
                {
                    "plow_zone": f"Z{u}",
                    "snowfall_event_id": f"E{e:02d}",
                    "address_count": 1000 * (u + 1),
                    "start_date": dt.date(2010 + e, 12, 1),
                    "snow_season": f"{2010 + e}-{2011 + e}",
                    "is_scheduling_era": e >= (n_events - era_events),
                    "total_snowfall_cm": 5.0 + e,
                    "peak_daily_snowfall_cm": 3.0 + e * 0.5,
                    "duration_days": 1 + (e % 2),
                    "min_temperature_c": -10.0 - e,
                    "accum_flag": e % 2 == 0,
                    "severity_score": 0.1 * e,
                    "request_count": 10 + 3 * e + 2 * u,
                }
            )
    return pd.DataFrame(rows)


# ── the city boundary ─────────────────────────────────────────────────────────


def test_to_role_names_renames_every_city_column() -> None:
    config = make_config()
    roles = train_m1.to_role_names(make_raw_panel(), config)
    for role in CITY:
        assert role in roles.columns
    # The city spellings are gone, so nothing downstream can accidentally use one.
    assert "plow_zone" not in roles.columns
    assert "snowfall_event_id" not in roles.columns


def test_to_role_names_names_the_missing_column_rather_than_keyerror() -> None:
    config = make_config()
    raw = make_raw_panel().drop(columns=["address_count"])
    with pytest.raises(train_m1.TrainingError, match="address_count"):
        train_m1.to_role_names(raw, config)


def test_column_map_rejects_a_config_missing_a_role() -> None:
    config = make_config()
    del config["panel"]["columns"]["unit_size"]
    with pytest.raises(train_m1.TrainingError, match="unit_size"):
        train_m1.column_map(config)


def test_event_start_becomes_comparable_datetimes() -> None:
    roles = train_m1.to_role_names(make_raw_panel(), make_config())
    assert pd.api.types.is_datetime64_any_dtype(roles["event_start"])


# ── the panel query ───────────────────────────────────────────────────────────


def test_panel_sql_has_no_select_star_and_no_date_literal() -> None:
    sql = train_m1.panel_sql(make_config(), "uoip_gold")
    assert "SELECT *" not in sql.upper()
    assert "2015-" not in sql and "2026-" not in sql


def test_panel_sql_groups_by_the_panel_grain_so_categories_collapse() -> None:
    sql = train_m1.panel_sql(make_config(), "uoip_gold")
    assert "SUM(f.request_count)" in sql
    assert "GROUP BY" in sql
    # winter_category must NOT survive into the panel: it is the dimension the
    # sum collapses, and keeping it would double the grain.
    assert "winter_category" not in sql


def test_panel_sql_qualifies_every_table_with_the_gold_schema() -> None:
    sql = train_m1.panel_sql(make_config(), "uoip_gold_smoke")
    for table in (
        "fact_service_request_zone_event",
        "dim_snowfall_event",
        "dim_plow_zone",
    ):
        assert f"uoip_gold_smoke.{table}" in sql


def test_panel_sql_carries_every_configured_event_feature() -> None:
    config = make_config()
    sql = train_m1.panel_sql(config, "uoip_gold")
    for name in config["features"]["event"]:
        assert f"e.{name}" in sql


# ── gates ─────────────────────────────────────────────────────────────────────


def test_gate_a1_fails_when_the_panel_is_not_the_expected_size() -> None:
    config = make_config()
    config["panel"]["expected_training_cells"] = 9999
    with pytest.raises(train_m1.TrainingError, match="gate a1"):
        train_m1.train(config, make_raw_panel(), dt.date(2026, 8, 21))


def test_gate_a1_error_points_at_o4_rather_than_at_the_code() -> None:
    config = make_config()
    config["panel"]["expected_training_cells"] = 9999
    with pytest.raises(train_m1.TrainingError, match="O4"):
        train_m1.train(config, make_raw_panel(), dt.date(2026, 8, 21))


def test_gate_a2_fails_when_the_scheduling_era_subset_is_not_the_expected_size() -> None:
    config = make_config()
    config["panel"]["expected_prediction_cells"] = 7
    with pytest.raises(train_m1.TrainingError, match="gate a2"):
        train_m1.train(config, make_raw_panel(), dt.date(2026, 8, 21))


# ── a full run ────────────────────────────────────────────────────────────────


@pytest.fixture
def run() -> train_m1.TrainingRun:
    return train_m1.train(make_config(), make_raw_panel(), dt.date(2026, 8, 21))


def test_predictions_carry_exactly_f5s_columns(run: train_m1.TrainingRun) -> None:
    assert list(run.predictions.columns) == [
        "snowfall_event_id",
        "plow_zone",
        "model_version",
        "predicted_count",
        "baseline_count",
        "actual_count",
    ]


def test_predictions_cover_the_scheduling_era_only(run: train_m1.TrainingRun) -> None:
    assert len(run.predictions) == make_config()["panel"]["expected_prediction_cells"]


def test_predictions_are_unique_on_f5s_primary_key(run: train_m1.TrainingRun) -> None:
    key = ["snowfall_event_id", "plow_zone", "model_version"]
    assert not run.predictions.duplicated(subset=key).any()


def test_predicted_counts_are_never_negative(run: train_m1.TrainingRun) -> None:
    assert (run.predictions["predicted_count"] >= 0).all()


def test_evaluation_reports_model_and_baseline_together(run: train_m1.TrainingRun) -> None:
    summary = run.evaluation.summary()
    assert "model MAE" in summary
    assert "seasonal-naive MAE" in summary


def test_a_losing_model_does_not_raise(run: train_m1.TrainingRun) -> None:
    """Gate a4: the model failing to beat the baseline is reported, not fatal."""
    assert isinstance(run.evaluation.beats_baseline, bool)


def test_the_forbidden_rank_features_are_absent_from_the_coefficients(
    run: train_m1.TrainingRun,
) -> None:
    for banned in ("shift_number", "rank_factor", "rank"):
        assert banned not in run.coefficients


# ── version identity: the whole of §5 rests on this ───────────────────────────


def test_same_config_and_panel_give_the_same_model_version() -> None:
    """Gate a6 — a rerun on unchanged inputs rewrites the same artefact."""
    config, panel = make_config(), make_raw_panel()
    day = dt.date(2026, 8, 21)
    first = train_m1.train(config, panel, day)
    second = train_m1.train(config, panel, day)
    assert first.model_version == second.model_version


def test_row_order_does_not_change_the_model_version() -> None:
    config = make_config()
    day = dt.date(2026, 8, 21)
    panel = make_raw_panel()
    shuffled = panel.iloc[::-1].reset_index(drop=True)
    assert (
        train_m1.train(config, panel, day).model_version
        == train_m1.train(config, shuffled, day).model_version
    )


def test_a_changed_panel_gives_a_different_model_version() -> None:
    """So a retrain cannot silently overwrite an earlier backtest's predictions."""
    config = make_config()
    day = dt.date(2026, 8, 21)
    panel = make_raw_panel()
    changed = panel.copy()
    changed.loc[0, "request_count"] = int(changed.loc[0, "request_count"]) + 1
    assert (
        train_m1.train(config, panel, day).model_version
        != train_m1.train(config, changed, day).model_version
    )


def test_a_changed_feature_list_gives_a_different_model_version() -> None:
    day = dt.date(2026, 8, 21)
    panel = make_raw_panel()
    baseline = train_m1.train(make_config(), panel, day)
    other = make_config()
    other["features"]["event"] = other["features"]["event"][:-1]
    assert train_m1.train(other, panel, day).model_version != baseline.model_version


def test_model_version_carries_the_prefix_and_the_date() -> None:
    run = train_m1.train(make_config(), make_raw_panel(), dt.date(2026, 8, 21))
    assert run.model_version.startswith("m1-poisson-20260821-")


# ── artefact ──────────────────────────────────────────────────────────────────


def test_write_artefacts_lands_both_files_under_the_version(tmp_path, run) -> None:
    paths = train_m1.write_artefacts(run, tmp_path)
    assert {p.name for p in paths} == {"predictions.csv", "metrics.json"}
    assert all(p.parent.name == run.model_version for p in paths)


def test_two_versions_coexist_rather_than_overwrite(tmp_path) -> None:
    """A3b in miniature: the artefact root accumulates, it never replaces."""
    config, day = make_config(), dt.date(2026, 8, 21)
    panel = make_raw_panel()
    changed = panel.copy()
    changed.loc[0, "request_count"] = int(changed.loc[0, "request_count"]) + 1

    train_m1.write_artefacts(train_m1.train(config, panel, day), tmp_path)
    train_m1.write_artefacts(train_m1.train(config, changed, day), tmp_path)

    assert len(list(tmp_path.iterdir())) == 2


def test_metrics_json_states_both_methods_and_the_verdict(tmp_path, run) -> None:
    train_m1.write_artefacts(run, tmp_path)
    payload = json.loads((tmp_path / run.model_version / "metrics.json").read_text())
    assert payload["model"]["mae"] >= 0
    assert payload["baseline"]["mae"] >= 0
    assert payload["model"]["n_rows"] == payload["baseline"]["n_rows"]
    assert isinstance(payload["beats_baseline"], bool)
    assert payload["model_version"] == run.model_version


def test_metrics_json_records_the_baseline_method_not_just_its_score(tmp_path, run) -> None:
    """§4.3 settled on the causal expanding mean; the artefact must say so."""
    train_m1.write_artefacts(run, tmp_path)
    payload = json.loads((tmp_path / run.model_version / "metrics.json").read_text())
    assert "expanding" in payload["baseline"]["method"]


def test_predictions_csv_round_trips_to_the_same_rows(tmp_path, run) -> None:
    paths = train_m1.write_artefacts(run, tmp_path)
    reloaded = pd.read_csv(paths[0])
    assert len(reloaded) == len(run.predictions)
    assert list(reloaded.columns) == list(run.predictions.columns)


# ── the panel dump seam ───────────────────────────────────────────────────────


def test_a_parquet_dump_without_pyarrow_refuses_up_front(tmp_path, monkeypatch) -> None:
    """The refusal must happen at the edge — the Trino fetch is the expensive half."""
    import importlib.util

    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *a, **k: None if name == "pyarrow" else True,
    )
    with pytest.raises(train_m1.TrainingError, match="pyarrow"):
        train_m1.check_panel_format(tmp_path / "panel.parquet")


def test_a_csv_dump_never_needs_an_engine(tmp_path) -> None:
    train_m1.check_panel_format(tmp_path / "panel.csv")


def test_panel_file_round_trips_through_csv(tmp_path) -> None:
    panel = make_raw_panel()
    path = tmp_path / "panel.csv"
    train_m1.write_panel_file(panel, path)
    assert len(train_m1.read_panel_file(path)) == len(panel)


def test_float_noise_below_the_rounding_floor_does_not_mint_a_version() -> None:
    """The measured CSV seam moves floats by ~5e-17; that must not be a retrain."""
    config, day = make_config(), dt.date(2026, 8, 21)
    panel = make_raw_panel()
    nudged = panel.copy()
    nudged["severity_score"] = nudged["severity_score"].astype(float) + 1e-15
    assert (
        train_m1.train(config, nudged, day).model_version
        == train_m1.train(config, panel, day).model_version
    )


def test_a_real_change_above_the_rounding_floor_still_mints_a_version() -> None:
    """...but the floor must not be so coarse that real differences vanish."""
    config, day = make_config(), dt.date(2026, 8, 21)
    panel = make_raw_panel()
    nudged = panel.copy()
    nudged["severity_score"] = nudged["severity_score"].astype(float) + 1e-6
    assert (
        train_m1.train(config, nudged, day).model_version
        != train_m1.train(config, panel, day).model_version
    )


def test_a_dumped_panel_trains_to_the_same_model_version(tmp_path) -> None:
    """The dump/load seam must be lossless, or "trained off a dump" means nothing."""
    config, day = make_config(), dt.date(2026, 8, 21)
    panel = make_raw_panel()
    path = tmp_path / "panel.csv"
    train_m1.write_panel_file(panel, path)
    assert (
        train_m1.train(config, train_m1.read_panel_file(path), day).model_version
        == train_m1.train(config, panel, day).model_version
    )


def test_forecast_artefact_constants_match_the_trainer():
    """The Gold loader duplicates three constants; this is where they are pinned.

    scripts/gold/forecast_artefacts.py cannot import this module — build_gold
    runs without pandas or statsmodels, on purpose. So the artefact's root,
    filename and column order are written down twice, and the copies can only
    be kept honest from the side that has both installed: here.
    """
    from scripts.gold import forecast_artefacts as fa

    assert fa.ARTEFACT_ROOT == train_m1.ARTEFACT_ROOT
    assert fa.PREDICTIONS_FILE == train_m1.PREDICTIONS_FILE
    # The trainer's column order is the one it slices `predictions` down to.
    assert list(fa.PREDICTION_COLUMNS) == [
        "snowfall_event_id",
        "plow_zone",
        "model_version",
        "predicted_count",
        "baseline_count",
        "actual_count",
    ]


def test_the_written_artefact_is_exactly_what_the_loader_expects():
    """End to end across the seam, without object storage in the middle."""
    import io

    from scripts.gold.forecast_artefacts import PREDICTION_COLUMNS, parse_predictions

    frame = pd.DataFrame(
        {
            "snowfall_event_id": ["EVT-1", "EVT-2"],
            "plow_zone": ["A", "B"],
            "model_version": ["m1-poisson-x", "m1-poisson-x"],
            "predicted_count": [12.5, 3.25],
            "baseline_count": [9.0, None],
            "actual_count": [11, 4],
        }
    )[list(PREDICTION_COLUMNS)]
    buffer = io.StringIO()
    frame.to_csv(buffer, index=False)
    rows = parse_predictions(buffer.getvalue().encode(), "m1-poisson-x", "k")
    assert len(rows) == 2
    # pandas writes a missing float as an empty cell, which sql_literal turns
    # into NULL. If it ever wrote "nan" instead, F5 would take a string.
    assert rows[1][PREDICTION_COLUMNS.index("baseline_count")] == ""
