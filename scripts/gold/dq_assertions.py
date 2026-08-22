"""Run the DDL's four test families against the built Gold tables (L3-c · C3).

Every `sql/ddl/*.sql` header already states its contract in four families —
`unique`, `not_null`, `relationships`, `accepted_values` (plus per-column
`range`) — but until now only the `-- relationships:` COUNT lines were ever
executed, and only the single-line ones at that. The rest were prose: true on
the day they were written, unchecked since.

This module turns all five annotation forms into SQL and runs them:

| annotation | where it sits | assertion |
|---|---|---|
| `-- unique: [['a', 'b']]` | table header | no duplicate key tuple |
| `-- not_null` | above a column | no NULL in that column |
| `-- accepted_values: [...]` | above a column | no value outside the set |
| `-- accepted_values (col): [...]` | table header | same, stated table-side |
| `-- range: [lo, hi]` | above a column | no value outside the bounds |
| `-- relationships -> t.c` | above a column | anti-join against t.c is empty |

🔴 A nullable column is checked **only on its non-null rows**. Otherwise every
documented-NULL column (`rank_factor` on the 924 partial_no_rank cells, the two
unmatched plow events, …) would fail an accepted-values or relationship check
that it does not actually violate — and a baseline that cries wolf on seven
known-good columns gets muted, which is the failure this exists to prevent.

Read-only. Gold-only: nothing here reads Silver, so R1 does not apply.

    TRINO_HOST=localhost TRINO_PORT=8090 uv run python -m scripts.gold.dq_assertions
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from typing import Any

from scripts._env import load_cli_env
from scripts.ddl.apply_ddl import TrinoConfigError, _connect, load_trino_settings, schema_name
from scripts.ddl.ddl_parser import DDL_DIR, parse_ddl_file
from scripts.gold.build_gold import HOST_SHELL_HINT, TABLES, _quote

_UNIQUE = re.compile(r"^--\s*unique:\s*(\[.*\])\s*$")
_TABLE_ACCEPTED = re.compile(r"^--\s*accepted_values\s*\((\w+)\):\s*(\[.*\])\s*$")
_COL_ACCEPTED = re.compile(r"^--\s*accepted_values:\s*(\[.*\])\s*$")
_COL_RANGE = re.compile(r"^--\s*range:\s*\[\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\]\s*$")
_COL_REL = re.compile(r"^--\s*relationships\s*->\s*(\w+)\.(\w+)\s*$")
_COLUMN = re.compile(r"^\s{4}(\w+)\s+[A-Z]")


@dataclass(frozen=True)
class Assertion:
    table: str
    family: str
    subject: str
    sql: str


def _literals(raw: str) -> list[str]:
    """Render a Python-ish literal list from the DDL header as SQL literals."""
    values = ast.literal_eval(raw)
    out: list[str] = []
    for value in values:
        if isinstance(value, bool):
            out.append("true" if value else "false")
        elif isinstance(value, (int, float)):
            out.append(str(value))
        else:
            out.append("'" + str(value).replace("'", "''") + "'")
    return out


def assertions_for(table: str) -> list[Assertion]:
    """Every executable assertion in one DDL file, in declaration order."""
    path = DDL_DIR / f"{table}.sql"
    text = path.read_text()
    found: list[Assertion] = []

    for line in text.splitlines():
        stripped = line.strip()
        match = _UNIQUE.match(stripped)
        if match:
            for key in ast.literal_eval(match.group(1)):
                cols = ", ".join(_quote(c) for c in key)
                found.append(
                    Assertion(
                        table,
                        "unique",
                        "(" + ", ".join(key) + ")",
                        f"SELECT COUNT(*) FROM (SELECT {cols} FROM {table}"
                        f" GROUP BY {cols} HAVING COUNT(*) > 1)",
                    )
                )
        match = _TABLE_ACCEPTED.match(stripped)
        if match:
            found.append(_accepted(table, match.group(1), match.group(2)))

    # Column-level annotations: the comment lines above a column apply to the
    # first column declaration that follows them.
    pending: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            pending.append(stripped)
            continue
        match = _COLUMN.match(line)
        if not match:
            if stripped:
                pending = []
            continue
        column = match.group(1)
        for note in pending:
            acc = _COL_ACCEPTED.match(note)
            if acc:
                found.append(_accepted(table, column, acc.group(1)))
            rng = _COL_RANGE.match(note)
            if rng:
                lo, hi = rng.group(1), rng.group(2)
                found.append(
                    Assertion(
                        table,
                        "range",
                        f"{column} in [{lo}, {hi}]",
                        f"SELECT COUNT(*) FROM {table} WHERE {_quote(column)} IS NOT NULL"
                        f" AND ({_quote(column)} < {lo} OR {_quote(column)} > {hi})",
                    )
                )
            rel = _COL_REL.match(note)
            if rel:
                parent, parent_col = rel.group(1), rel.group(2)
                found.append(
                    Assertion(
                        table,
                        "relationships",
                        f"{column} -> {parent}.{parent_col}",
                        f"SELECT COUNT(*) FROM {table} c"
                        f" LEFT JOIN {parent} p ON p.{_quote(parent_col)} = c.{_quote(column)}"
                        f" WHERE c.{_quote(column)} IS NOT NULL"
                        f" AND p.{_quote(parent_col)} IS NULL",
                    )
                )
        pending = []

    # The same rule is often stated twice — once in the table header, once
    # above the column — and running it twice only inflates the count.
    for col in parse_ddl_file(path).columns:
        if col.not_null:
            found.append(
                Assertion(
                    table,
                    "not_null",
                    col.name,
                    f"SELECT COUNT(*) FROM {table} WHERE {_quote(col.name)} IS NULL",
                )
            )
    seen: set[tuple[str, str]] = set()
    unique_found: list[Assertion] = []
    for item in found:
        if (item.family, item.sql) in seen:
            continue
        seen.add((item.family, item.sql))
        unique_found.append(item)
    return unique_found


def _accepted(table: str, column: str, raw: str) -> Assertion:
    values = ", ".join(_literals(raw))
    return Assertion(
        table,
        "accepted_values",
        column,
        f"SELECT COUNT(*) FROM {table} WHERE {_quote(column)} IS NOT NULL"
        f" AND CAST({_quote(column)} AS VARCHAR) NOT IN"
        f" ({', '.join(f'CAST({v} AS VARCHAR)' for v in values.split(', '))})",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", default=None, help="a stage or a single table name")
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
    try:
        conn: Any = _connect(settings, gold_schema)
    except Exception as exc:  # noqa: BLE001
        print(f"\n{HOST_SHELL_HINT}\n\nunderlying error: {type(exc).__name__}: {exc}")
        return 2

    failures: list[str] = []
    counts: dict[str, int] = {}
    for table in selected:
        checks = assertions_for(table.name)
        print(f"--- {table.name} ({len(checks)} assertions)", file=sys.stderr, flush=True)
        for check in checks:
            counts[check.family] = counts.get(check.family, 0) + 1
            cursor = conn.cursor()
            cursor.execute(check.sql)
            violations = cursor.fetchone()[0]
            if violations:
                failures.append(f"{check.table}: {check.family} {check.subject} -> {violations:,}")

    total = sum(counts.values())
    print("\n| family | assertions |\n|---|---|")
    for family in sorted(counts):
        print(f"| `{family}` | {counts[family]} |")
    print(f"| **total** | **{total}** |")

    if failures:
        print(f"\n🔴 {len(failures)} 条违反：")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print(f"\n✅ {total} assertions across {len(selected)} tables, 0 violations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
