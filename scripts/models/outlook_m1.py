"""M1 forward chain entry point — one forecast issue in, one outlook artefact out (H2-R12).

Design: ``docs/dev/design/20260927-forecast-chain-rehearsal.md``.

    forecast vintage (Bronze, one ingest_date=)  ─┐
    observed archive (Silver, the days before)   ─┼─> daily ─> events ─> M1 ─> artefact
    M1 panel (Gold) + a model's coefficients     ─┘

🔴 **Reads one Bronze vintage, never Silver's forecast table.** Silver keeps
only the freshest revision of each hour, so once an event has passed its hours
come from a later collection's ``past_days`` — close to observation, not a
forecast. Scoring that would be scoring hindsight (design §0.2).

🔴 **``--model-version`` is required.** No "latest": version strings sort the
deliberately broken ``nomonth`` model first, and every other ordering key is
equal across a training batch (L3 launch §4.6).

🔴 **The artefact is append-only.** It is the record of what the chain said on
its issue date — the thing a later forward evaluation (H2-R11) is scored
against. An existing ``issue_date × model_version`` is refused, locally and in
object storage.

Like ``train_m1.py``, this file and ``config/models/outlook.yaml`` are the only
places the chain names the deployed city's columns.

Ways to run it:

    # compute node, from production inputs
    python -m scripts.models.outlook_m1 --issue-date 2026-11-15 \\
        --model-version m1-poisson-20260822-df31d954 --upload

    # anywhere, from dumped inputs — no Trino, no object storage
    python -m scripts.models.outlook_m1 --issue-date 2025-12-18 \\
        --model-version m1-poisson-20260822-df31d954 \\
        --panel-file var/m1-panel.parquet --metrics-file metrics.json \\
        --archive-file archive.csv --replay-archive

``--replay-archive`` feeds the observed archive in as a *perfect forecast*.
It checks the pipeline, not forecast skill: for a past event it must
reproduce F5's prediction for that event, and ``--check-predictions``
asserts exactly that.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from models.request_forecast import features as feat
from models.request_forecast import outlook as ol
from models.request_forecast.outlook_weather import (
    DailyWeather,
    EventRule,
    OutlookWeatherError,
    SeverityBounds,
    check_contiguous,
    hourly_to_daily,
    segment_events,
    splice,
)
from scripts._env import load_cli_env
from scripts.models import train_m1

if TYPE_CHECKING:  # pragma: no cover - import-time only
    import pandas as pd

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "models" / "outlook.yaml"
SOURCES_DIR = REPO_ROOT / "config" / "sources"

OUTLOOK_FILE = "outlook.csv"
RUN_FILE = "run.json"
METRICS_FILE = train_m1.METRICS_FILE

# F5's replay tolerance. The chain and training compute the same product in a
# different order (numpy matmul vs statsmodels' linear predictor), which moves
# the last bits; measured 6.4e-15 across all 1,298 scheduling-era cells.
REPLAY_RTOL = 1e-9

PRODUCTION_BRONZE_ROOT = "bronze/raw"


class OutlookRunError(RuntimeError):
    """The run cannot produce an honest artefact."""


# ── config ────────────────────────────────────────────────────────────────────


def load_config(path: Path | None = None) -> dict:
    with (path or CONFIG_PATH).open(encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    if not isinstance(loaded, dict):
        raise OutlookRunError(f"{path or CONFIG_PATH} did not parse to a mapping")
    rule = EventRule.from_config(loaded["event_rule"])
    lookback = int(loaded["observed_lookback_days"])
    if rule.accum_window_days is not None and lookback < rule.accum_window_days - 1:
        raise OutlookRunError(
            f"observed_lookback_days={lookback} is shorter than the rolling window "
            f"needs ({rule.accum_window_days - 1} days before the forecast starts)"
        )
    return loaded


def forecast_query_params(config: dict) -> dict:
    """The forecast dataset's query params, read from its source YAML."""
    from ingestion.config import load_source_config

    source = load_source_config(config["forecast"]["source_id"])
    ds = next((d for d in source.datasets if d.name == config["forecast"]["dataset"]), None)
    if ds is None:
        raise OutlookRunError(f"dataset {config['forecast']['dataset']!r} not in its source YAML")
    return dict(ds.query_params or {})


# ── inputs ────────────────────────────────────────────────────────────────────


