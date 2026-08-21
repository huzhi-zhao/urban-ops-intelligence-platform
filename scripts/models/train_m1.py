"""M1 training entry point — panel -> Poisson GLM -> artefact.

Design: ``docs/dev/design/20260820-scoring-chain-and-m1.md`` §4 (the model) and
§5 (why this writes an artefact rather than a table).

🔴 **This file and ``config/models/m1.yaml`` are the only two places in the M1
stack where Winnipeg column names may appear.** ``models/request_forecast/``
speaks role names and never learns which city it runs for — city-agnostic
guardrail §2. The mapping is applied once, in :func:`to_role_names`.

🔴 **It writes an artefact, never a table.** F5 grows with every retrain and
old versions are never overwritten, which is exactly what R4's whole-table
rebuild would destroy. The loader in ``build_gold``'s ``scoring`` stage reads
every artefact and rebuilds the table from all of them, so what gets purged is
the table, not the history. Design §5.

Three ways to run it, because Trino lives on the compute node and the panel
does not:

    # 1. compute node, one shot
    python -m scripts.models.train_m1 --upload

    # 2. compute node: take a copy of the panel and stop
    python -m scripts.models.train_m1 --dump-panel var/m1-panel.parquet

    # 3. anywhere, from that copy — no Trino, no network
    python -m scripts.models.train_m1 --panel-file var/m1-panel.parquet

Form 3 is not only a convenience. It is what lets the training half be run
against the *real* 2,178-cell panel on a machine that cannot reach Trino, which
is the difference between "the unit tests pass on a synthetic frame" and "we
have seen what the real panel looks like".
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from models.request_forecast import features as feat
from models.request_forecast import model as mdl
from scripts._env import load_cli_env
from scripts.ddl.apply_ddl import (
    TrinoConfigError,
    _connect,
    load_trino_settings,
    normalise_prefix,
    schema_name,
)

if TYPE_CHECKING:  # pragma: no cover - import-time only
    import pandas as pd

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "models" / "m1.yaml"

# Where the artefact lands, relative to the bucket. Design §5. Kept out of the
# Gold table prefixes on purpose: `build_gold` purges those, and purging this
# one would delete the version history the artefact exists to preserve.
ARTEFACT_ROOT = "gold/_forecast_runs"

# Decimal places the panel fingerprint rounds to. See _canonical_frame.
FINGERPRINT_DECIMALS = 9

PREDICTIONS_FILE = "predictions.csv"
METRICS_FILE = "metrics.json"

# The prediction panel is the scheduling-era subset; training uses everything.
# Both numbers come from the config rather than being written here twice.
GATE_TRAINING_CELLS = "expected_training_cells"
GATE_PREDICTION_CELLS = "expected_prediction_cells"


class TrainingError(RuntimeError):
    """A gate failed, or the panel is not what the config says it is."""


# ── config ────────────────────────────────────────────────────────────────────


def load_config(path: Path | None = None) -> dict:
    """Read ``config/models/m1.yaml``."""
    import yaml

    target = path or CONFIG_PATH
    with target.open(encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    if not isinstance(loaded, dict):
        raise TrainingError(f"{target} did not parse to a mapping")
    return loaded


def column_map(config: dict) -> dict[str, str]:
    """Role name -> the Winnipeg column the panel query returns it as."""
    mapping = config.get("panel", {}).get("columns") or {}
    missing = [role for role in feat.REQUIRED_PANEL_COLUMNS if role not in mapping]
    if missing:
        raise TrainingError(
            f"config/models/m1.yaml panel.columns is missing role(s) {missing}; "
            f"every role in features.REQUIRED_PANEL_COLUMNS needs a city column"
        )
    return dict(mapping)


# ── panel: the one place that speaks Winnipeg ─────────────────────────────────


def panel_sql(config: dict, gold_schema: str) -> str:
    """F1 summed across ``winter_category``, joined to its two dimensions.

    One row per (plow_zone, snowfall_event) — F1's own grain carries
    ``winter_category`` as well, and M1 trains on the cross-category sum
    (design §4.1). The ``GROUP BY`` is what collapses 13,068 rows to 2,178;
    :func:`features.validate_panel` fails loudly if it did not.

    No ``SELECT *`` and no date literals (R5). This reads Gold only, so R1's
    Silver date predicate does not apply — the partition wall is in
    ``silver_service_request``, and F1 is 13,068 rows in one prefix.
    """
    event_features = config.get("features", {}).get("event") or []
    cols = column_map(config)
    event_columns = ",\n        ".join(f"e.{name}" for name in event_features)
    return f"""
