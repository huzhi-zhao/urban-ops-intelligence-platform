"""Reconstruct past forecast vintages from Open-Meteo's Previous Runs API — H2-R11 batch A.

Design: ``docs/dev/design/20260927-r11-forward-evaluation-proposal.md`` §1–§2.

Real snapshots only exist from 2026-09-27. Open-Meteo also serves, for every
past hour, what the model said N days before that hour
(``<field>_previous_day{N}``, "predicted N×24 hours before valid time").
From those this module builds, for an issue date D, a payload shaped like a
real snapshot:

    day D+k (k = 0 … 6)  <-  <field>_previous_day{k+1}

🔴 **k+1, never k.** A value "predicted 24·(k+1) h before valid time" for any
hour of D+k was issued no later than D−1 at that hour of day — before the 06:45
collection on D. Taking ``previous_day{k}`` would let every hour after 06:45 on
D+k come from a model run issued after the vintage's own issue time. The cost
of the safe choice is a vintage up to about a day staler than a real one, so
results lean pessimistic; the design says so beside the numbers.

A vintage stops at the first day with any missing hour on its lead, so its
horizon varies (lead 7 starts later than leads 1–6); the manifest records it.

🔴 **Nothing here writes under ``bronze/raw``.** That tree is the record of
what was actually collected on each day; a reconstructed payload filed there
would be indistinguishable from a real collection and could not be told apart
afterwards. The root must start with ``research/``, and every manifest carries
``reconstructed: true`` and the method string.

    # R11-0: coverage by month and lead, plus a models= consistency check
    python -m scripts.models.reconstruct_forecast --seasons 2024-2025 2025-2026 --coverage

    # R11-A: write the vintages
    python -m scripts.models.reconstruct_forecast --seasons 2024-2025 2025-2026 --upload
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from scripts._env import load_cli_env
from scripts.models import outlook_m1, outlook_rehearsal

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

PREVIOUS_RUNS_ENDPOINT = "https://previous-runs-api.open-meteo.com/v1/forecast"
MAX_LEAD_DAYS = 7  # the API's deepest offset
RESEARCH_ROOT = "research/r11/reconstructed"
METHOD = "open-meteo previous-runs api; day D+k <- <field>_previous_day{k+1}"
DEFAULT_CACHE_DIR = REPO_ROOT / "var" / "r11-cache"

# Only the query params that locate the forecast. The snapshot's own window
# params (past_days, forecast_days, hourly) mean nothing to this API.
LOCATION_PARAMS = ("latitude", "longitude", "timezone")

# Issue months of a snow season, matching the proposal's §5.0 sampling.
LAST_ISSUE_MONTH = 4


class ReconstructionError(RuntimeError):
    """The inputs cannot yield an honest reconstructed vintage."""


@dataclass(frozen=True)
class Fields:
    time: str
    snowfall: str
    temperature: str

    @classmethod
    def from_config(cls, config: dict) -> Fields:
        fc = config["forecast"]
        return cls(fc["time_field"], fc["snowfall_field"], fc["temperature_field"])

    @property
    def values(self) -> tuple[str, str]:
        return (self.snowfall, self.temperature)


def lead_variable(field: str, lead: int) -> str:
    return f"{field}_previous_day{lead}"


def check_research_root(root: str) -> None:
    if not root.startswith("research/"):
        raise ReconstructionError(
            f"root {root!r} must start with 'research/': reconstructed vintages never go "
            f"where a real collection day could be mistaken for them"
        )


# ── dates ─────────────────────────────────────────────────────────────────────


def season_issue_dates(season: str, first_month: int) -> list[dt.date]:
    """Every day from ``first_month`` of the season's first year to the end of April."""
    first_year = int(season.split("-")[0])
    start = dt.date(first_year, first_month, 1)
    end = dt.date(first_year + 1, LAST_ISSUE_MONTH + 1, 1)
    return [start + dt.timedelta(days=i) for i in range((end - start).days)]


# ── fetch ─────────────────────────────────────────────────────────────────────


