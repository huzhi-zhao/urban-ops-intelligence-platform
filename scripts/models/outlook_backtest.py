"""Run M1's forward chain over every reconstructed issue date — H2-R11 batch B.

Design: ``docs/dev/design/20260927-r11-forward-evaluation-proposal.md`` §4.

The same chain as ``outlook_m1`` — same weather splice, event rule, features
and artefact shape — driven over a season's worth of issue dates with the
inputs that do not change between them (model metrics, M1 panel, observed
archive) read once instead of 181 times.

This produces outlooks, not an evaluation. Scoring them against what happened
is batch C, whose criteria are registered before anyone looks (§5.0).

🔴 Both roots must start with ``research/``. The input is reconstructed, not
collected; its outlooks filed under the production artefact root would be
indistinguishable from what the chain really said on those days.

    python -m scripts.models.outlook_backtest --seasons 2024-2025 2025-2026 \\
        --model-version m1-poisson-20260822-df31d954 --upload
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
from pathlib import Path

from models.request_forecast import features as feat
from models.request_forecast import outlook as ol
from models.request_forecast.outlook_weather import OutlookWeatherError
from scripts._env import load_cli_env
from scripts.models import outlook_m1, train_m1
from scripts.models import reconstruct_forecast as rc

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ARTEFACT_ROOT = "research/r11/outlook_runs"
BACKTEST_MARKER = {"reconstructed_input": True, "study": "H2-R11"}


def check_roots(bronze_root: str, artefact_root: str) -> None:
    for name, root in (("--bronze-root", bronze_root), ("--artefact-root", artefact_root)):
        if not root.startswith("research/"):
            raise outlook_m1.OutlookRunError(f"{name} {root!r} must start with 'research/'")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--seasons", nargs="+", required=True, metavar="YYYY-YYYY")
    p.add_argument("--model-version", required=True)
    p.add_argument("--bronze-root", default=rc.RESEARCH_ROOT)
    p.add_argument("--artefact-root", default=ARTEFACT_ROOT)
    p.add_argument("--bucket", default=None, help="Overrides S3_BUCKET_NAME.")
    p.add_argument("--out-dir", type=Path, default=REPO_ROOT / "var" / "r11-runs")
    p.add_argument("--upload", action="store_true",
                   help="Write artefacts to object storage; without it they stay in --out-dir.")
    p.add_argument("--location-prefix", default="")
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    load_cli_env()
    bucket = args.bucket or os.environ.get("S3_BUCKET_NAME")

    try:
        check_roots(args.bronze_root, args.artefact_root)
        config = outlook_m1.load_config()
        m1_config = train_m1.load_config()
        root = outlook_m1.resolve_artefact_root(config, args.bronze_root, args.artefact_root)
        if not bucket:
            raise outlook_m1.OutlookRunError("pass --bucket or set S3_BUCKET_NAME")
        metrics = outlook_m1.fetch_metrics(args.model_version, bucket)
        if metrics.get("model_version") != args.model_version:
            raise outlook_m1.OutlookRunError(f"metrics are not for {args.model_version!r}")
        panel = train_m1.to_role_names(train_m1.fetch_panel(m1_config, args.location_prefix),
                                       m1_config)
    except outlook_m1.OutlookRunError as exc:
        logger.error("%s", exc)
        return 1

    lookback = dt.timedelta(days=int(config["observed_lookback_days"]))
    first_month = int(config["season_first_month"])
    done = skipped = events = 0
    failed: list[str] = []

    for season in args.seasons:
        issues = rc.season_issue_dates(season, first_month)
        # One read per season. Days on or after an issue date are dropped by
        # the splice for that issue, so a wider archive cannot leak into it.
        observed = outlook_m1.fetch_archive(issues[0] - lookback, issues[-1],
                                            args.location_prefix)
        logger.info("%s: %d issue dates, %d observed days", season, len(issues), len(observed))

        for issue in issues:
            prefix = outlook_m1.artefact_prefix(root, issue, args.model_version)
            if args.upload and outlook_m1.run_exists(prefix, bucket):
                skipped += 1
                continue
            try:
                records, checksum, source = outlook_m1.fetch_forecast_vintage(
                    config, issue, bucket, args.bronze_root)
                weather = outlook_m1.build_weather(config, issue, observed, records,
                                                   rc.MAX_LEAD_DAYS)
                outlook, run = outlook_m1.run_outlook(
                    config=config, m1_config=m1_config, issue_date=issue,
                    panel=panel, metrics=metrics, weather=weather)
                output = outlook_m1.to_output(outlook, m1_config, issue, args.model_version)
                run.update(outlook_m1.run_provenance(m1_config, panel, metrics,
                                                     args.model_version, source, checksum))
                run.update(BACKTEST_MARKER)
                paths = outlook_m1.write_local(args.out_dir, prefix, output, run)
                if args.upload:
                    outlook_m1.upload(paths, prefix, bucket)
            except (outlook_m1.OutlookRunError, OutlookWeatherError, ol.OutlookError,
                    feat.PanelError) as exc:
                logger.error("issue %s: %s", issue, exc)
                failed.append(issue.isoformat())
                continue
            done += 1
            events += run["event_count"]

    logger.info("scored %d issue dates (%d events in total), %d already present, %d failed",
                done, events, skipped, len(failed))
    if failed:
        logger.error("failed issue dates: %s", ", ".join(failed))
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
