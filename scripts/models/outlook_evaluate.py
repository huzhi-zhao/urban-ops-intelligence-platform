"""Score H2-R11's backtest outlooks against the events that happened — batch C, question 2.

Design: ``docs/dev/design/20260927-r11-forward-evaluation-proposal.md`` §5.1,
which registered every definition used here **before** this file was written.
Change a definition and the registration no longer describes the result; a
changed rule needs a new registration, not an edit here.

    real event E, lead k   ->  issue date D = E.start - k
    eligible               :   D is a sampled issue date with a run
    matched                :   some outlook event of D overlaps [E.start, E.end]
    hit rate               :   matched / eligible, both seasons pooled
    criterion              :   hit rate at lead 3 >= 0.50

Snow-amount error is descriptive and carries no criterion: for a matched
event, the summed ``total_snowfall_cm`` of every overlapping outlook event
minus the real total.

Standard library only for the scoring itself, so the rule is tested offline.

    python -m scripts.models.outlook_evaluate --seasons 2024-2025 2025-2026 \\
        --model-version m1-poisson-20260822-df31d954
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import logging
import os
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

from models.request_forecast.outlook_weather import snow_season
from scripts._env import load_cli_env
from scripts.models import outlook_backtest, outlook_m1
from scripts.models import reconstruct_forecast as rc

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LEADS = tuple(range(1, rc.MAX_LEAD_DAYS + 1))

# §5.1 — registered 2026-09-27, before any comparison was computed.
CRITERION_LEAD = 3
CRITERION_HIT_RATE = 0.50

EVALUATION_ROOT = "research/r11/evaluation"


@dataclass(frozen=True)
class Event:
    event_id: str
    start: dt.date
    end: dt.date
    total_snowfall_cm: float


def overlaps(a: Event, b: Event) -> bool:
    """Closed intervals; touching endpoints overlap (§5.1)."""
    return a.start <= b.end and b.start <= a.end


@dataclass
class Match:
    event_id: str
    season: str
    lead: int
    issue_date: str
    matched: bool
    forecast_cm: float | None
    observed_cm: float

    @property
    def error_cm(self) -> float | None:
        return None if self.forecast_cm is None else self.forecast_cm - self.observed_cm


def match_events(
    real: list[Event],
    outlooks: dict[dt.date, list[Event]],
    first_month: int,
    leads: tuple[int, ...] = LEADS,
) -> list[Match]:
    """One row per (real event, lead) that is eligible. ``outlooks`` holds
    every issue date that has a run — an empty list is a run with no event,
    which is eligible and unmatched, not missing."""
    rows: list[Match] = []
    for event in sorted(real, key=lambda e: e.start):
        for lead in leads:
            issue = event.start - dt.timedelta(days=lead)
            if issue not in outlooks:
                continue
            hits = [f for f in outlooks[issue] if overlaps(f, event)]
            rows.append(Match(
                event_id=event.event_id,
                season=snow_season(event.start, first_month),
                lead=lead,
                issue_date=issue.isoformat(),
                matched=bool(hits),
                forecast_cm=sum(f.total_snowfall_cm for f in hits) if hits else None,
                observed_cm=event.total_snowfall_cm,
            ))
    return rows


def summarise(rows: list[Match], group_by_season: bool = False) -> dict:
    """``{key: {eligible, matched, hit_rate, mean_error_cm, mean_abs_error_cm}}``,
    key = lead, or (season, lead). Ratios computed once from counts (gold-sql R3)."""
    cells: dict = defaultdict(list)
    for r in rows:
        cells[(r.season, r.lead) if group_by_season else r.lead].append(r)
    out = {}
    for key, group in sorted(cells.items()):
        errors = [r.error_cm for r in group if r.matched]
        matched = sum(r.matched for r in group)
        out[key] = {
            "eligible": len(group),
            "matched": matched,
            "hit_rate": matched / len(group),
            "mean_error_cm": sum(errors) / len(errors) if errors else None,
            "mean_abs_error_cm": sum(abs(e) for e in errors) / len(errors) if errors else None,
        }
    return out


def verdict(pooled: dict) -> dict:
    cell = pooled.get(CRITERION_LEAD)
    if cell is None:
        return {"status": "no_data", "lead": CRITERION_LEAD, "threshold": CRITERION_HIT_RATE}
    return {
        "status": "pass" if cell["hit_rate"] >= CRITERION_HIT_RATE else "fail",
        "lead": CRITERION_LEAD,
        "threshold": CRITERION_HIT_RATE,
        "matched": cell["matched"],
        "eligible": cell["eligible"],
        "hit_rate": cell["hit_rate"],
    }


# ── inputs ────────────────────────────────────────────────────────────────────


def outlook_events(csv_text: str, city_event_id: str = "outlook_event_id") -> list[Event]:
    """Distinct events of one outlook.csv (one row per unit per event)."""
    seen: dict[str, Event] = {}
    for row in csv.DictReader(io.StringIO(csv_text)):
        eid = row[city_event_id]
        if eid not in seen:
            seen[eid] = Event(eid, dt.date.fromisoformat(row["event_start"]),
                              dt.date.fromisoformat(row["event_end"]),
                              float(row["total_snowfall_cm"]))
    return list(seen.values())


def fetch_outlooks(client, bucket: str, root: str, model_version: str,
                   issues: list[dt.date]) -> dict[dt.date, list[Event]]:
    out: dict[dt.date, list[Event]] = {}
    for issue in issues:
        prefix = outlook_m1.artefact_prefix(root, issue, model_version)
        try:
            client.head_object(Bucket=bucket, Key=f"{prefix}/{outlook_m1.RUN_FILE}")
        except client.exceptions.ClientError:
            continue  # no run: the issue is not eligible, not unmatched
        body = client.get_object(Bucket=bucket, Key=f"{prefix}/{outlook_m1.OUTLOOK_FILE}")
        out[issue] = outlook_events(body["Body"].read().decode("utf-8"))
    return out


def fetch_real_events(location_prefix: str) -> list[Event]:
    """dim_snowfall_event is ~100 rows and not day-partitioned, so a full read is fine."""
    from scripts.ddl.apply_ddl import _connect, load_trino_settings, normalise_prefix, schema_name

    schema = schema_name("gold", normalise_prefix(location_prefix))
    cursor = _connect(load_trino_settings(), schema).cursor()
    cursor.execute(
        "SELECT snowfall_event_id, start_date, end_date, total_snowfall_cm "
        f"FROM {schema}.dim_snowfall_event ORDER BY start_date"
    )
    return [
        Event(r[0], r[1] if isinstance(r[1], dt.date) else dt.date.fromisoformat(str(r[1])),
              r[2] if isinstance(r[2], dt.date) else dt.date.fromisoformat(str(r[2])), float(r[3]))
        for r in cursor.fetchall()
    ]


# ── driver ────────────────────────────────────────────────────────────────────


def _fmt(value: float | None, spec: str) -> str:
    return "—" if value is None else format(value, spec)


def report(pooled: dict, by_season: dict, result: dict) -> str:
    lines = ["lead  matched/eligible  hit rate  mean err cm  mean |err| cm"]
    for lead, c in pooled.items():
        lines.append(f"{lead:>4}  {c['matched']:>7}/{c['eligible']:<8}  {c['hit_rate']:>8.1%}  "
                     f"{_fmt(c['mean_error_cm'], '+11.2f')}  {_fmt(c['mean_abs_error_cm'], '13.2f')}")
    lines.append("\nby season (descriptive only)")
    for (season, lead), c in by_season.items():
        lines.append(f"  {season}  lead {lead}: {c['matched']}/{c['eligible']}")
    v = result
    lines.append(f"\ncriterion: lead {v['lead']} hit rate >= {v['threshold']:.0%} -> "
                 f"{v['status'].upper()}"
                 + (f" ({v['matched']}/{v['eligible']} = {v['hit_rate']:.1%})"
                    if "hit_rate" in v else ""))
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--seasons", nargs="+", required=True, metavar="YYYY-YYYY")
    p.add_argument("--model-version", required=True)
    p.add_argument("--artefact-root", default=outlook_backtest.ARTEFACT_ROOT)
    p.add_argument("--bucket", default=None)
    p.add_argument("--location-prefix", default="")
    p.add_argument("--out", type=Path, default=REPO_ROOT / "var" / "r11-evaluation.json")
    p.add_argument("--upload", action="store_true",
                   help=f"Also write the result under {EVALUATION_ROOT}/.")
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    load_cli_env()
    from ingestion.loaders.s3_client import build_s3_client

    bucket = args.bucket or os.environ.get("S3_BUCKET_NAME")
    if not bucket:
        logger.error("pass --bucket or set S3_BUCKET_NAME")
        return 1
    if not args.artefact_root.startswith("research/"):
        logger.error("--artefact-root must be under research/")
        return 1

    config = outlook_m1.load_config()
    first_month = int(config["season_first_month"])
    client = build_s3_client()
    issues = [d for s in args.seasons for d in rc.season_issue_dates(s, first_month)]
    outlooks = fetch_outlooks(client, bucket, args.artefact_root, args.model_version, issues)
    real = fetch_real_events(args.location_prefix)
    logger.info("%d issue dates with a run, %d real events in dim_snowfall_event",
                len(outlooks), len(real))

    rows = match_events(real, outlooks, first_month)
    pooled = summarise(rows)
    by_season = summarise(rows, group_by_season=True)
    result = verdict(pooled)
    print(report(pooled, by_season, result))

    payload = {
        "registration": "docs/dev/design/20260927-r11-forward-evaluation-proposal.md §5.1",
        "model_version": args.model_version,
        "seasons": args.seasons,
        "issue_dates_with_run": len(outlooks),
        "verdict": result,
        "pooled": {str(k): v for k, v in pooled.items()},
        "by_season": {f"{s}/{k}": v for (s, k), v in by_season.items()},
        "rows": [asdict(r) for r in rows],
        "code_git_sha": outlook_m1.git_sha(),
        "created_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if args.upload:
        stamp = payload["created_at"].replace(":", "").replace("-", "")
        key = f"{EVALUATION_ROOT}/{args.model_version}/question2-{stamp}.json"
        client.put_object(Bucket=bucket, Key=key, Body=args.out.read_bytes())
        logger.info("uploaded s3://%s/%s", bucket, key)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