SELECT
        z.{cols["unit_id"]} AS {cols["unit_id"]},
        e.{cols["event_id"]} AS {cols["event_id"]},
        z.{cols["unit_size"]} AS {cols["unit_size"]},
        e.{cols["event_start"]} AS {cols["event_start"]},
        e.{cols["season"]} AS {cols["season"]},
        e.is_scheduling_era AS is_scheduling_era,
        {event_columns},
        SUM(f.request_count) AS {cols["target"]}
FROM {gold_schema}.fact_service_request_zone_event AS f
JOIN {gold_schema}.dim_snowfall_event AS e
        ON f.{cols["event_id"]} = e.{cols["event_id"]}
JOIN {gold_schema}.dim_plow_zone AS z
        ON f.{cols["unit_id"]} = z.{cols["unit_id"]}
GROUP BY
        z.{cols["unit_id"]},
        e.{cols["event_id"]},
        z.{cols["unit_size"]},
        e.{cols["event_start"]},
        e.{cols["season"]},
        e.is_scheduling_era,
        {event_columns}
""".strip()


def fetch_panel(config: dict, location_prefix: str = "") -> pd.DataFrame:
    """Run :func:`panel_sql` against Trino and return the raw, city-named frame."""
    import pandas as pd

    settings = load_trino_settings()
    gold_schema = schema_name("gold", normalise_prefix(location_prefix))
    sql = panel_sql(config, gold_schema)
    logger.info("fetching M1 panel from %s (%s)", settings, gold_schema)

    connection = _connect(settings, gold_schema)
    cursor = connection.cursor()
    cursor.execute(sql)
    rows = cursor.fetchall()
    columns = [d[0] for d in cursor.description]
    return pd.DataFrame(rows, columns=columns)


def to_role_names(raw: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Rename the city's columns to role names — the city boundary, crossed once.

    Everything downstream of this call is city-agnostic, which is only true
    because the rename happens here and nowhere else.
    """
    cols = column_map(config)
    rename = {city: role for role, city in cols.items()}
    missing = [city for city in cols.values() if city not in raw.columns]
    if missing:
        raise TrainingError(
            f"panel is missing column(s) {missing} that config/models/m1.yaml "
            f"maps to roles; got {sorted(raw.columns)}"
        )
    renamed = raw.rename(columns=rename)
    renamed["event_start"] = _to_datetime(renamed["event_start"])
    return renamed


def _to_datetime(column: pd.Series) -> pd.Series:
    """Coerce the event start to datetimes.

    Trino hands back ``datetime.date``; a parquet round-trip hands back
    ``datetime64``. Both must end up comparable, because
    ``assert_no_history_leak`` compares a max against a min and Python will
    happily raise on mixed types halfway through a run instead of at the edge.
    """
    import pandas as pd

    return pd.to_datetime(column)


# ── artefact ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TrainingRun:
    """Everything one training produces. Written out by :func:`write_artefacts`."""

    model_version: str
    predictions: pd.DataFrame
    evaluation: mdl.Evaluation
    coefficients: dict[str, float]
    panel_fingerprint: str
    trained_at: str