def read_forecast_file(path: Path) -> tuple[list[dict], str]:
    """Records and the sha256 of the uncompressed payload (the manifest's own measure)."""
    raw = path.read_bytes()
    payload = gzip.decompress(raw) if path.suffix == ".gz" else raw
    return _parse_ndjson(payload), hashlib.sha256(payload).hexdigest()


def fetch_forecast_vintage(
    config: dict, issue_date: dt.date, bucket: str, bronze_root: str
) -> tuple[list[dict], str, str]:
    """Read one ``ingest_date=`` partition and verify it against its manifest.

    A checksum mismatch is refused rather than logged: the artefact records the
    vintage's checksum as its provenance, and a payload that does not match
    its own manifest has no provenance.
    """
    from ingestion.loaders.s3_client import build_s3_client

    fc = config["forecast"]
    prefix = f"{bronze_root}/{fc['source_id']}/{fc['dataset']}/ingest_date={issue_date.isoformat()}"
    client = build_s3_client()
    try:
        body = client.get_object(Bucket=bucket, Key=f"{prefix}/data.ndjson.gz")["Body"].read()
        manifest = json.loads(
            client.get_object(Bucket=bucket, Key=f"{prefix}/manifest.json")["Body"].read()
        )
    except client.exceptions.NoSuchKey as exc:
        raise OutlookRunError(
            f"no forecast collected for {issue_date} under s3://{bucket}/{prefix}/ — "
            f"a missed snapshot day cannot be recovered (docs/guide/snapshot-collection.md)"
        ) from exc
    payload = gzip.decompress(body)
    checksum = hashlib.sha256(payload).hexdigest()
    if checksum != manifest.get("sha256_checksum"):
        raise OutlookRunError(f"{prefix}: payload checksum does not match its manifest")
    return _parse_ndjson(payload), checksum, f"s3://{bucket}/{prefix}/"


def _parse_ndjson(payload: bytes) -> list[dict]:
    return [json.loads(line) for line in payload.decode("utf-8").splitlines() if line.strip()]


def read_archive_file(path: Path) -> list[DailyWeather]:
    """``weather_date, snowfall_sum_cm, temperature_2m_min_c`` — header optional."""
    out: list[DailyWeather] = []
    with path.open(encoding="utf-8") as handle:
        for row in csv.reader(handle):
            if not row or row[0] == "weather_date":
                continue
            out.append(
                DailyWeather(
                    day=dt.date.fromisoformat(row[0]),
                    snowfall_cm=float(row[1]) if row[1] not in ("", "NULL") else None,
                    min_temperature_c=float(row[2]) if row[2] not in ("", "NULL") else None,
                )
            )
    return sorted(out, key=lambda d: d.day)


def fetch_archive(start: dt.date, end: dt.date, location_prefix: str) -> list[DailyWeather]:
    """Observed days in ``[start, end)`` from Silver.

    The predicate is on the partition column ``"date"`` (gold-sql.md R1): a
    predicate on ``weather_date`` would filter rows but prune nothing, and a
    whole-table read of the archive's ~6,800 day partitions times out.
    """
    from scripts.ddl.apply_ddl import _connect, load_trino_settings, normalise_prefix, schema_name

    schema = schema_name("silver", normalise_prefix(location_prefix))
    sql = (
        "SELECT weather_date, snowfall_sum_cm, temperature_2m_min_c "
        f"FROM {schema}.silver_weather_archive "
        f"WHERE \"date\" >= '{start.isoformat()}' AND \"date\" < '{end.isoformat()}' "
        "ORDER BY weather_date"
    )
    cursor = _connect(load_trino_settings(), schema).cursor()
    cursor.execute(sql)
    return [
        DailyWeather(
            day=row[0] if isinstance(row[0], dt.date) else dt.date.fromisoformat(str(row[0])),
            snowfall_cm=None if row[1] is None else float(row[1]),
            min_temperature_c=None if row[2] is None else float(row[2]),
        )
        for row in cursor.fetchall()
    ]


def fetch_metrics(model_version: str, bucket: str) -> dict:
    from ingestion.loaders.s3_client import build_s3_client

    key = f"{train_m1.ARTEFACT_ROOT}/{model_version}/{METRICS_FILE}"
    body = build_s3_client().get_object(Bucket=bucket, Key=key)["Body"].read()
    return json.loads(body)


