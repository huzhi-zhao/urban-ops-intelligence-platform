"""Summarise `dq_audit_log` into per-dimension pass rates, trend and streaks.

    TRINO_HOST=localhost TRINO_PORT=8090 make dq-scorecard

🔴 **A reader, not a second source of truth.** It re-runs no check and writes
no table. `dq_audit_log` is already the one place an observation lives; a
stored summary beside it would be a second number that can disagree with the
first, and the disagreement would surface as an argument rather than an error
(design §3).

🟡 **The trend needs points before it means anything.** With a handful of runs
on record, "consecutive failing days" is 0 or 1 for everything. That is not a
defect — but do not read an all-green scorecard as evidence that the trend
check works. The same caveat the second batch recorded in §6.2.

🔴 Discord delivery is conditional on purpose (W-O3): a daily all-green
summary is unread within a fortnight, which is the same failure mode as a
permanently red DAG. It posts when there is a finding or when a dimension's
pass rate moved.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Any

from scripts._env import load_cli_env
from scripts.ddl.apply_ddl import TrinoConfigError, _connect, load_trino_settings, schema_name
from scripts.dq.audit_store import meta_schema
from scripts.dq.run_audit import notify
from scripts.gold.build_gold import HOST_SHELL_HINT

# Only error-level checks count towards a pass rate. A warn is information, and
# folding it in would make the rate move for reasons nobody intends to act on.
DIMENSION_SQL = """
SELECT dimension,
       COUNT_IF(passed) AS passed,
       COUNT(*) AS total,
       COUNT_IF(error_text IS NOT NULL) AS could_not_run