def location_params(config: dict) -> dict:
    params = outlook_m1.forecast_query_params(config)
    missing = [k for k in LOCATION_PARAMS if k not in params]
    if missing:
        raise ReconstructionError(f"forecast query params lack {missing}")
    return {k: params[k] for k in LOCATION_PARAMS}


def fetch_previous_runs(
    location: dict,
    start: dt.date,
    end: dt.date,
    fields: Fields,
    *,
    models: str | None = None,
    cache_dir: Path | None = DEFAULT_CACHE_DIR,
) -> dict[str, list]:
    """Hourly columns for ``[start, end]`` inclusive, every lead of both fields.

    Cached by the full query: the API serves a fixed past, and a season is one
    request, so a rerun of the tooling should not ask again.
    """
    import requests

    params = dict(location)
    params["hourly"] = ",".join(
        lead_variable(f, n) for f in fields.values for n in range(1, MAX_LEAD_DAYS + 1)
    )
    params["start_date"] = start.isoformat()
    params["end_date"] = end.isoformat()
    if models:
        params["models"] = models

    key = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]
    cached = cache_dir / f"previous-runs-{key}.json" if cache_dir else None
    if cached and cached.exists():
        return json.loads(cached.read_text())["hourly"]

    response = requests.get(PREVIOUS_RUNS_ENDPOINT, params=params, timeout=120)
    response.raise_for_status()
    body = response.json()
    if "hourly" not in body:
        raise ReconstructionError(f"previous-runs response has no hourly block: {body}")
    if cached:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(body))
    return body["hourly"]


def hours_by_day(hourly: dict[str, list]) -> dict[dt.date, list[int]]:
    """Row indices grouped by local date. The API's ``time`` is local when a
    timezone is passed, which is the same convention the snapshot uses."""
    days: dict[dt.date, list[int]] = defaultdict(list)
    for i, stamp in enumerate(hourly["time"]):
        days[dt.date.fromisoformat(stamp[:10])].append(i)
    return days


# ── reconstruct ───────────────────────────────────────────────────────────────


def reconstruct(
    hourly: dict[str, list], issue: dt.date, fields: Fields, horizon_days: int = MAX_LEAD_DAYS
) -> list[dict]:
    """Snapshot-shaped records for issue date ``issue``, days ``issue … issue+h-1``.

    Stops at the first day whose lead has any missing hour or that the fetched
    window does not cover, so a vintage is always a contiguous run of complete
    days starting on its issue date. May return ``[]``.
    """
    if not 1 <= horizon_days <= MAX_LEAD_DAYS:
        raise ReconstructionError(f"horizon_days must be 1..{MAX_LEAD_DAYS}, got {horizon_days}")
    by_day = hours_by_day(hourly)
    records: list[dict] = []
    for k in range(horizon_days):
        rows = by_day.get(issue + dt.timedelta(days=k))
        if not rows:
            break
        lead = k + 1
        day = []
        for i in rows:
            values = {f: hourly[lead_variable(f, lead)][i] for f in fields.values}
            if any(v is None for v in values.values()):
                day = []
                break
            day.append({fields.time: hourly["time"][i], **values})
        if not day:
            break
        records.extend(day)
    return records


def horizon_of(records: list[dict], fields: Fields) -> int:
    return len({r[fields.time][:10] for r in records})


def coverage(
    hourly: dict[str, list], fields: Fields
) -> dict[tuple[str, int], tuple[int, int]]:
    """``(YYYY-MM, lead) -> (hours with both fields present, hours)``."""
    out: dict[tuple[str, int], list[int]] = defaultdict(lambda: [0, 0])
    for i, stamp in enumerate(hourly["time"]):
        month = stamp[:7]
        for lead in range(1, MAX_LEAD_DAYS + 1):
            cell = out[(month, lead)]
            cell[1] += 1
            if all(hourly[lead_variable(f, lead)][i] is not None for f in fields.values):
                cell[0] += 1
    return {k: (v[0], v[1]) for k, v in out.items()}