def derive_model_version(config: dict, panel: pd.DataFrame, today: dt.date) -> str:
    """``{prefix}-{YYYYMMDD}-{fingerprint}``.

    The date is for whoever reads the table; the fingerprint is the identity.
    It hashes the config and the panel's contents, which buys both halves of
    gate a6 at once: retraining on unchanged inputs yields the same version and
    therefore rewrites the same artefact byte-for-byte, while a changed feature
    list or a refreshed panel yields a new version and so *cannot* silently
    overwrite the predictions an earlier backtest was scored against.

    That second half is what makes A3b (two versions coexisting in F5) a
    consequence of the design rather than something to remember to do.
    """
    fingerprint = panel_fingerprint(config, panel)
    prefix = config.get("model_version_prefix") or "m1"
    return f"{prefix}-{today:%Y%m%d}-{fingerprint}"


def panel_fingerprint(config: dict, panel: pd.DataFrame) -> str:
    """A short stable hash of (config, panel contents).

    Sorted by the panel's own grain before hashing so a query that returns the
    same rows in a different order is the same panel — otherwise every rerun
    would mint a new "version" that differs in nothing.

    🔴 Hashed through :func:`_canonical_frame` rather than over the DataFrame's
    raw values, because **the same panel arrives with different dtypes
    depending on how it got here**: Trino hands back ``Decimal`` and
    ``datetime.date``, parquet preserves int64/bool, a CSV dump returns strings
    and floats. ``hash_pandas_object`` hashes the representation, so without
    normalisation the dump/load seam alone would produce a second
    ``model_version`` for a panel that had not changed at all — and F5, which
    accumulates versions by design (§5), has no way to tell that apart from a
    real retrain. A unit test pins the CSV round-trip specifically.
    """
    import pandas as pd

    ordered = _canonical_frame(panel.sort_values(["unit_id", "event_id"]))
    digest = hashlib.sha256()
    digest.update(json.dumps(config, sort_keys=True, default=str).encode("utf-8"))
    digest.update(pd.util.hash_pandas_object(ordered, index=False).values.tobytes())
    return digest.hexdigest()[:8]


def _canonical_frame(panel: pd.DataFrame) -> pd.DataFrame:
    """One representation per *value*, whatever dtype it arrived as.

    Columns are taken in sorted order so a query that adds a column at a
    different position does not by itself change the hash, and each is coerced
    by kind: dates to ISO strings, booleans to 0/1, anything numeric to float,
    everything else to string.

    🔴 Floats are rounded to ``FINGERPRINT_DECIMALS`` places, which is not
    sloppiness — it is the difference between a fingerprint and a checksum.
    ``to_csv`` writes ``0.30000000000000004`` as ``0.3``; measured, the seam
    moves values by up to **5.6e-17**. Nine decimal places is many orders
    coarser than that and still many orders finer than any real difference in
    these units (centimetres of snow, whole tickets, whole addresses), so it
    discards float noise without ever merging two panels a GLM could tell
    apart.
    """
    import pandas as pd

    canonical = {}
    for name in sorted(panel.columns):
        column = panel[name].reset_index(drop=True)
        if pd.api.types.is_datetime64_any_dtype(column):
            canonical[name] = column.dt.strftime("%Y-%m-%d")
        elif pd.api.types.is_bool_dtype(column):
            canonical[name] = column.astype("int64")
        elif pd.api.types.is_numeric_dtype(column):
            canonical[name] = column.astype("float64").round(FINGERPRINT_DECIMALS)
        else:
            canonical[name] = column.astype("string")
    return pd.DataFrame(canonical)