# ── the chain ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Weather:
    """The spliced daily series and what it is made of."""

    days: list[DailyWeather]
    first_forecast_day: dt.date
    last_forecast_day: dt.date


def build_weather(
    config: dict,
    issue_date: dt.date,
    observed: list[DailyWeather],
    forecast_records: list[dict] | None,
    horizon_days: int,
) -> Weather:
    """Forecast days (from the vintage, or the archive under ``--replay-archive``)
    spliced after observed days."""
    fc = config["forecast"]
    if forecast_records is None:
        end = issue_date + dt.timedelta(days=horizon_days)
        forecast = [d for d in observed if issue_date <= d.day < end]
    else:
        forecast = hourly_to_daily(
            forecast_records,
            time_field=fc["time_field"],
            snowfall_field=fc["snowfall_field"],
            temperature_field=fc["temperature_field"],
            min_hours_per_day=int(fc["min_hours_per_day"]),
        )
    if not forecast:
        raise OutlookRunError(f"no complete forecast day for issue {issue_date}")
    days = splice(observed, forecast)
    lookback_start = issue_date - dt.timedelta(days=int(config["observed_lookback_days"]))
    days = [d for d in days if d.day >= lookback_start]
    check_contiguous(days)
    return Weather(days=days, first_forecast_day=forecast[0].day, last_forecast_day=forecast[-1].day)


def run_outlook(
    *,
    config: dict,
    m1_config: dict,
    issue_date: dt.date,
    panel: pd.DataFrame,
    metrics: dict,
    weather: Weather,
) -> tuple[pd.DataFrame, dict]:
    """Every forward event scored for every unit, plus the run's provenance."""
    import pandas as pd

    rule = EventRule.from_config(config["event_rule"])
    names = feat.feature_names(m1_config)
    missing = [a for a in ol.EVENT_ATTRIBUTES if a not in names]
    if missing:
        raise OutlookRunError(f"m1.yaml no longer lists event feature(s) {missing}")

    cohort = panel.drop_duplicates("event_id")
    bounds = SeverityBounds.from_cohort(
        cohort["total_snowfall_cm"], cohort["min_temperature_c"],
        snow_weight=float(config["severity_snow_weight"]),
    )
    envelope = ol.TrainingEnvelope.from_panel(
        feat.build_panel_features(panel), metrics["holdout_season"]
    )

    events = [e for e in segment_events(weather.days, rule) if e.end_date >= issue_date]
    frames = []
    for event in events:
        event_id = ol.outlook_event_id(issue_date, event.start_date)
        prepared = ol.prepare_event(
            panel, event, event_id=event_id, bounds=bounds,
            season_first_month=int(config["season_first_month"]),
        )
        prepared["predicted_count"] = ol.predict_mean(prepared, metrics["coefficients"], names)
        prepared = ol.flag_extrapolation(prepared, envelope)
        prepared["event_end"] = event.end_date
        prepared["started_before_issue"] = event.start_date < issue_date
        prepared["truncated_at_horizon"] = event.end_date >= weather.last_forecast_day
        frames.append(prepared)

    outlook = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    run = {
        "issue_date": issue_date.isoformat(),
        "event_count": len(events),
        "first_forecast_day": weather.first_forecast_day.isoformat(),
        "last_forecast_day": weather.last_forecast_day.isoformat(),
        "event_rule": config["event_rule"],
        "severity_bounds": {
            "snow_lo": bounds.snow_lo, "snow_hi": bounds.snow_hi,
            "cold_lo": bounds.cold_lo, "cold_hi": bounds.cold_hi,
            "snow_weight": bounds.snow_weight,
        },
        "training_envelope": {
            "ranges": envelope.ranges,
            "max_season_index": envelope.max_season_index,
            "months": sorted(envelope.months),
        },
        # R13 has not delivered. A Poisson analytic interval is not a stand-in:
        # under the known overdispersion it is systematically too narrow.
        "interval": None,
    }
    return outlook, run


OUTPUT_ROLE_COLUMNS = ("unit_id", "unit_size")


