"""Turn one audit run's observations into one verdict about the Gold batch.

    TRINO_HOST=localhost TRINO_PORT=8090 make dq-certify

Reads `uoip_meta.dq_audit_log` for a single `run_id` and appends one row to
`uoip_meta.gold_certification`. It runs nothing and re-checks nothing: the
audit is the only place a number is measured, and a second measurement here
would be a second truth to reconcile.

🔴 **Three statuses, and `unknown` is not a synonym for `suspect`.**
`suspect` = we looked and the data is wrong. `unknown` = we could not look.
Merging them hands out a conclusion-shaped answer on the day the audit itself
was broken — and downstream, "no suspect row today" reads as "no problem"
(design §0.3).

🔴 **`certified` is decided by error-level checks alone.** A warn-level
finding is counted and shown but never blocks, because if warns blocked, the
only way to keep the batch certified would be to demote the warn to nothing.
The counts are on the row so "green with warnings" stays visible.

🔴 **Certification blocks nothing.** Writing `suspect` does not raise, for the
same reason a finding does not fail the audit (ADR 0012 §1 rule 2). What it
changes is what downstream reads, not whether the pipeline runs.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts._env import load_cli_env
from scripts.ddl.apply_ddl import (
    TrinoConfigError,
    TrinoSettings,
    _connect,
    load_trino_settings,
    normalise_prefix,
    schema_name,
)
from scripts.dq.audit_store import META_LAYER, _literal, meta_schema
from scripts.gold.build_gold import HOST_SHELL_HINT

REPO_ROOT = Path(__file__).resolve().parents[2]
DDL_PATH = REPO_ROOT / "sql" / "meta" / "gold_certification.sql"
TABLE = "gold_certification"

CERTIFIED = "certified"
SUSPECT = "suspect"
UNKNOWN = "unknown"

COLUMNS = (
    "run_id",
    "cadence",
    "certified_at",
    "status",
    "error_count",
    "warn_count",
    "checks_total",
    "checks_could_not_run",
    "note",
)


@dataclass(frozen=True)
class Verdict:
    """What one audit run concluded about the batch as a whole."""

    run_id: str
    cadence: str
    status: str
    error_count: int
    warn_count: int
    checks_total: int
    checks_could_not_run: int
    note: str


def decide(
    *, error_count: int, warn_count: int, checks_total: int, checks_could_not_run: int
) -> tuple[str, str]:
    """The whole state machine, in the order the statuses outrank each other.

    `unknown` is tested first on purpose: a run with both a finding and a
    broken check has not established that the finding is the whole story, so
    reporting `suspect` would overstate what was learned.
    """
    if checks_total == 0:
        return UNKNOWN, "no check ran at all — the audit produced no observations"
    if checks_could_not_run > 0:
        return UNKNOWN, (
            f"{checks_could_not_run} of {checks_total} checks could not run — "
            f"this is 'we could not look', not 'the data is fine'"
        )
    if error_count > 0:
        return SUSPECT, (
            f"{error_count} error-level finding(s) across {checks_total} checks"
            + (f"; {warn_count} warn" if warn_count else "")
        )
    return CERTIFIED, (
        f"all {checks_total} checks ran, every error-level check passed"
        + (f"; {warn_count} warn-level finding(s), which do not block" if warn_count else "")
    )


SUMMARY_SQL = """
SELECT
    cadence,
    COUNT(*) AS checks_total,
    COUNT_IF(error_text IS NOT NULL) AS could_not_run,
    COUNT_IF(NOT passed AND error_text IS NULL AND severity = 'error') AS error_count,
    COUNT_IF(NOT passed AND error_text IS NULL AND severity = 'warn') AS warn_count