def write_artefacts(run: TrainingRun, out_dir: Path) -> list[Path]:
    """Write ``predictions.csv`` and ``metrics.json`` under ``out_dir/{version}/``."""
    target = out_dir / run.model_version
    target.mkdir(parents=True, exist_ok=True)

    predictions_path = target / PREDICTIONS_FILE
    run.predictions.to_csv(predictions_path, index=False)

    metrics_path = target / METRICS_FILE
    metrics_path.write_text(
        json.dumps(
            {
                "model_version": run.model_version,
                "trained_at": run.trained_at,
                "panel_fingerprint": run.panel_fingerprint,
                "holdout_season": run.evaluation.holdout_season,
                "summary": run.evaluation.summary(),
                "model": {
                    "mae": run.evaluation.model.mae,
                    "poisson_deviance": run.evaluation.model.poisson_deviance,
                    "n_rows": run.evaluation.model.n_rows,
                },
                "baseline": {
                    "method": "seasonal_naive (causal expanding mean)",
                    "mae": run.evaluation.baseline.mae,
                    "poisson_deviance": run.evaluation.baseline.poisson_deviance,
                    "n_rows": run.evaluation.baseline.n_rows,
                },
                "beats_baseline": run.evaluation.beats_baseline,
                "coefficients": run.coefficients,
            },
            indent=2,
            sort_keys=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return [predictions_path, metrics_path]


def upload_artefacts(paths: list[Path], model_version: str, bucket: str) -> list[str]:
    """Put the artefact files under ``{ARTEFACT_ROOT}/{model_version}/``.

    Uses the same S3 client as Bronze ingestion rather than a second one, so
    endpoint, path-style addressing and SigV4 are configured in exactly one
    place.
    """
    from ingestion.loaders.s3_client import build_s3_client

    client = build_s3_client()
    written: list[str] = []
    for path in paths:
        key = f"{ARTEFACT_ROOT}/{model_version}/{path.name}"
        client.put_object(Bucket=bucket, Key=key, Body=path.read_bytes())
        logger.info("uploaded s3://%s/%s", bucket, key)
        written.append(key)
    return written


# ── training ──────────────────────────────────────────────────────────────────


def train(config: dict, raw_panel: pd.DataFrame, today: dt.date) -> TrainingRun:
    """Panel in, :class:`TrainingRun` out. No I/O, so it is testable as a unit."""
    panel = to_role_names(raw_panel, config)
    _gate_cell_count(config, panel)

    prepared = feat.build_panel_features(panel)
    trainable = feat.drop_rows_without_history(prepared)

    split = mdl.split_holdout_last_season(trainable)
    mdl.assert_no_history_leak(split.train, split.test)

    names = feat.feature_names(config)
    x_train, y_train, offset_train = feat.build_design_matrix(split.train, names)
    results = mdl.fit_poisson_glm(x_train, y_train, offset_train)

    x_test, _y_test, offset_test = feat.build_design_matrix(split.test, names)
    evaluation = mdl.evaluate(
        actual=split.test["target"],
        model_predicted=mdl.predict(results, x_test, offset_test),
        baseline_predicted=mdl.seasonal_naive(split.test),
        holdout_season=split.holdout_season,
    )

    predictions = _predict_scoring_era(config, prepared, results, names)
    model_version = derive_model_version(config, panel, today)

    return TrainingRun(
        model_version=model_version,
        predictions=predictions.assign(model_version=model_version)[
            [
                "snowfall_event_id",
                "plow_zone",
                "model_version",
                "predicted_count",
                "baseline_count",
                "actual_count",
            ]
        ],
        evaluation=evaluation,
        coefficients={k: float(v) for k, v in results.params.items()},
        panel_fingerprint=panel_fingerprint(config, panel),
        trained_at=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
    )


def _predict_scoring_era(
    config: dict, prepared: pd.DataFrame, results: Any, names: list[str]
) -> pd.DataFrame:
    """Predict the scheduling-era subset — F5's 1,298 rows.

    Predicting the *whole* panel and filtering afterwards would be the same
    arithmetic, but it would also silently produce rows for pre-era events that
    F5's contract has no place for, and the row-count gate would then be
    measuring the filter rather than the model.
    """
    era = prepared[prepared["is_scheduling_era"].astype(bool)].reset_index(drop=True)
    expected = config.get("panel", {}).get(GATE_PREDICTION_CELLS)
    if expected is not None and len(era) != int(expected):
        raise TrainingError(
            f"gate a2: prediction panel has {len(era)} scheduling-era cells, "
            f"config expects {expected}. Check dim_snowfall_event.is_scheduling_era "
            f"and design §3 P3 (N drifting is O4, not a code bug)."
        )
    feat.assert_prediction_panel_has_history(era)

    x_era, _y, offset_era = feat.build_design_matrix(era, names)
    return era.assign(
        snowfall_event_id=era["event_id"],
        plow_zone=era["unit_id"],
        predicted_count=mdl.predict(results, x_era, offset_era).astype("float64"),
        baseline_count=mdl.seasonal_naive(era),
        # Ground truth is known for every elapsed event and is exactly the
        # panel's own target. F5 declares it null for unelapsed events; the
        # panel only contains events that already happened, so nothing is null
        # here yet. Recorded rather than left to the reader to infer.
        actual_count=era["target"].astype("int64"),
    )


def _gate_cell_count(config: dict, panel: pd.DataFrame) -> None:
    """Gate a1 — the training panel is 2,178 cells."""
    expected = config.get("panel", {}).get(GATE_TRAINING_CELLS)
    if expected is None:
        return
    if len(panel) != int(expected):
        raise TrainingError(
            f"gate a1: training panel has {len(panel)} cells, config expects "
            f"{expected}. If N moved, that is design O4 — update the config and "
            f"the launch doc together, and do not change any schema."
        )


# ── CLI ───────────────────────────────────────────────────────────────────────


def read_panel_file(path: Path) -> pd.DataFrame:
    """Read a dumped panel. Parquet by extension, CSV otherwise."""
    import pandas as pd

    if path.suffix.lower() in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    return pd.read_csv(path)


def write_panel_file(panel: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in {".parquet", ".pq"}:
        panel.to_parquet(path, index=False)
    else:
        panel.to_csv(path, index=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Train M1 and write a forecast artefact (design §4/§5).",
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--panel-file",
        type=Path,
        help="Train from a dumped panel instead of Trino. Works with no network.",
    )
    source.add_argument(
        "--dump-panel",
        type=Path,
        help="Fetch the panel from Trino, write it here and stop without training.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=REPO_ROOT / "var" / "forecast-runs",
        help="Local artefact directory (default: var/forecast-runs).",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help=f"Also upload the artefact to s3://{{bucket}}/{ARTEFACT_ROOT}/.",
    )
    parser.add_argument("--bucket", default=None, help="Overrides S3_BUCKET_NAME.")
    parser.add_argument(
        "--location-prefix",
        default="",
        help="Smoke-test schema prefix, same meaning as in apply_ddl.",
    )
    parser.add_argument("--config", type=Path, default=None, help="Override m1.yaml path.")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    load_cli_env()
    config = load_config(args.config)

    try:
        if args.panel_file:
            raw = read_panel_file(args.panel_file)
            logger.info("panel from %s: %d rows", args.panel_file, len(raw))
        else:
            raw = fetch_panel(config, args.location_prefix)
            logger.info("panel from Trino: %d rows", len(raw))
            if args.dump_panel:
                write_panel_file(raw, args.dump_panel)
                logger.info("panel written to %s — stopping before training", args.dump_panel)
                return 0

        run = train(config, raw, dt.date.today())
    except (TrainingError, feat.PanelError, TrinoConfigError) as exc:
        logger.error("%s", exc)
        return 1

    paths = write_artefacts(run, args.out_dir)
    logger.info("model_version %s", run.model_version)
    logger.info("%s", run.evaluation.summary())
    logger.info("artefact: %s", paths[0].parent)

    if args.upload:
        import os

        bucket = args.bucket or os.environ.get("S3_BUCKET_NAME")
        if not bucket:
            logger.error("--upload needs --bucket or S3_BUCKET_NAME")
            return 1
        upload_artefacts(paths, run.model_version, bucket)

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