def to_output(outlook: pd.DataFrame, m1_config: dict, issue_date: dt.date, model_version: str
              ) -> pd.DataFrame:
    """Rename roles back to the city's columns — the boundary, crossed once."""
    import pandas as pd

    columns = [
        "outlook_event_id", "event_start", "event_end", "started_before_issue",
        "truncated_at_horizon", "unit_id", "unit_size", *ol.EVENT_ATTRIBUTES,
        "season", "season_index", "month", "prev_target", "expanding_mean",
        "lag_asof_event_id", "predicted_count", "predicted_per_1k",
    ]
    if outlook.empty:
        frame = pd.DataFrame(columns=["issue_date", "model_version", *columns])
    else:
        frame = outlook.rename(columns={"event_id": "outlook_event_id"}).copy()
        frame["event_start"] = pd.to_datetime(frame["event_start"]).dt.date
        frame["predicted_per_1k"] = 1000.0 * frame["predicted_count"] / frame["unit_size"]
        frame = frame[[*columns, *ol.flag_columns(frame)]]
        frame.insert(0, "model_version", model_version)
        frame.insert(0, "issue_date", issue_date.isoformat())
    city = train_m1.column_map(m1_config)
    return frame.rename(columns={role: city[role] for role in OUTPUT_ROLE_COLUMNS})


def check_replay(outlook: pd.DataFrame, predictions: pd.DataFrame, m1_config: dict) -> int:
    """For a past event, the chain must reproduce F5's prediction for it.

    Returns the number of cells compared. Raises on any cell beyond
    :data:`REPLAY_RTOL`, or when nothing could be compared — a check that
    matched zero rows has checked nothing.
    """
    city = train_m1.column_map(m1_config)
    if outlook.empty:
        raise OutlookRunError("replay produced no event, so nothing was compared")
    got = outlook.assign(
        real_event_id=[
            f"SNOW-{pd_date:%Y%m%d}" for pd_date in outlook["event_start"]
        ]
    )
    merged = got.merge(
        predictions,
        left_on=["real_event_id", city["unit_id"]],
        right_on=[city["event_id"], city["unit_id"]],
        suffixes=("", "_f5"),
    )
    if merged.empty:
        raise OutlookRunError("no replayed cell matched a F5 prediction")
    rel = (merged["predicted_count"] - merged["predicted_count_f5"]).abs() / merged[
        "predicted_count_f5"
    ].abs().clip(lower=1e-12)
    if float(rel.max()) > REPLAY_RTOL:
        worst = merged.loc[rel.idxmax()]
        raise OutlookRunError(
            f"replay diverges from F5: {worst['real_event_id']} × {worst[city['unit_id']]} "
            f"relative difference {float(rel.max()):.3e} > {REPLAY_RTOL}"
        )
    return len(merged)


# ── artefact ──────────────────────────────────────────────────────────────────


def artefact_prefix(root: str, issue_date: dt.date, model_version: str) -> str:
    return f"{root}/issue_date={issue_date.isoformat()}/{model_version}"


def resolve_artefact_root(config: dict, bronze_root: str, override: str | None) -> str:
    """Where this run's artefact goes — never the real record for synthetic input.

    The production artefact root is the record of what the chain said on each
    issue date. An outlook computed from a rehearsal's synthetic Bronze filed
    there would be indistinguishable from a real one and would be scored
    against real outcomes later. So a non-production Bronze root must name its
    own artefact root, and that root must not be the production one.
    """
    production = str(config["artefact_root"]).rstrip("/")
    root = (override or production).rstrip("/")
    if bronze_root.rstrip("/") != PRODUCTION_BRONZE_ROOT and (
        root == production or root.startswith(production + "/")
    ):
        raise OutlookRunError(
            f"--bronze-root {bronze_root!r} is not production, so the artefact must "
            f"not go under {production!r}; pass --artefact-root under the same smoke prefix"
        )
    return root


def write_local(out_dir: Path, prefix: str, outlook: pd.DataFrame, run: dict) -> list[Path]:
    target = out_dir / prefix
    if (target / RUN_FILE).exists():
        raise OutlookRunError(f"{target} already holds a run; outlooks are append-only")
    target.mkdir(parents=True, exist_ok=True)
    outlook.to_csv(target / OUTLOOK_FILE, index=False)
    (target / RUN_FILE).write_text(json.dumps(run, indent=2, default=str) + "\n", encoding="utf-8")
    return [target / OUTLOOK_FILE, target / RUN_FILE]


def run_exists(prefix: str, bucket: str) -> bool:
    """True when ``run.json`` is present — the file whose presence marks a run complete."""
    from botocore.exceptions import ClientError

    from ingestion.loaders.s3_client import build_s3_client

    try:
        build_s3_client().head_object(Bucket=bucket, Key=f"{prefix}/{RUN_FILE}")
    except ClientError:
        return False
    return True