FROM {schema}.dq_audit_log
WHERE run_id = {run_id}
GROUP BY cadence
"""

LATEST_RUN_SQL = """
SELECT run_id FROM {schema}.dq_audit_log
ORDER BY checked_at DESC
LIMIT 1
"""


def latest_run_id(connection: Any, schema: str) -> str | None:
    cursor = connection.cursor()
    cursor.execute(LATEST_RUN_SQL.format(schema=schema))
    row = cursor.fetchone()
    return None if row is None else str(row[0])


def summarise_run(connection: Any, schema: str, run_id: str) -> Verdict:
    """Read one run out of the log and apply the state machine to it.

    A `run_id` with no rows is `unknown`, not an error: the audit having
    written nothing is exactly the case §0.3 says must not default to silence.
    """
    cursor = connection.cursor()
    cursor.execute(SUMMARY_SQL.format(schema=schema, run_id=_literal(run_id)))
    rows = cursor.fetchall()
    if not rows:
        status, note = decide(
            error_count=0, warn_count=0, checks_total=0, checks_could_not_run=0
        )
        return Verdict(run_id, "unknown", status, 0, 0, 0, 0, note)
    cadence, total, could_not_run, errors, warns = rows[0]
    status, note = decide(
        error_count=int(errors),
        warn_count=int(warns),
        checks_total=int(total),
        checks_could_not_run=int(could_not_run),
    )
    return Verdict(
        run_id=run_id,
        cadence=str(cadence),
        status=status,
        error_count=int(errors),
        warn_count=int(warns),
        checks_total=int(total),
        checks_could_not_run=int(could_not_run),
        note=note,
    )


def render_ddl(text: str, bucket: str, location_prefix: str = "") -> str:
    rendered = text.replace("{bucket}", bucket)
    prefix = normalise_prefix(location_prefix)
    if prefix:
        rendered = rendered.replace(
            f"s3a://{bucket}/{META_LAYER}/", f"s3a://{bucket}/{prefix}/{META_LAYER}/"
        )
    return rendered[rendered.index("CREATE TABLE") :].rstrip().rstrip(";")


def ensure_table(settings: TrinoSettings, bucket: str, location_prefix: str = "") -> str:
    """Create the table if absent. The schema itself is `audit_store`'s job."""
    schema = meta_schema(location_prefix)
    with _connect(settings, schema) as connection:
        cursor = connection.cursor()
        cursor.execute(
            f"CREATE SCHEMA IF NOT EXISTS {settings.catalog}.{schema} "
            f"WITH (location = 's3a://{bucket}/"
            f"{(normalise_prefix(location_prefix) + '/') if normalise_prefix(location_prefix) else ''}"
            f"{META_LAYER}/')"
        )
        cursor.fetchall()
        cursor = connection.cursor()
        cursor.execute(render_ddl(DDL_PATH.read_text(), bucket, location_prefix))
        cursor.fetchall()
    return schema


def insert_sql(schema: str, verdict: Verdict, certified_at: dt.datetime) -> str:
    values = [
        verdict.run_id,
        verdict.cadence,
        certified_at,
        verdict.status,
        verdict.error_count,
        verdict.warn_count,
        verdict.checks_total,
        verdict.checks_could_not_run,
        verdict.note,
    ]
    columns = ", ".join(COLUMNS)
    return (
        f"INSERT INTO {schema}.{TABLE} ({columns}) VALUES ("
        + ", ".join(_literal(v) for v in values)
        + ")"
    )


MARKERS = {CERTIFIED: "✅", SUSPECT: "❌", UNKNOWN: "🔴"}


def render(verdict: Verdict) -> str:
    return (
        f"{MARKERS[verdict.status]} Gold certification: {verdict.status} "
        f"({verdict.cadence}, run {verdict.run_id})\n"
        f"- {verdict.note}\n"
        f"- {verdict.checks_total} checks · {verdict.error_count} error · "
        f"{verdict.warn_count} warn · {verdict.checks_could_not_run} could not run"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-id",
        default=None,
        help="dq_audit_log run to certify; defaults to the most recent one",
    )
    parser.add_argument("--location-prefix", default="", help="smoke namespace, as in apply_ddl")
    parser.add_argument("--bucket", default=None, help="defaults to S3_BUCKET_NAME")
    parser.add_argument(
        "--dry-run", action="store_true", help="decide and print, but write no row"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    load_cli_env()
    args = build_parser().parse_args(argv)
    try:
        settings = load_trino_settings()
    except TrinoConfigError as exc:
        print(f"{exc}\n\n{HOST_SHELL_HINT}", file=sys.stderr)
        return 3

    import os

    bucket = (args.bucket or os.environ.get("S3_BUCKET_NAME") or "").strip()
    if not bucket and not args.dry_run:
        print("No bucket: set S3_BUCKET_NAME.", file=sys.stderr)
        return 3

    schema = meta_schema(args.location_prefix)
    try:
        if not args.dry_run:
            ensure_table(settings, bucket, args.location_prefix)
        connection = _connect(settings, schema_name("gold", args.location_prefix))
        run_id = args.run_id or latest_run_id(connection, schema)
    except Exception as exc:  # noqa: BLE001
        print(f"\n{HOST_SHELL_HINT}\n\nunderlying error: {type(exc).__name__}: {exc}")
        return 3

    if run_id is None:
        print("dq_audit_log is empty — nothing to certify", file=sys.stderr)
        return 3

    verdict = summarise_run(connection, schema, run_id)
    print(render(verdict))

    if not args.dry_run:
        cursor = connection.cursor()
        cursor.execute(insert_sql(schema, verdict, dt.datetime.now(dt.UTC).replace(tzinfo=None)))
        cursor.fetchall()
        print(f"\nappended 1 row to {schema}.{TABLE}")

    # 🔴 Always 0 on a verdict. `suspect` and `unknown` are conclusions the
    # table records, not failures of this step — exiting non-zero here would
    # turn every bad-data day into a red DAG, which is a muted DAG.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
