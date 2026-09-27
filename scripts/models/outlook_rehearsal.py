"""Synthetic rehearsal of M1's forward chain — H2-R12's criterion (batch B).

For each scenario: build a forecast payload shaped exactly like an Open-Meteo
snapshot, file it as Bronze under a **smoke prefix**, run the real chain
(``outlook_m1``) against it through object storage, and check the outcome
against what the scenario was built to produce.

What this checks is that the chain *runs* — reads a vintage, cuts events,
featurises, scores, flags, writes — on the inputs winter will bring. It does
**not** check whether predictions are right: a synthetic storm never happened,
so there is no count to compare with, and comparing against the model's own
output would be the model grading itself (H2-R3). The ``extreme`` scenario
checks that flags light up, never the number beside them.

🔴 Nothing here may write under the production Bronze or artefact roots. A
synthetic payload in ``bronze/raw/.../ingest_date=`` would be indistinguishable
from a real collection day, and that history cannot be corrected afterwards.
The prefix must start with ``smoke-``; ``outlook_m1`` separately refuses a
non-production Bronze root aimed at the production artefact root.

    python -m scripts.models.outlook_rehearsal \\
        --model-version m1-poisson-20260822-df31d954

Design: docs/dev/design/20260927-forecast-chain-rehearsal.md §4 batch B.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from scripts._env import load_cli_env
from scripts.models import outlook_m1

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# A quiet winter day, chosen from the archive: no snow at all in the 13 days
# before it, so the rolling-accumulation criterion cannot pull real snow into
# a synthetic scenario. February, in a training season — so a scenario's
# extrapolation flags come from its synthetic weather alone, not from the date.
DEFAULT_ISSUE_DATE = dt.date(2025, 2, 25)

PAST_DAYS = 3  # mirrors the forecast dataset's query params; read at run time
TEMPERATURE_C = -10.0


# ── scenarios ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Scenario:
    """Daily snowfall (cm) by offset from issue date, over the forecast window.

    ``daily_cm`` maps day offset -> snowfall for that day, spread evenly over
    its hours; any offset not listed is dry. ``hours_on_last_day`` cuts the
    payload's final day short, like a real horizon.
    """

    name: str
    daily_cm: dict[int, float]
    expect: Callable[[dict, list[dict]], list[str]]
    hours_on_last_day: int = 24
    description: str = ""


@dataclass
class Outcome:
    scenario: str
    events: int
    rows: int
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def _events(run: dict) -> int:
    return int(run["event_count"])


def _flags(rows: list[dict]) -> set[str]:
    return {k for r in rows for k, v in r.items() if k.startswith("outside_") and v in (True, "True")}


def _expect_events(n: int) -> Callable[[dict, list[dict]], list[str]]:
    def check(run: dict, rows: list[dict]) -> list[str]:
        return [] if _events(run) == n else [f"expected {n} event(s), got {_events(run)}"]
    return check


def _all(*checks: Callable[[dict, list[dict]], list[str]]) -> Callable[[dict, list[dict]], list[str]]:
    def check(run: dict, rows: list[dict]) -> list[str]:
        return [p for c in checks for p in c(run, rows)]
    return check


def _rows_per_event(units: int) -> Callable[[dict, list[dict]], list[str]]:
    def check(run: dict, rows: list[dict]) -> list[str]:
        want = _events(run) * units
        return [] if len(rows) == want else [f"expected {want} row(s), got {len(rows)}"]
    return check


def _no_flags(run: dict, rows: list[dict]) -> list[str]:
    lit = _flags(rows)
    return [] if not lit else [f"unexpected extrapolation flag(s) {sorted(lit)}"]


def _flags_include(*names: str) -> Callable[[dict, list[dict]], list[str]]:
    def check(run: dict, rows: list[dict]) -> list[str]:
        missing = sorted(set(names) - _flags(rows))
        return [] if not missing else [f"flag(s) {missing} did not light up"]
    return check


def _column_all(column: str, value: bool) -> Callable[[dict, list[dict]], list[str]]:
    def check(run: dict, rows: list[dict]) -> list[str]:
        bad = [r for r in rows if (r[column] in (True, "True")) != value]
        return [] if not bad else [f"{column} is not {value} on {len(bad)} row(s)"]
    return check


def _positive_predictions(run: dict, rows: list[dict]) -> list[str]:
    bad = [r for r in rows if not float(r["predicted_count"]) > 0]
    return [] if not bad else [f"{len(bad)} non-positive prediction(s)"]


def _horizon_is(offset: int) -> Callable[[dict, list[dict]], list[str]]:
    def check(run: dict, rows: list[dict]) -> list[str]:
        want = (dt.date.fromisoformat(run["issue_date"]) + dt.timedelta(days=offset)).isoformat()
        got = run["last_forecast_day"]
        return [] if got == want else [f"last complete forecast day {got}, expected {want}"]
    return check


def scenarios(units: int, horizon: int) -> list[Scenario]:
    """The seven scenarios of design §4 batch B.

    Snow days sit at least two dry days apart from anything else so the
    event rule's one-day gap bridge never joins scenarios' pieces by accident.
    """
    last = horizon - 1
    scored = _all(_rows_per_event(units), _positive_predictions,
                  _column_all("started_before_issue", False))
    return [
        Scenario("no_snow", {}, _all(_expect_events(0), _horizon_is(last)),
                 description="dry window: an artefact with zero events is still written"),
        Scenario("moderate", {2: 8.0},
                 _all(_expect_events(1), scored, _no_flags,
                      _column_all("truncated_at_horizon", False)),
                 description="one 8 cm day, inside everything training saw"),
        Scenario("heavy", {2: 5.0, 3: 6.0, 4: 5.0},
                 _all(_expect_events(1), scored, _no_flags),
                 description="16 cm over three days, the size of SNOW-20251218"),
        Scenario("extreme", {2: 40.0},
                 _all(_expect_events(1), scored,
                      _flags_include("outside_total_snowfall_cm",
                                     "outside_peak_daily_snowfall_cm")),
                 description="40 cm in a day against a 29.05 cm cohort maximum: "
                             "flags must light, the number is not judged"),
        Scenario("accum_only", {2: 2.5, 3: 2.5, 4: 2.5, 5: 2.5, 6: 2.5},
                 _all(_expect_events(1), scored, _column_all("accum_flag", True)),
                 description="no single day reaches 3 cm; the rolling total does"),
        Scenario("horizon", {last - 1: 4.0, last: 4.0},
                 _all(_expect_events(1), scored, _column_all("truncated_at_horizon", True)),
                 description="snow on the last two forecast days: the event may not be over"),
        Scenario("partial_day", {last: 10.0},
                 _all(_expect_events(0), _horizon_is(last - 1)),
                 hours_on_last_day=12,
                 description="10 cm on a half-day at the horizon: the half-day is dropped"),
    ]


# ── payload ───────────────────────────────────────────────────────────────────


def build_records(scenario: Scenario, issue: dt.date, past_days: int, horizon: int,
                  fields: dict) -> list[dict]:
    """Hourly records shaped like an Open-Meteo snapshot for this issue date."""
    records = []
    for offset in range(-past_days, horizon):
        day = issue + dt.timedelta(days=offset)
        hours = scenario.hours_on_last_day if offset == horizon - 1 else 24
        per_hour = scenario.daily_cm.get(offset, 0.0) / 24.0
        for hour in range(hours):
            records.append({
                fields["time_field"]: f"{day.isoformat()}T{hour:02d}:00",
                fields["temperature_field"]: TEMPERATURE_C,
                "precipitation": per_hour * 0.1,
                fields["snowfall_field"]: per_hour,
                "windspeed_10m": 10.0,
            })
    return records


def put_vintage(client, bucket: str, bronze_root: str, config: dict, issue: dt.date,
                records: list[dict]) -> str:
    """Write data + manifest in the Bronze snapshot layout under ``bronze_root``."""
    fc = config["forecast"]
    prefix = f"{bronze_root}/{fc['source_id']}/{fc['dataset']}/ingest_date={issue.isoformat()}"
    payload = ("\n".join(json.dumps(r, sort_keys=True) for r in records) + "\n").encode("utf-8")
    body = gzip.compress(payload, mtime=0)
    times = sorted(r[fc["time_field"]] for r in records)
    manifest = {
        "source_id": fc["source_id"],
        "dataset_name": fc["dataset"],
        "ingest_date": issue.isoformat(),
        "month_partition": issue.strftime("%Y-%m"),
        "filename": "data.ndjson.gz",
        "record_count": len(records),
        "file_size_bytes": len(payload),
        "sha256_checksum": hashlib.sha256(payload).hexdigest(),
        "compression": "gzip",
        "stored_bytes": len(body),
        "data_date_min": times[0][:10],
        "data_date_max": times[-1][:10],
        "fetch_timestamp": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "timestamp_field": fc["time_field"],
        "synthetic": True,
    }
    client.put_object(Bucket=bucket, Key=f"{prefix}/data.ndjson.gz", Body=body)
    client.put_object(Bucket=bucket, Key=f"{prefix}/manifest.json",
                      Body=json.dumps(manifest, indent=2).encode("utf-8"))
    return prefix


def read_artefact(client, bucket: str, prefix: str) -> tuple[dict, list[dict]]:
    import csv
    import io

    run = json.loads(client.get_object(Bucket=bucket, Key=f"{prefix}/run.json")["Body"].read())
    text = client.get_object(Bucket=bucket, Key=f"{prefix}/outlook.csv")["Body"].read().decode()
    return run, list(csv.DictReader(io.StringIO(text)))


# ── driver ────────────────────────────────────────────────────────────────────


def check_smoke_prefix(prefix: str) -> str:
    prefix = prefix.strip().strip("/")
    if not prefix.startswith("smoke-"):
        raise SystemExit(f"--prefix must start with 'smoke-' (got {prefix!r})")
    return prefix


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Synthetic rehearsal of the M1 forward chain.")
    p.add_argument("--model-version", required=True)
    p.add_argument("--issue-date", type=dt.date.fromisoformat, default=DEFAULT_ISSUE_DATE)
    p.add_argument("--prefix", default="smoke-r12",
                   help="Object-storage prefix for every write; must start with 'smoke-'.")
    p.add_argument("--run-id", default=None,
                   help="Subfolder under the prefix (default: UTC timestamp).")
    p.add_argument("--only", nargs="*", default=None, help="Run these scenarios only.")
    p.add_argument("--bucket", default=None)
    p.add_argument("--location-prefix", default="")
    p.add_argument("--out-dir", type=Path, default=REPO_ROOT / "var" / "outlook-rehearsal")
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    load_cli_env()
    prefix = check_smoke_prefix(args.prefix)
    bucket = args.bucket or os.environ.get("S3_BUCKET_NAME")
    if not bucket:
        raise SystemExit("pass --bucket or set S3_BUCKET_NAME")
    run_id = args.run_id or dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")

    from ingestion.loaders.s3_client import build_s3_client
    from scripts.models import train_m1

    config = outlook_m1.load_config()
    params = outlook_m1.forecast_query_params(config)
    past_days = int(params.get("past_days", PAST_DAYS))
    horizon = int(params.get("forecast_days", 16))
    units = len(train_m1.to_role_names(
        train_m1.fetch_panel(train_m1.load_config(), args.location_prefix),
        train_m1.load_config(),
    ).unit_id.unique())

    client = build_s3_client()
    outcomes: list[Outcome] = []
    for scenario in scenarios(units, horizon):
        if args.only and scenario.name not in args.only:
            continue
        root = f"{prefix}/{run_id}/{scenario.name}"
        records = build_records(scenario, args.issue_date, past_days, horizon, config["forecast"])
        put_vintage(client, bucket, f"{root}/bronze/raw", config, args.issue_date, records)
        code = outlook_m1.main([
            "--issue-date", args.issue_date.isoformat(),
            "--model-version", args.model_version,
            "--bronze-root", f"{root}/bronze/raw",
            "--artefact-root", f"{root}/gold/_outlook_runs",
            "--out-dir", str(args.out_dir / run_id / scenario.name),
            "--bucket", bucket,
            "--location-prefix", args.location_prefix,
            "--upload",
        ])
        if code != 0:
            outcomes.append(Outcome(scenario.name, 0, 0, [f"chain exited {code}"]))
            continue
        artefact = outlook_m1.artefact_prefix(
            f"{root}/gold/_outlook_runs", args.issue_date, args.model_version
        )
        run, rows = read_artefact(client, bucket, artefact)
        outcomes.append(Outcome(scenario.name, _events(run), len(rows),
                                scenario.expect(run, rows)))

    width = max(len(o.scenario) for o in outcomes)
    for o in outcomes:
        status = "PASS" if o.ok else "FAIL"
        logger.info("%s %-*s events=%d rows=%d %s", status, width, o.scenario, o.events, o.rows,
                    "; ".join(o.problems))
    failed = [o for o in outcomes if not o.ok]
    logger.info("rehearsal %s: %d/%d scenario(s) passed — s3://%s/%s/%s/",
                run_id, len(outcomes) - len(failed), len(outcomes), bucket, prefix, run_id)
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
