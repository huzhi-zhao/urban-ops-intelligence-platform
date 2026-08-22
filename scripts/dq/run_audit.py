"""Run the out-of-pipeline DQ audit and append the results to `dq_audit_log`.

    TRINO_HOST=localhost TRINO_PORT=8090 make dq-audit
    TRINO_HOST=localhost TRINO_PORT=8090 make dq-audit CADENCE=manual   # full sweep

🔴 **A finding does not fail this run.** Data already on disk cannot be fixed
by exiting non-zero, and a task that goes red on a finding stays red until a
human repairs the data by hand — which is how a DAG gets muted (ADR 0012 §1,
rule 2, and the same reasoning `dag_audit_bronze` already runs under). What
*does* fail is a check that could not be executed at all: that is the audit
being broken, not the data.

Exit codes: 0 = every check ran (findings or not) · 2 = a check could not run
· 3 = configuration/connection problem.

Three things about how the checks are executed:

* **Structural checks are not re-implemented.** `scripts.gold.dq_assertions`
  already turns the DDL headers into 185 executable assertions and L3-c ran
  them at 0 violations; this module is its second consumer. Editing an
  assertion still means editing a DDL header. Its "a nullable column is checked
  only on its non-null rows" semantics come along for free, which is the point:
  re-deriving them would fail the seven documented-NULL columns and mute the
  baseline.
* **One profile scan per table serves several rules.** Row count, null rates,
  zero-row and all-null checks all come from a single
  `COUNT(*), COUNT(*)-COUNT(col)…` per table (`dq_baseline.profile`), so 17
  tables cost 17 queries, not 60. Rules derived from the same scan report that
  scan's elapsed time.
* **Silver is never scanned whole** (design §4.3, O13's wall). Freshness reads
  `"…$partitions"`, which is Metastore-only, and the two Silver business rules
  carry a 14-day `open_date_local` window substituted into their SQL.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import uuid
from dataclasses import dataclass
from typing import Any

import requests

from scripts._env import load_cli_env
from scripts.ddl.apply_ddl import TrinoConfigError, _connect, load_trino_settings, schema_name
from scripts.dq import audit_store
from scripts.dq.audit_store import Observation
from scripts.dq.rules import Rule, evaluate, load_rules
from scripts.gold.build_gold import HOST_SHELL_HINT, TABLES, _resolve_alert_url
from scripts.gold.dq_assertions import assertions_for
from scripts.gold.dq_baseline import profile

DEFAULT_WINDOW_DAYS = 14
CONTENT_LIMIT = 1900
TIMEOUT_SECS = 10
# One row in the log for a check whose scope is "all Gold tables at once".
WHOLE_SCHEMA = "*"


@dataclass
class Context:
    connection: Any
    silver_schema: str
    window_start: dt.date
    window_end: dt.date
    profiles: dict[str, Any]

    def profile_of(self, table: str) -> Any:
        if table not in self.profiles:
            stage = next((t.stage for t in TABLES if t.name == table), "")
            self.profiles[table] = profile(self.connection, table, stage)
        return self.profiles[table]


class CheckError(RuntimeError):
    """The check could not be executed. Distinct from the check failing."""


def expand(rules: list[Rule]) -> list[tuple[Rule, str]]:
    """Pair every rule with the concrete table it observes.

    `table: "*"` means one result per Gold table — except for the two
    whole-schema shape checks, which are one number about all of them.
    """
    pairs: list[tuple[Rule, str]] = []
    for rule in rules:
        if rule.table != "*":
            pairs.append((rule, rule.table))
        elif rule.check_type in ("empty_tables", "all_null_columns"):
            pairs.append((rule, WHOLE_SCHEMA))
        else:
            pairs.extend((rule, table.name) for table in TABLES)
    return pairs


def _listed_null_columns(rules: list[Rule]) -> set[tuple[str, str]]:
    """(table, column) pairs that have a null-rate rule of their own.

    The catch-all "every other column is 0% null" rule must not also fire on
    the seven documented-NULL columns; they are watched for *movement*, not
    pinned to zero.
    """
    return {
        (r.table, str(r.check["column"]))
        for r in rules
        if r.check_type == "null_rate" and "column" in r.check
    }


def _scalar(connection: Any, sql: str) -> Any:
    cursor = connection.cursor()
    cursor.execute(sql)
    row = cursor.fetchone()
    return None if row is None else row[0]


def run_check(
    rule: Rule, table: str, ctx: Context, listed: set[tuple[str, str]]
) -> tuple[float | None, int | None, int | None]:
    """Execute one check. Returns (observed, rows_checked, rows_failed).

    Raises CheckError when the check could not run — never when it failed.
    """
    kind = rule.check_type
    try:
        if kind == "ddl_assertions":
            checks = assertions_for(table)
            violations = 0
            for check in checks:
                violations += int(_scalar(ctx.connection, check.sql) or 0)
            return float(violations), len(checks), violations

        if kind == "row_count":
            report = ctx.profile_of(table)
            return float(report.rows), report.rows, None

        if kind == "null_rate":
            report = ctx.profile_of(table)
            if "column" in rule.check:
                column = str(rule.check["column"])
                nulls = report.nulls.get(column)
                if nulls is None:
                    raise CheckError(f"{table} has no column {column!r}")
                return report.rate(nulls), report.rows, nulls
            # scope: unlisted_columns — how many other columns carry any null
            offenders = [
                (c, n)
                for c, n in report.nulls.items()
                if n > 0 and (table, c) not in listed
            ]
            return float(len(offenders)), len(report.nulls), sum(n for _, n in offenders)

        if kind == "empty_tables":
            empty = [t.name for t in TABLES if ctx.profile_of(t.name).rows == 0]
            return float(len(empty)), len(TABLES), len(empty)

        if kind == "all_null_columns":
            count = sum(len(ctx.profile_of(t.name).all_null) for t in TABLES)
            return float(count), len(TABLES), count

        if kind == "partition_freshness":
            # Metastore-only: `$partitions` never touches a data file, which is
            # what keeps a daily freshness check off the 4,878-object wall.
            newest = _scalar(
                ctx.connection,
                f'SELECT MAX({rule.check["partition_column"]}) '
                f'FROM {ctx.silver_schema}."{table}$partitions"',
            )
            if newest is None:
                raise CheckError(f"{table} has no partitions at all")
            if isinstance(newest, str):
                newest = dt.date.fromisoformat(newest)
            return float((dt.date.today() - newest).days), None, None

        if kind == "sql":
            sql = (
                str(rule.check["sql"])
                .replace("{{ silver }}", ctx.silver_schema)
                .replace("{window_start}", ctx.window_start.isoformat())
                .replace("{window_end}", ctx.window_end.isoformat())
            )
            value = _scalar(ctx.connection, sql)
            if value is None:
                raise CheckError("query returned no row")
            return float(value), None, None
    except CheckError:
        raise
    except Exception as exc:  # noqa: BLE001 - any Trino error means "could not run"
        raise CheckError(f"{type(exc).__name__}: {exc}") from exc

    raise CheckError(f"no implementation for check type {kind!r}")


def render_expected(rule: Rule) -> str:
    if rule.comparator == "between":
        return f"between {rule.expected[0]} and {rule.expected[1]}"
    if rule.comparator == "pct_change":
        return f"within ±{rule.expected}% of previous"
    if rule.comparator == "pct_point_change":
        return f"within ±{rule.expected} points of previous"
    return f"{rule.comparator} {rule.expected}"


def audit(
    ctx: Context, rules: list[Rule], cadence: str, previous: dict[tuple[str, str], float]
) -> list[Observation]:
    run_id = f"dq-{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
    listed = _listed_null_columns(rules)
    selected = [(r, t) for r, t in expand(rules) if r.runs_on(cadence)]
    results: list[Observation] = []

    for rule, table in selected:
        started = dt.datetime.now(dt.UTC)
        prior = previous.get((rule.id, table))
        observed: float | None = None
        rows_checked: int | None = None
        rows_failed: int | None = None
        error_text: str | None = None
        try:
            observed, rows_checked, rows_failed = run_check(rule, table, ctx, listed)
            passed = evaluate(rule, observed, prior)
        except CheckError as exc:
            error_text = str(exc)
            passed = False
        elapsed = (dt.datetime.now(dt.UTC) - started).total_seconds()
        if rule.check_type in ("row_count", "null_rate") and table in ctx.profiles:
            elapsed = max(elapsed, ctx.profiles[table].elapsed)

        results.append(
            Observation(
                run_id=run_id,
                cadence=cadence,
                layer=rule.layer,
                table_name=table,
                rule_id=rule.id,
                dimension=rule.dimension,
                severity=rule.severity,
                observed=observed,
                expected=render_expected(rule),
                comparator=rule.comparator,
                passed=passed,
                previous_observed=prior,
                rows_checked=rows_checked,
                rows_failed=rows_failed,
                elapsed_s=round(elapsed, 3),
                checked_at=dt.datetime.now(dt.UTC).replace(tzinfo=None),
                error_text=error_text,
            )
        )
    return results


def render(results: list[Observation]) -> str:
    out = ["| rule | table | 维度 | 期望 | 实测 | 上次 | 结果 | 耗时 |", "|---|---|---|---|---|---|---|---|"]
    for r in results:
        mark = "✅" if r.passed else ("🔴 无法执行" if r.could_not_run else "❌")
        observed = "—" if r.observed is None else f"{r.observed:,.4g}"
        prior = "—" if r.previous_observed is None else f"{r.previous_observed:,.4g}"
        out.append(
            f"| `{r.rule_id}` | `{r.table_name}` | {r.dimension} | {r.expected} | "
            f"{observed} | {prior} | {mark} | {r.elapsed_s:.1f}s |"
        )
    return "\n".join(out)


def notify(text: str) -> bool:
    """Push findings to the same Discord channel every other UOIP job uses.

    Never raises: an alert channel that is down must not turn into an audit
    that "could not run", which is the one thing that does fail the task.
    """
    url = _resolve_alert_url()
    if not url:
        print("no alert webhook configured — findings were not pushed", file=sys.stderr)
        return False
    try:
        response = requests.post(
            url,
            json={"content": text[:CONTENT_LIMIT], "allowed_mentions": {"parse": []}},
            timeout=TIMEOUT_SECS,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"could not deliver DQ notice: {type(exc).__name__}", file=sys.stderr)
        return False
    return True


def summarise(results: list[Observation], cadence: str) -> str:
    errors = [r for r in results if not r.passed and r.severity == "error" and not r.could_not_run]
    warns = [r for r in results if not r.passed and r.severity == "warn" and not r.could_not_run]
    broken = [r for r in results if r.could_not_run]
    head = (
        f"DQ audit ({cadence}): {len(results)} checks, "
        f"{len(errors)} error · {len(warns)} warn · {len(broken)} could not run"
    )
    lines = [head]
    for r in errors + warns + broken:
        detail = r.error_text or f"observed {r.observed}, expected {r.expected}"
        lines.append(f"- [{r.severity}] {r.rule_id} / {r.table_name}: {detail}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cadence", default="daily", choices=("daily", "weekly", "manual"))
    parser.add_argument(
        "--full-sweep",
        action="store_true",
        help="shorthand for --cadence manual: every rule regardless of cadence",
    )
    parser.add_argument("--location-prefix", default="", help="smoke namespace, as in apply_ddl")
    parser.add_argument(
        "--bucket", default=None, help="object storage bucket; defaults to S3_BUCKET_NAME"
    )
    parser.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run every check and print, but append nothing to dq_audit_log",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    load_cli_env()
    args = build_parser().parse_args(argv)
    cadence = "manual" if args.full_sweep else args.cadence

    try:
        settings = load_trino_settings()
    except TrinoConfigError as exc:
        print(f"{exc}\n\n{HOST_SHELL_HINT}", file=sys.stderr)
        return 3

    rules = load_rules()
    gold_schema = schema_name("gold", args.location_prefix)
    silver_schema = schema_name("silver", args.location_prefix)
    today = dt.date.today()

    try:
        if args.dry_run:
            # A dry run touches nothing: it neither creates the log table nor
            # reads a baseline from it, so a first look at a new deployment
            # cannot leave a half-made table behind.
            meta = audit_store.meta_schema(args.location_prefix)
            previous: dict[tuple[str, str], float] = {}
            connection = _connect(settings, gold_schema)
        else:
            meta = audit_store.ensure_table(settings, _bucket(args.bucket), args.location_prefix)
            connection = _connect(settings, gold_schema)
            previous = audit_store.previous_observations(connection, meta)
    except Exception as exc:  # noqa: BLE001
        print(f"\n{HOST_SHELL_HINT}\n\nunderlying error: {type(exc).__name__}: {exc}")
        return 3

    ctx = Context(
        connection=connection,
        silver_schema=silver_schema,
        window_start=today - dt.timedelta(days=args.window_days),
        window_end=today + dt.timedelta(days=1),
        profiles={},
    )
    print(f"cadence={cadence} gold={gold_schema} meta={meta} rules={len(rules)}", file=sys.stderr)

    results = audit(ctx, rules, cadence, previous)
    print(render(results))
    summary = summarise(results, cadence)
    print("\n" + summary)

    if not args.dry_run:
        written = audit_store.append(connection, meta, results)
        print(f"\nappended {written} row(s) to {meta}.{audit_store.TABLE}")

    if any(not r.passed for r in results):
        notify(summary)

    broken = [r for r in results if r.could_not_run]
    if broken:
        # 🔴 The only failing exit: the audit itself is broken, not the data.
        print(f"\n🔴 {len(broken)} check(s) could not run — the audit is broken", file=sys.stderr)
        return 2
    return 0


def _bucket(explicit: str | None = None) -> str:
    import os

    bucket = (explicit or os.environ.get("S3_BUCKET_NAME") or "").strip()
    if not bucket:
        raise RuntimeError("No bucket: set S3_BUCKET_NAME.")
    return bucket


if __name__ == "__main__":
    raise SystemExit(main())