FROM {schema}.dq_audit_log
WHERE run_id = {run_id} AND severity = 'error'
GROUP BY dimension
ORDER BY dimension
"""

# The previous run *of the same cadence*. Comparing a daily run against a
# weekly one would report a swing that is only the rule selection changing.
PREVIOUS_RUN_SQL = """
SELECT run_id FROM (
    SELECT run_id, MAX(checked_at) AS latest
    FROM {schema}.dq_audit_log
    WHERE cadence = {cadence} AND run_id <> {run_id}
    GROUP BY run_id
)
ORDER BY latest DESC
LIMIT 1
"""

LATEST_RUN_SQL = """
SELECT run_id, cadence FROM {schema}.dq_audit_log ORDER BY checked_at DESC LIMIT 1
"""

# Consecutive runs a rule has been failing, counted back from the newest.
# 🔴 This is the only form chronic degradation takes that anyone can see: a
# rule that fails one day in three never looks urgent in a single run.
STREAK_SQL = """
SELECT rule_id, table_name, run_rank, passed, error_text
FROM (
    SELECT rule_id, table_name, passed, error_text,
           DENSE_RANK() OVER (PARTITION BY rule_id, table_name
                              ORDER BY checked_at DESC) AS run_rank
    FROM {schema}.dq_audit_log
    WHERE severity = 'error'
)
WHERE run_rank <= {depth}
ORDER BY rule_id, table_name, run_rank
"""


@dataclass(frozen=True)
class DimensionScore:
    dimension: str
    passed: int
    total: int
    could_not_run: int
    previous_rate: float | None = None

    @property
    def rate(self) -> float:
        return 100.0 * self.passed / self.total if self.total else 0.0

    @property
    def delta(self) -> float | None:
        return None if self.previous_rate is None else self.rate - self.previous_rate


def _rows(connection: Any, sql: str) -> list[tuple]:
    cursor = connection.cursor()
    cursor.execute(sql)
    return list(cursor.fetchall())


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def dimension_scores(
    connection: Any, schema: str, run_id: str, previous_run_id: str | None
) -> list[DimensionScore]:
    previous: dict[str, float] = {}
    if previous_run_id:
        previous = {
            str(d): (100.0 * int(p) / int(t) if int(t) else 0.0)
            for d, p, t, _ in _rows(
                connection, DIMENSION_SQL.format(schema=schema, run_id=_literal(previous_run_id))
            )
        }
    return [
        DimensionScore(str(d), int(p), int(t), int(c), previous.get(str(d)))
        for d, p, t, c in _rows(
            connection, DIMENSION_SQL.format(schema=schema, run_id=_literal(run_id))
        )
    ]


def failing_streaks(connection: Any, schema: str, depth: int = 14) -> dict[tuple[str, str], int]:
    """How many consecutive most-recent runs each rule has been failing.

    Counted from the newest run backwards and stopped at the first pass, so a
    rule that failed a week ago and has been green since scores 0.
    """
    streaks: dict[tuple[str, str], int] = {}
    seen_pass: set[tuple[str, str]] = set()
    for rule_id, table_name, _rank, passed, error_text in _rows(
        connection, STREAK_SQL.format(schema=schema, depth=depth)
    ):
        key = (str(rule_id), str(table_name))
        if key in seen_pass:
            continue
        # A check that could not run is not a data failure, but it is not a
        # pass either — it neither extends the streak nor resets it.
        if error_text is not None:
            continue
        if passed:
            seen_pass.add(key)
            continue
        streaks[key] = streaks.get(key, 0) + 1
    return {k: v for k, v in streaks.items() if v > 0}


def render(
    run_id: str, cadence: str, scores: list[DimensionScore], streaks: dict[tuple[str, str], int]
) -> str:
    lines = [
        f"DQ scorecard · run {run_id} ({cadence})",
        "",
        "| 维度 | error 级通过率 | 通过/总数 | 无法执行 | 环比 |",
        "|---|---|---|---|---|",
    ]
    for score in scores:
        delta = "—" if score.delta is None else f"{score.delta:+.1f} 点"
        lines.append(
            f"| {score.dimension} | {score.rate:.1f}% | {score.passed}/{score.total} | "
            f"{score.could_not_run} | {delta} |"
        )
    if streaks:
        lines += ["", "连续失败（error 级，最多回溯 14 趟）："]
        for (rule_id, table_name), runs in sorted(streaks.items(), key=lambda kv: -kv[1]):
            lines.append(f"- `{rule_id}` / `{table_name}`: {runs} 趟")
    else:
        lines += ["", "连续失败：无"]
    return "\n".join(lines)


def summarise(scores: list[DimensionScore], streaks: dict[tuple[str, str], int]) -> tuple[str, bool]:
    """(message, worth_pushing). See W-O3 on why a green day stays quiet."""
    broken = sum(s.could_not_run for s in scores)
    failing = sum(s.total - s.passed for s in scores)
    moved = [s for s in scores if s.delta is not None and abs(s.delta) >= 0.05]
    if broken:
        marker = "🔴"
    elif failing:
        marker = "❌"
    elif moved:
        marker = "⚠️"
    else:
        marker = "✅"
    head = (
        f"{marker} DQ scorecard: {failing} failing · {broken} could not run · "
        f"{len(streaks)} rule(s) failing consecutively"
    )
    body = [head]
    for score in scores:
        delta = "" if score.delta is None else f" ({score.delta:+.1f} 点)"
        body.append(f"- {score.dimension}: {score.rate:.1f}% ({score.passed}/{score.total}){delta}")
    for (rule_id, table_name), runs in sorted(streaks.items(), key=lambda kv: -kv[1]):
        body.append(f"- 连红 {runs} 趟: {rule_id} / {table_name}")
    return "\n".join(body), bool(broken or failing or moved)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=None, help="defaults to the most recent run")
    parser.add_argument("--location-prefix", default="", help="smoke namespace, as in apply_ddl")
    parser.add_argument("--depth", type=int, default=14, help="how many runs back a streak looks")
    parser.add_argument(
        "--notify",
        action="store_true",
        help="push to Discord when there is something to say (W-O3)",
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

    schema = meta_schema(args.location_prefix)
    try:
        connection = _connect(settings, schema_name("gold", args.location_prefix))
        if args.run_id:
            run_id, cadence = args.run_id, ""
            rows = _rows(
                connection,
                f"SELECT cadence FROM {schema}.dq_audit_log "
                f"WHERE run_id = {_literal(run_id)} LIMIT 1",
            )
            cadence = str(rows[0][0]) if rows else ""
        else:
            rows = _rows(connection, LATEST_RUN_SQL.format(schema=schema))
            if not rows:
                print("dq_audit_log is empty — nothing to score", file=sys.stderr)
                return 3
            run_id, cadence = str(rows[0][0]), str(rows[0][1])
        previous_rows = _rows(
            connection,
            PREVIOUS_RUN_SQL.format(
                schema=schema, cadence=_literal(cadence), run_id=_literal(run_id)
            ),
        )
        previous_run_id = str(previous_rows[0][0]) if previous_rows else None
        scores = dimension_scores(connection, schema, run_id, previous_run_id)
        streaks = failing_streaks(connection, schema, args.depth)
    except Exception as exc:  # noqa: BLE001
        print(f"\n{HOST_SHELL_HINT}\n\nunderlying error: {type(exc).__name__}: {exc}")
        return 3

    print(render(run_id, cadence, scores, streaks))
    message, worth_pushing = summarise(scores, streaks)
    print("\n" + message)
    if args.notify and worth_pushing:
        notify(message)
    # Reading the log never fails on what it read. Same reasoning as certify.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