def upload(paths: list[Path], prefix: str, bucket: str) -> None:
    """Refuse before writing anything if the run already exists."""
    from ingestion.loaders.s3_client import build_s3_client

    if run_exists(prefix, bucket):
        raise OutlookRunError(f"s3://{bucket}/{prefix}/ already holds a run; outlooks are append-only")
    client = build_s3_client()
    # run.json last: its presence is what marks a run complete.
    for path in sorted(paths, key=lambda p: p.name == RUN_FILE):
        client.put_object(Bucket=bucket, Key=f"{prefix}/{path.name}", Body=path.read_bytes())
        logger.info("uploaded s3://%s/%s/%s", bucket, prefix, path.name)


def git_sha() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


# ── scheduling helpers ────────────────────────────────────────────────────────


def issue_date_for(moment: dt.datetime, config: dict) -> dt.date:
    """The forecast issue a scheduled run should score: ``moment``'s local date.

    Collection labels ``ingest_date=`` by the city's local date (the storage
    node's unit sets ``TZ``), so the run must convert the same way — a UTC date
    would point at tomorrow's partition for every run after local midnight UTC.
    The timezone is the forecast source's own ``timezone`` query parameter.
    """
    from zoneinfo import ZoneInfo

    tz = forecast_query_params(config).get("timezone")
    if not tz:
        raise OutlookRunError("the forecast source declares no timezone")
    if moment.tzinfo is None:
        raise OutlookRunError("issue_date_for needs a timezone-aware moment")
    return moment.astimezone(ZoneInfo(str(tz))).date()


def scheduled_argv(issue_date: dt.date, config: dict, bucket: str, out_dir: str) -> list[str]:
    """The one invocation the daily DAG makes. Kept here so the DAG holds no logic."""
    return [
        "--issue-date", issue_date.isoformat(),
        "--model-version", str(config["serving_model_version"]),
        "--bucket", bucket,
        "--out-dir", out_dir,
        "--upload",
        "--skip-existing",
    ]


