"""Execute every `sql/presentation/*.sql` and print it, or freeze it to JSON.

    TRINO_HOST=localhost TRINO_PORT=8090 make eda-run
    TRINO_HOST=localhost TRINO_PORT=8090 make eda-export

🔴 **This never fails on what it read** (design §3.2). An EDA query's output is
a distribution, and a distribution has no right answer. The numbers that do
need a gate already have one — `scripts/gold/gates.py` inside the pipeline and
`config/dq/rules.yaml` outside it. A third set here would be a third place for
the same number to disagree with itself. What *does* exit non-zero is a query
that could not be executed at all: that is this tool being broken, not the data.

🔴 **The catalogue is derived from the files, not kept beside them.** Design A2
forbids orphan figures and orphan SQL, and a hand-maintained list is exactly how
one appears. Every `fig_*.sql` header carries its own `fig_id` / `bo` /
`carrier` / `schema` / `criterion` / `caption` / `must_not_say`.

🔴 **`schema:` is load-bearing.** The connection's session schema comes from it,
and a bare table name under the wrong schema does not fail to resolve — it
resolves somewhere else. Same failure mode as `.claude/rules/gold-sql.md` R6.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts._env import load_cli_env
from scripts.ddl.apply_ddl import TrinoConfigError, _connect, load_trino_settings, schema_name
from scripts.gold.build_gold import HOST_SHELL_HINT

PRESENTATION_DIR = Path(__file__).resolve().parents[2] / "sql" / "presentation"
DEFAULT_EXPORT_DIR = Path("var/presentation")

# Every one of these must be present. A figure without a caption is a figure
# whose reader has to guess, and a figure without `must_not_say` is one nobody
# checked against the discipline table in the ledger §1.
REQUIRED_KEYS = ("fig_id", "bo", "carrier", "schema", "criterion", "caption", "must_not_say")
KNOWN_CARRIERS = ("echarts", "superset", "grafana")
KNOWN_SCHEMAS = ("gold", "silver", "meta")


class FigureHeaderError(ValueError):
    """A `fig_*.sql` header is missing a key or carries an unknown value."""


@dataclass(frozen=True)
class Figure:
    path: Path
    header: dict[str, str]
    sql: str

    @property
    def fig_id(self) -> str:
        return self.header["fig_id"]

    @property
    def carrier(self) -> str:
        return self.header["carrier"]

    @property
    def layer(self) -> str:
        return self.header["schema"]


def parse_header(text: str, path: Path) -> tuple[dict[str, str], str]:
    """Split a figure file into its `-- key: value` header and its SQL body.

    Continuation lines (`--   more text`) append to the previous key, so a
    caption may span lines without becoming unreadable in the file.
    """
    header: dict[str, str] = {}
    body: list[str] = []
    last_key: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if body or not stripped.startswith("--"):
            if stripped or body:
                body.append(line)
            continue
        comment = stripped[2:].strip()
        key, sep, value = comment.partition(":")
        if sep and key and " " not in key.strip() and key.strip().islower():
            last_key = key.strip()
            header[last_key] = value.strip()
        elif last_key:
            header[last_key] = f"{header[last_key]} {comment}".strip()
    missing = [key for key in REQUIRED_KEYS if not header.get(key)]
    if missing:
        raise FigureHeaderError(f"{path.name}: header is missing {', '.join(missing)}")
    if header["carrier"] not in KNOWN_CARRIERS:
        raise FigureHeaderError(f"{path.name}: unknown carrier {header['carrier']!r}")
    if header["schema"] not in KNOWN_SCHEMAS:
        raise FigureHeaderError(f"{path.name}: unknown schema {header['schema']!r}")
    return header, "\n".join(body).strip().rstrip(";")


def load_figures(directory: Path = PRESENTATION_DIR) -> list[Figure]:
    figures = []
    for path in sorted(directory.glob("fig_*.sql")):
        header, sql = parse_header(path.read_text(encoding="utf-8"), path)
        figures.append(Figure(path=path, header=header, sql=sql))
    return figures


@dataclass
class Result:
    figure: Figure
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    error: str | None = None


def _execute(connection: Any, sql: str) -> tuple[list[str], list[list[Any]]]:
    cursor = connection.cursor()
    cursor.execute(sql)
    rows = [list(row) for row in cursor.fetchall()]
    columns = [description[0] for description in cursor.description or []]
    return columns, rows


def run(figures: list[Figure], settings: Any, location_prefix: str) -> list[Result]:
    """One connection per schema — figures are grouped so meta and gold do not
    take turns reconnecting."""
    results: list[Result] = []
    for layer in KNOWN_SCHEMAS:
        subset = [figure for figure in figures if figure.layer == layer]
        if not subset:
            continue
        connection = _connect(settings, schema_name(layer, location_prefix))
        for figure in subset:
            try:
                columns, rows = _execute(connection, figure.sql)
                results.append(Result(figure=figure, columns=columns, rows=rows))
            except Exception as exc:  # noqa: BLE001
                results.append(Result(figure=figure, error=f"{type(exc).__name__}: {exc}"))
    return sorted(results, key=lambda result: result.figure.fig_id)


CERTIFICATION_SQL = """
SELECT status, certified_at, run_id, error_count, warn_count
FROM {schema}.gold_certification
ORDER BY certified_at DESC
LIMIT 1
"""


def read_certification(settings: Any, location_prefix: str) -> dict[str, Any]:
    """C5: a frozen figure carries the certification state of the build it came
    from. `unknown` is not a synonym for `suspect` and neither is an error here
    — the figure records what was true, it does not adjudicate it."""
    try:
        connection = _connect(settings, schema_name("meta", location_prefix))
        columns, rows = _execute(
            connection, CERTIFICATION_SQL.format(schema=schema_name("meta", location_prefix))
        )
    except Exception as exc:  # noqa: BLE001
        return {"status": "unread", "error": f"{type(exc).__name__}: {exc}"}
    if not rows:
        return {"status": "unread", "error": "gold_certification is empty"}
    return {column: _jsonable(value) for column, value in zip(columns, rows[0], strict=False)}


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def render(results: list[Result]) -> str:
    lines: list[str] = ["# presentation figures", ""]
    for result in results:
        figure = result.figure
        lines.append(f"## {figure.fig_id} · {figure.carrier} · `{figure.path.name}`")
        lines.append("")
        lines.append(f"**判据** {figure.header['criterion']}")
        lines.append("")
        lines.append(f"**图注** {figure.header['caption']}")
        lines.append("")
        lines.append(f"🔴 **不得读成** {figure.header['must_not_say']}")
        lines.append("")
        if result.error:
            lines.append(f"🔴 could not run: {result.error}")
            lines.append("")
            continue
        lines.append("| " + " | ".join(result.columns) + " |")
        lines.append("|" + "|".join(["---"] * len(result.columns)) + "|")
        for row in result.rows:
            lines.append("| " + " | ".join("" if v is None else str(v) for v in row) + " |")
        lines.append("")
        lines.append(f"{len(result.rows)} rows")
        lines.append("")
    return "\n".join(lines)


def export(results: list[Result], directory: Path, certification: dict[str, Any]) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    frozen_at = datetime.now(UTC).isoformat()
    for result in results:
        if result.error:
            continue
        payload = {
            "fig_id": result.figure.fig_id,
            "source_sql": f"sql/presentation/{result.figure.path.name}",
            "frozen_at": frozen_at,
            "certification": certification,
            "header": result.figure.header,
            "columns": result.columns,
            "rows": [[_jsonable(value) for value in row] for row in result.rows],
        }
        path = directory / f"{result.figure.fig_id}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(path)
    return written


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", default=None, help="one fig id, or a comma-separated list")
    parser.add_argument("--carrier", default=None, choices=KNOWN_CARRIERS)
    parser.add_argument("--json", dest="json_dir", default=None, help="freeze results into this dir")
    parser.add_argument("--location-prefix", default="", help="smoke namespace, as in apply_ddl")
    return parser


def main(argv: list[str] | None = None) -> int:
    load_cli_env()
    args = build_parser().parse_args(argv)
    try:
        figures = load_figures()
    except FigureHeaderError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.only:
        wanted = {item.strip().upper() for item in args.only.split(",")}
        figures = [figure for figure in figures if figure.fig_id.upper() in wanted]
    if args.carrier:
        figures = [figure for figure in figures if figure.carrier == args.carrier]
    if not figures:
        print("no figures selected", file=sys.stderr)
        return 2

    try:
        settings = load_trino_settings()
    except TrinoConfigError as exc:
        print(f"{exc}\n\n{HOST_SHELL_HINT}", file=sys.stderr)
        return 3

    try:
        results = run(figures, settings, args.location_prefix)
    except Exception as exc:  # noqa: BLE001
        print(f"\n{HOST_SHELL_HINT}\n\nunderlying error: {type(exc).__name__}: {exc}")
        return 3

    print(render(results))
    if args.json_dir is not None:
        certification = read_certification(settings, args.location_prefix)
        written = export(results, Path(args.json_dir or DEFAULT_EXPORT_DIR), certification)
        print(f"\nfrozen {len(written)} figures into {args.json_dir}")
        print(f"certification at freeze time: {certification.get('status')}")

    could_not_run = [result for result in results if result.error]
    if could_not_run:
        # Not a finding — the tool failed to ask. Same distinction as ADR 0012
        # rule 2 and `certify`'s `unknown`.
        print(f"\n🔴 {len(could_not_run)} figures could not be executed", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
