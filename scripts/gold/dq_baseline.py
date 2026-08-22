"""Collect the L2 data-quality baseline: row count and per-column null rate.

One pass over the 13 built Gold tables, printing markdown that goes straight
into the launch doc's §3 table. This is the *baseline* — L3's E6 summarises
it, it does not re-measure it.

The tables and their build order come from build_gold.TABLES, so a table
added there is picked up here without a second list to keep in step.

Null rates are computed as ``COUNT(*) - COUNT(col)`` in a single SELECT per
table: one scan, and the numerator and denominator come from the same
statement, so they cannot disagree. Ratios are formed once, at the end, from
counts (gold-sql.md R3) — never averaged.

Every Gold table is small (largest is 141,377 rows) and unpartitioned, so
O13's partition-count wall does not apply: nothing here reads Silver.

    TRINO_HOST=localhost TRINO_PORT=8090 make gold-dq
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from typing import Any

from scripts._env import load_cli_env
from scripts.ddl.apply_ddl import TrinoConfigError, _connect, load_trino_settings, schema_name
from scripts.ddl.ddl_parser import DDL_DIR, parse_ddl_file
from scripts.gold.build_gold import HOST_SHELL_HINT, TABLES, _quote


class TableReport:
    def __init__(self, name: str, stage: str) -> None:
        self.name = name
        self.stage = stage
        self.rows = 0
        self.elapsed = 0.0
        # column -> null count
        self.nulls: dict[str, int] = {}

    @property
    def all_null(self) -> list[str]:
        return [c for c, n in self.nulls.items() if self.rows and n == self.rows]

    @property
    def partial_null(self) -> list[tuple[str, int]]:
        return [(c, n) for c, n in self.nulls.items() if 0 < n < self.rows]

    def rate(self, nulls: int) -> float:
        return 0.0 if not self.rows else 100.0 * nulls / self.rows


def profile(conn: Any, name: str, stage: str) -> TableReport:
    ddl = parse_ddl_file(DDL_DIR / f"{name}.sql")
    columns = [c.name for c in ddl.columns]
    projections = ", ".join(
        f"COUNT(*) - COUNT({_quote(c)})" for c in columns
    )
    sql = f"SELECT COUNT(*), {projections} FROM {name}"

    report = TableReport(name, stage)
    started = dt.datetime.now(dt.UTC)
    cur = conn.cursor()
    cur.execute(sql)
    row = cur.fetchone()
    report.elapsed = (dt.datetime.now(dt.UTC) - started).total_seconds()
    report.rows = int(row[0])
    report.nulls = {c: int(v) for c, v in zip(columns, row[1:], strict=True)}
    return report


def render(reports: list[TableReport]) -> str:
    out: list[str] = []
    out.append("| 表 | 段 | 行数 | 列数 | 有空值的列 | 全空列 | 查询耗时 |")
    out.append("|---|---|---|---|---|---|---|")
    for r in reports:
        out.append(
            f"| `{r.name}` | {r.stage} | {r.rows:,} | {len(r.nulls)} | "
            f"{len(r.partial_null)} | {len(r.all_null)} | {r.elapsed:.1f}s |"
        )

    out.append("")
    out.append("### 逐列空值率（只列非零的列；未列出的列空值率为 0%）")
    out.append("")
    out.append("| 表 | 列 | 空值数 | 空值率 |")
    out.append("|---|---|---|---|")
    any_row = False
    for r in reports:
        for col, nulls in sorted(r.partial_null + [(c, r.rows) for c in r.all_null]):
            any_row = True
            flag = " 🔴 全空" if nulls == r.rows and r.rows else ""
            out.append(f"| `{r.name}` | `{col}` | {nulls:,} | {r.rate(nulls):.2f}%{flag} |")
    if not any_row:
        out.append("| — | — | 0 | 所有列空值率均为 0% |")
    return "\n".join(out)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        default=None,
        help="a stage (seeds|dims|facts|scoring|all) or a single table name",
    )
    parser.add_argument("--location-prefix", default="", help="smoke namespace, as in apply_ddl")
    return parser


def main(argv: list[str] | None = None) -> int:
    load_cli_env()
    args = build_parser().parse_args(argv)
    selected = [t for t in TABLES if args.only in (None, "all", t.stage, t.name)]
    if not selected:
        print(f"--only {args.only!r} matched no table or stage.", file=sys.stderr)
        return 2

    try:
        settings = load_trino_settings()
    except TrinoConfigError as exc:
        print(f"{exc}\n\n{HOST_SHELL_HINT}", file=sys.stderr)
        return 2

    gold_schema = schema_name("gold", args.location_prefix)
    print(f"schema={gold_schema}  tables={len(selected)}", file=sys.stderr)
    try:
        conn = _connect(settings, gold_schema)
    except Exception as exc:  # noqa: BLE001
        print(f"\n{HOST_SHELL_HINT}\n\nunderlying error: {type(exc).__name__}: {exc}")
        return 2

    reports: list[TableReport] = []
    for table in selected:
        print(f"--- {table.name}", file=sys.stderr, flush=True)
        reports.append(profile(conn, table.name, table.stage))

    empty = [r.name for r in reports if r.rows == 0]
    print(render(reports))
    if empty:
        # A zero-row Gold table is never expected after L2: the build's own
        # row-count gates would have failed first. Say so loudly rather than
        # emitting a table of 0% null rates that looks clean.
        print(f"\n🔴 零行的表（基线无意义，先查构建）：{', '.join(empty)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