def models_agree(default: dict[str, list], explicit: dict[str, list], fields: Fields) -> int:
    """Hours where the default and an explicit ``models=`` answer differ."""
    differ = 0
    for f in fields.values:
        for lead in range(1, MAX_LEAD_DAYS + 1):
            name = lead_variable(f, lead)
            differ += sum(a != b for a, b in zip(default[name], explicit[name], strict=True))
    return differ


# ── write ─────────────────────────────────────────────────────────────────────


def provenance(records: list[dict], fields: Fields) -> dict:
    return {
        "reconstructed": True,
        "method": METHOD,
        "endpoint": PREVIOUS_RUNS_ENDPOINT,
        "horizon_days": horizon_of(records, fields),
    }


def vintage_exists(client, bucket: str, root: str, config: dict, issue: dt.date) -> bool:
    fc = config["forecast"]
    key = f"{root}/{fc['source_id']}/{fc['dataset']}/ingest_date={issue.isoformat()}/manifest.json"
    try:
        client.head_object(Bucket=bucket, Key=key)
    except client.exceptions.ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise
    return True


# ── driver ────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--seasons", nargs="+", required=True, metavar="YYYY-YYYY")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--coverage", action="store_true",
                      help="R11-0: print coverage by month x lead and check models=; write nothing")
    mode.add_argument("--dry-run", action="store_true",
                      help="reconstruct every issue and report horizons; write nothing")
    mode.add_argument("--upload", action="store_true", help="write vintages to object storage")
    p.add_argument("--root", default=RESEARCH_ROOT)
    p.add_argument("--bucket", default=None, help="defaults to $S3_BUCKET_NAME")
    p.add_argument("--models", default="best_match",
                   help="explicit models= for the --coverage consistency check")
    p.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    check_research_root(args.root)
    load_cli_env()

    config = outlook_m1.load_config()
    fields = Fields.from_config(config)
    location = location_params(config)
    first_month = int(config["season_first_month"])

    client = bucket = None
    if args.upload:
        from ingestion.loaders.s3_client import build_s3_client

        bucket = args.bucket or os.environ.get("S3_BUCKET_NAME")
        if not bucket:
            raise ReconstructionError("no bucket: pass --bucket or set S3_BUCKET_NAME")
        client = build_s3_client()

    horizons: dict[int, int] = defaultdict(int)
    written = skipped = 0
    for season in args.seasons:
        issues = season_issue_dates(season, first_month)
        end = issues[-1] + dt.timedelta(days=MAX_LEAD_DAYS - 1)
        hourly = fetch_previous_runs(location, issues[0], end, fields, cache_dir=args.cache_dir)

        if args.coverage:
            explicit = fetch_previous_runs(location, issues[0], end, fields,
                                           models=args.models, cache_dir=args.cache_dir)
            print(f"\n{season}  hours with both fields present, by lead (1..{MAX_LEAD_DAYS})")
            table = coverage(hourly, fields)
            for month in sorted({m for m, _ in table}):
                cells = [table[(month, lead)] for lead in range(1, MAX_LEAD_DAYS + 1)]
                print(f"  {month}  " + "  ".join(f"{a / b:6.1%}" for a, b in cells))
            differ = models_agree(hourly, explicit, fields)
            print(f"  default vs models={args.models}: {differ} differing values")
            continue

        for issue in issues:
            records = reconstruct(hourly, issue, fields)
            horizon = horizon_of(records, fields)
            horizons[horizon] += 1
            if not records or not args.upload:
                continue
            if vintage_exists(client, bucket, args.root, config, issue):
                skipped += 1
                continue
            outlook_rehearsal.put_vintage(client, bucket, args.root, config, issue, records,
                                          provenance=provenance(records, fields))
            written += 1

    if not args.coverage:
        summary = ", ".join(f"{h} days: {n}" for h, n in sorted(horizons.items()))
        logger.info("issue dates by horizon — %s", summary)
        if args.upload:
            logger.info("wrote %d vintages under s3://%s/%s/ (%d already there, left alone)",
                        written, bucket, args.root, skipped)
        if horizons.get(0):
            logger.warning("%d issue dates have no reconstructable day", horizons[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