# ── CLI ───────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Score one forecast issue with M1 (H2-R12).")
    p.add_argument("--issue-date", required=True, type=dt.date.fromisoformat,
                   help="The forecast's collection date (its ingest_date= partition).")
    p.add_argument("--model-version", required=True,
                   help="Exact model_version. Never inferred — see the module docstring.")
    weather = p.add_mutually_exclusive_group()
    weather.add_argument("--forecast-file", type=Path,
                         help="A Bronze-shaped .ndjson(.gz) instead of object storage.")
    weather.add_argument("--replay-archive", action="store_true",
                         help="Use the observed archive as a perfect forecast (pipeline check).")
    p.add_argument("--panel-file", type=Path, help="Dumped M1 panel instead of Trino.")
    p.add_argument("--metrics-file", type=Path, help="The model's metrics.json instead of S3.")
    p.add_argument("--archive-file", type=Path, help="Observed daily weather CSV instead of Trino.")
    p.add_argument("--check-predictions", type=Path,
                   help="F5 predictions.csv; with --replay-archive, assert the chain reproduces it.")
    p.add_argument("--bronze-root", default=PRODUCTION_BRONZE_ROOT,
                   help="Bronze key root. A smoke prefix for rehearsals (batch B).")
    p.add_argument("--artefact-root", default=None,
                   help="Artefact key root; defaults to the config's. Required with a "
                        "non-production --bronze-root.")
    p.add_argument("--out-dir", type=Path, default=REPO_ROOT / "var" / "outlook-runs")
    p.add_argument("--upload", action="store_true", help="Also write the artefact to S3.")
    p.add_argument("--skip-existing", action="store_true",
                   help="With --upload: exit 0 without doing anything if this issue date "
                        "and model version already have a run. For scheduled reruns.")
    p.add_argument("--bucket", default=None, help="Overrides S3_BUCKET_NAME.")
    p.add_argument("--location-prefix", default="", help="Smoke schema prefix, as in apply_ddl.")
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    load_cli_env()
    bucket = args.bucket or os.environ.get("S3_BUCKET_NAME")

    try:
        config = load_config()
        m1_config = train_m1.load_config()
        # First, before any input is read: a synthetic run aimed at the real
        # artefact root must fail before it has done anything.
        artefact_root = resolve_artefact_root(config, args.bronze_root, args.artefact_root)
        if args.skip_existing:
            if not args.upload or not bucket:
                raise OutlookRunError("--skip-existing needs --upload and a bucket")
            existing = artefact_prefix(artefact_root, args.issue_date, args.model_version)
            if run_exists(existing, bucket):
                # A rerun of a day already recorded: nothing to do, and nothing
                # may be overwritten. Success rather than failure, so clearing
                # a DAG run does not turn into a permanently red task.
                logger.info("s3://%s/%s/ already holds a run — skipping", bucket, existing)
                return 0
        needs_bucket = args.upload or not (args.metrics_file and (args.forecast_file or args.replay_archive))
        if needs_bucket and not bucket:
            raise OutlookRunError("object storage is needed here: pass --bucket or set S3_BUCKET_NAME")

        metrics = (
            json.loads(args.metrics_file.read_text(encoding="utf-8"))
            if args.metrics_file else fetch_metrics(args.model_version, bucket)
        )
        if metrics.get("model_version") != args.model_version:
            raise OutlookRunError(
                f"metrics are for {metrics.get('model_version')!r}, not {args.model_version!r}"
            )

        raw_panel = (
            train_m1.read_panel_file(args.panel_file) if args.panel_file
            else train_m1.fetch_panel(m1_config, args.location_prefix)
        )
        panel = train_m1.to_role_names(raw_panel, m1_config)

        horizon = int(forecast_query_params(config).get("forecast_days", 16))
        lookback = dt.timedelta(days=int(config["observed_lookback_days"]))
        if args.archive_file:
            observed = read_archive_file(args.archive_file)
        else:
            observed = fetch_archive(
                args.issue_date - lookback,
                args.issue_date + dt.timedelta(days=horizon if args.replay_archive else 0),
                args.location_prefix,
            )

        if args.replay_archive:
            records, checksum, source = None, None, "replay: observed archive as a perfect forecast"
        elif args.forecast_file:
            records, checksum = read_forecast_file(args.forecast_file)
            source = str(args.forecast_file)
        else:
            records, checksum, source = fetch_forecast_vintage(
                config, args.issue_date, bucket, args.bronze_root
            )

        weather = build_weather(config, args.issue_date, observed, records, horizon)
        outlook, run = run_outlook(
            config=config, m1_config=m1_config, issue_date=args.issue_date,
            panel=panel, metrics=metrics, weather=weather,
        )
        output = to_output(outlook, m1_config, args.issue_date, args.model_version)

        if args.check_predictions:
            import pandas as pd

            compared = check_replay(outlook_for_check(outlook, m1_config),
                                    pd.read_csv(args.check_predictions), m1_config)
            logger.info("replay matches F5 on %d cell(s) within rtol %g", compared, REPLAY_RTOL)
            run["replay_check"] = {"cells": compared, "rtol": REPLAY_RTOL}
    except (OutlookRunError, OutlookWeatherError, ol.OutlookError, feat.PanelError) as exc:
        logger.error("%s", exc)
        return 1

    panel_fp = train_m1.panel_fingerprint(m1_config, panel)
    run.update(
        {
            "model_version": args.model_version,
            "model_panel_fingerprint": metrics.get("panel_fingerprint"),
            "panel_fingerprint": panel_fp,
            # False is expected once winter adds events to Gold: the lags then
            # come from a newer panel than the model was trained on.
            "panel_matches_model": panel_fp == metrics.get("panel_fingerprint"),
            "forecast_source": source,
            "forecast_sha256": checksum,
            "code_git_sha": git_sha(),
            "created_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        }
    )

    try:
        prefix = artefact_prefix(artefact_root, args.issue_date, args.model_version)
        paths = write_local(args.out_dir, prefix, output, run)
        if args.upload:
            upload(paths, prefix, bucket)
    except OutlookRunError as exc:
        logger.error("%s", exc)
        return 1

    logger.info("issue %s: %d event(s), %d row(s) -> %s",
                args.issue_date, run["event_count"], len(output), paths[0].parent)
    return 0


def outlook_for_check(outlook: pd.DataFrame, m1_config: dict) -> pd.DataFrame:
    """Role-named outlook, renamed only as far as :func:`check_replay` needs."""
    city = train_m1.column_map(m1_config)
    return outlook.rename(columns={"unit_id": city["unit_id"]})


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
