"""Build the Gold tables from Silver plus the seed CSVs.

Execution model — measured on Trino 451 / Hive connector, 2026-08-19 (O12):
a Gold table is rebuilt whole, in four steps, because three of the four
obvious ways do not exist on this connector (CREATE OR REPLACE, TRUNCATE and
DELETE are all NOT_SUPPORTED):

    1. DROP TABLE
    2. purge the table's storage prefix   <- not optional, see below
    3. CREATE TABLE from sql/ddl/<table>.sql
    4. INSERT INTO <table> (<explicit columns>) <the SELECT in sql/dml/>

Step 2 is load-bearing: these are *external* tables, so DROP leaves the
objects behind and a table recreated over them reads the previous generation
immediately. Skipping it makes a rebuild silently union two generations.
Because step 4 appends, the exact row-count gates are the only thing that
notices — which is why a failed gate fails the whole batch.

See .claude/rules/gold-sql.md R4 and
docs/dev/design/20260817-gold-dimensional-build.md §4.3/§7.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

# Reused rather than re-derived, same as dags/_alerts.py: redaction (the
# webhook URL *is* the credential), Discord's 2000-char `content` ceiling, and
# the short timeout that keeps alerting from becoming the thing that hangs.
from ingestion.snapshot.notify import CONTENT_LIMIT, TIMEOUT_SECS, _redact
from scripts._env import load_cli_env
from scripts.ddl.apply_ddl import (
    LAYERS,
    TrinoConfigError,
    _connect,
    _purge_storage,
    load_trino_settings,
    normalise_prefix,
    render_ddl,
    schema_name,
)
from scripts.ddl.ddl_parser import DDL_DIR, parse_ddl_file
from scripts.gold.gates import Gate, parse_gates

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DML_DIR = REPO_ROOT / "sql" / "dml"
SEED_DIR = REPO_ROOT / "config" / "seeds"

LINEAGE_COLUMNS = ("etl_run_id", "built_at", "source_max_ingest_date")

# How to reach Trino from a human shell. The .env value is the *container*
# view (TRINO_HOST=trino) because the Airflow container is the unattended
# consumer; a host shell has to go through the published port. Printing this
# on a connection failure is not a nicety: the real cause arrives buried under
# five layers of urllib3 traceback, which cost a round trip on 2026-08-19.
HOST_SHELL_HINT = (
    "Cannot reach Trino. From the compute node's *host* shell, Trino is on the\n"
    "published port, not the in-network one — prefix the command:\n"
    "    TRINO_HOST=localhost TRINO_PORT=8090 make gold-build\n"
    "(.env holds the container view, trino:8080, for the Airflow container.)"
)

# Same webhook the backfill plan scripts use (_plan_lib.sh) and the same
# fallback order — one Discord channel for all UOIP long-running jobs, per
# dags/_alerts.py. A build is manually triggered from a terminal someone is
# not necessarily watching (dim_service_type alone is 19 sequential chunks),
# so a run that clears this threshold gets a completion notice the same way a
# backfill window failure does. Below the threshold the terminal output is
# still right there, so a notification would just be noise.
ALERT_URL_VARS = ("BACKFILL_ALERT_WEBHOOK_URL", "SNAPSHOT_ALERT_WEBHOOK_URL")
SLOW_BUILD_THRESHOLD_SECS = 300


def _resolve_alert_url() -> str:
    for var in ALERT_URL_VARS:
        url = (os.environ.get(var) or "").strip()
        if url:
            return url
    return ""


def notify_build_outcome(text: str, elapsed_secs: float) -> bool:
    """POST a Discord notice about a build that took long enough to walk away from.

    Mirrors dags/_alerts.py's failure notice — same payload shape, same
    never-raises contract — but covers what that module does not: this script
    is run by hand from a compute-node shell, where "did it finish, and did it
    pass" is otherwise answered only by staring at a terminal.

    🔴 Every outcome notifies, not just success. The first 18-minute run of
    `--only dims` ended on a failed gate and sent nothing, which is precisely
    the case the notification exists for — a run long enough to walk away from
    is a run whose *failure* you most need pushed to you. A crash notifies for
    the same reason: `dim_service_type` is 19 sequential chunks, and dying at
    chunk 18 is a ten-minute silence otherwise.

    The elapsed-time threshold is the only filter: below it the terminal
    output is still on screen, so a notification would be noise.

    Returns True if delivered; never raises, since a failing alert channel
    must not turn into a failing build.
    """
    url = _resolve_alert_url()
    if not url:
        logger.warning(
            "Build took %.0fs (>= %ds) and none of %s is set — no notice was sent: %s",
            elapsed_secs, SLOW_BUILD_THRESHOLD_SECS, "/".join(ALERT_URL_VARS), text,
        )
        return False
    payload = {"content": text[:CONTENT_LIMIT], "allowed_mentions": {"parse": []}}
    try:
        response = requests.post(url, json=payload, timeout=TIMEOUT_SECS)
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error("Could not deliver build notice: %s", _redact(e))
        return False
    return True


@dataclass(frozen=True)
class Table:
    """One Gold table and what it needs built before it."""

    name: str
    stage: str  # "seeds" | "dims" | "facts"
    deps: tuple[str, ...] = ()
    seed: str | None = None  # basename in config/seeds/, for seed-loaded tables
    # Gates that cannot be expressed as COUNT(*) in the DDL header. Each entry
    # is (description, SQL returning one number, expected value).
    extra_gates: tuple[tuple[str, str, int], ...] = field(default=())


# Order is hardcoded rather than derived from the filenames: `dim_service_type`
# sorting after `dim_winter_category` is a coincidence, and `dim_admin_label`
# does not sort where it must run. Dependency graph: design §5.
TABLES: tuple[Table, ...] = (
    Table("dim_winter_category", "seeds", seed="winter_category.csv"),
    Table("dim_channel", "seeds", seed="channel.csv"),
    Table(
        "dim_recommendation_rules",
        "seeds",
        seed="recommendation_rules.csv",
        extra_gates=(
            (
                "at least one fallback rule exists (design §6.9)",
                # LEAST(...,1) turns "at least one" into something an equality
                # gate can state. A rule set with no fallback degrades to empty
                # attribution text in L3 rather than saying it degraded.
                "SELECT LEAST(COUNT(*), 1) FROM dim_recommendation_rules WHERE is_fallback = true",
                1,
            ),
        ),
    ),
    Table(
        "dim_service_type",
        "dims",
        deps=("dim_winter_category",),
        extra_gates=(
            (
                "every observed silver_service_request.type resolves to a row "
                "(build-time LEFT ANTI JOIN, design §6.2 decision 1)",
                # NB: this is about *coverage of type values*, not about
                # winter_category being non-null. A non-winter type with a NULL
                # winter_category is correct; conflating the two makes the
                # first build fail forever.
                "SELECT COUNT(*) FROM ("
                ' SELECT DISTINCT s."type" FROM {silver}.silver_service_request s'
                ' LEFT JOIN dim_service_type d ON d."type" = s."type"'
                ' WHERE d."type" IS NULL)',
                0,
            ),
        ),
    ),
    Table("dim_plow_zone", "dims"),
    Table("dim_admin_label", "dims"),
    Table("dim_snowfall_event", "dims"),
    Table("dim_plow_event", "dims", deps=("dim_snowfall_event",)),
    Table(
        "dim_region_crosswalk",
        "dims",
        deps=("dim_plow_zone", "dim_admin_label", "dim_service_type"),
        extra_gates=(
            (
                "weights sum to 1.0 within every (plow_zone, label_type) group",
                "SELECT COUNT(*) FROM ("
                " SELECT plow_zone, label_type, SUM(weight) AS w FROM dim_region_crosswalk"
                " GROUP BY plow_zone, label_type) WHERE ABS(w - 1.0) > 1e-9",
                0,
            ),
            (
                "exactly one is_dominant row per (plow_zone, label_type) group",
                "SELECT COUNT(*) FROM ("
                " SELECT plow_zone, label_type, COUNT_IF(is_dominant) AS n"
                " FROM dim_region_crosswalk GROUP BY plow_zone, label_type) WHERE n <> 1",
                0,
            ),
        ),
    ),
    Table("fact_plow_shift", "facts", deps=("dim_plow_event", "dim_plow_zone")),
    Table("fact_parking_ban", "facts", deps=("dim_plow_event",)),
    Table("fact_event_zone_rank", "facts", deps=("dim_plow_event", "dim_plow_zone")),
    Table(
        "fact_service_request_zone_event",
        "facts",
        deps=("dim_snowfall_event", "dim_plow_zone", "dim_winter_category", "dim_service_type"),
    ),
    # Chunked: the only Gold table whose grain contains a date, and the only
    # one whose SELECT spans the whole Silver history. R1/R2 of gold-sql.md.
    Table(
        "fact_winter_request_daily_by_label",
        "facts",
        deps=("dim_admin_label", "dim_service_type"),
        extra_gates=(
            (
                # 141,377 measured 2026-08-19 by counting the real
                # (date, label) pairs. The design's ≈1.6 M assumed a dense
                # 6,600-day x 252-label panel; only ~80% of winter rows carry
                # a label at all, and the panel is nowhere near dense.
                "row count matches the measured (date, label) pair count (O14)",
                "SELECT COUNT(*) FROM fact_winter_request_daily_by_label",
                141_377,
            ),
            (
                # A chunked build that dies halfway leaves a smaller, entirely
                # plausible-looking table. The chunk count is the only thing
                # that says otherwise.
                "every chunk contributed: all 19 calendar years present",
                'SELECT COUNT(DISTINCT YEAR("date")) FROM fact_winter_request_daily_by_label',
                19,
            ),
        ),
    ),
)

CHUNKED: dict[str, tuple[int, int]] = {
    # table -> [first year, last year] inclusive, chunked by calendar year.
    "fact_winter_request_daily_by_label": (2008, 2026),
    # Same shape as dim_admin_label below: its grain has no date, but it has to
    # enumerate every `type` value ever observed, which is the same
    # 4,878-partition scan. Overlapping chunks, PK held by the anti-join.
    "dim_service_type": (2008, 2026),
    # Not date-grained, but it has to enumerate label values across the whole
    # history, which is the same 4,878-partition scan. Its chunks overlap, so
    # the SQL anti-joins against the rows earlier chunks already inserted.
    "dim_admin_label": (2008, 2026),
}

BY_NAME = {t.name: t for t in TABLES}


def check_order() -> None:
    """Every dependency must be built before the table that needs it."""
    seen: set[str] = set()
    for table in TABLES:
        missing = [d for d in table.deps if d not in seen]
        if missing:
            raise ValueError(
                f"{table.name} depends on {missing}, which TABLES lists later "
                f"(or not at all). Fix the order in TABLES — it is the build order."
            )
        seen.add(table.name)


def sql_literal(value: str, sql_type: str) -> str:
    """Render one CSV cell as a Trino literal of the DDL's declared type."""
    text = value.strip()
    upper = sql_type.upper()
    if text == "":
        return "NULL"
    if upper.startswith(("VARCHAR", "CHAR")):
        # Doubling the quote is what keeps a neighbourhood called O'Connor from
        # ending the string early.
        return "'" + text.replace("'", "''") + "'"
    if upper.startswith("BOOLEAN"):
        if text.lower() not in {"true", "false"}:
            raise ValueError(f"{text!r} is not a boolean")
        return text.lower()
    if upper.startswith(("INTEGER", "BIGINT", "DOUBLE", "REAL", "DECIMAL")):
        float(text)  # raises if it is not numeric
        return text
    if upper.startswith("DATE"):
        return f"DATE '{text}'"
    if upper.startswith("TIMESTAMP"):
        return f"TIMESTAMP '{text}'"
    raise ValueError(f"no literal rendering for SQL type {sql_type!r}")


def _seed_rows(name: str) -> list[dict[str, str]]:
    with (SEED_DIR / name).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def seed_placeholders() -> dict[str, str]:
    """Seed facts a DML file needs but cannot read from a Gold table.

    Both are rendered into *string literals* in the SQL, never as a bare
    placeholder in a clause: sqlfluff cannot parse the latter, and an
    unparseable file silently loses the SELECT * and partition-predicate
    checks too (launch doc §7.3).

    `winter_category_order` exists because dim_winter_category's schema is
    frozen with no priority column, and the CSV's row order *is* the multi-hit
    arbitration rule. Row order on a Parquet read is not a guarantee, so the
    order has to travel explicitly rather than be recovered from the table.

    `service_type_keywords` has no Gold table at all — the 17 are frozen — so
    the 9 priority patterns travel as `priority;regex;weight` joined by `|`.
    Neither character occurs in any pattern; the check below keeps it that way.
    """
    order = [r["winter_category"] for r in _seed_rows("winter_category.csv")]
    rows = _seed_rows("service_type_keywords.csv")
    fields = [
        (r["match_priority"].strip(), r["pattern_regex"].strip(), r["priority_weight"].strip())
        for r in rows
    ]
    for parts in (*fields, *((c,) for c in order)):
        for cell in parts:
            if ";" in cell or "|" in cell or "'" in cell or "," in cell:
                raise ValueError(
                    f"{cell!r} contains a delimiter used to encode a seed placeholder "
                    f"(one of ; | , ') — change the encoding, not the seed."
                )
    return {
        "winter_category_order": ",".join(order),
        "service_type_keywords": "|".join(";".join(f) for f in fields),
    }


def seed_select(table: Table, columns: list[Any], run_id: str, built_at: str) -> str:
    """Build `SELECT ... FROM (VALUES ...)` from a seed CSV.

    The CSV's column order must match the DDL's, minus the three lineage
    columns which the build injects. Mismatches raise rather than being
    matched up by name: a tolerant loader turns one misplaced column into a
    silent semantic swap.
    """
    path = SEED_DIR / table.seed
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        raise ValueError(f"{path} is empty")
    header, body = rows[0], rows[1:]
    expected = [c.name for c in columns if c.name not in LINEAGE_COLUMNS]
    if [h.strip() for h in header] != expected:
        raise ValueError(
            f"{path.name} columns {header} do not match {table.name}'s DDL "
            f"columns {expected} (order matters — see build_gold.seed_select)"
        )
    types = {c.name: c.sql_type for c in columns}
    # A seed's upstream is this file, so its lineage date is the file's own
    # last modification — not the build date. source_max_ingest_date answers
    # "how old is the input", and CURRENT_DATE would answer "when did we run",
    # which is what built_at is already for (ADR 0010 D7).
    seed_date = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.UTC).date().isoformat()
    tuples = []
    for line_no, row in enumerate(body, start=2):
        if len(row) != len(expected):
            raise ValueError(
                f"{path.name}:{line_no} has {len(row)} cells, expected {len(expected)}"
            )
        cells = [sql_literal(cell, types[name]) for cell, name in zip(row, expected, strict=True)]
        cells += [f"'{run_id}'", f"TIMESTAMP '{built_at}'", f"DATE '{seed_date}'"]
        tuples.append("(" + ", ".join(cells) + ")")
    all_cols = expected + list(LINEAGE_COLUMNS)
    return (
        "SELECT * FROM (VALUES\n  " + ",\n  ".join(tuples) + "\n) AS t(" + ", ".join(all_cols) + ")"
    )


# Column names that collide with SQL keywords. Both are real: silver/gold DDL
# declares "type" and "date" quoted, and an unquoted reference parses as the
# keyword and fails at build time, not at review time.
_RESERVED = {"type", "date"}


def _quote(name: str) -> str:
    return f'"{name}"' if name in _RESERVED else name


class Builder:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        # Deliberately lazy: --dry-run must work on a machine that cannot
        # reach Trino at all, which is where the SQL gets reviewed.
        self.settings = None if args.dry_run else load_trino_settings()
        self.bucket = args.bucket
        self.prefix = args.location_prefix
        self.gold_schema = schema_name("gold", self.prefix)
        self.silver_schema = schema_name("silver", self.prefix)
        self.run_id = f"l2-{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%SZ}"
        # One timestamp for the whole batch: 13 tables built in one run must
        # carry one built_at, so `now()` in SQL is not an option.
        self.built_at = f"{dt.datetime.now(dt.UTC):%Y-%m-%d %H:%M:%S.%f}"
        self.conn: Any = None

    def connect(self) -> None:
        if self.args.dry_run:
            return
        try:
            self.conn = _connect(self.settings, self.gold_schema)
            self.run("SELECT 1")
        except Exception as exc:  # noqa: BLE001
            print(f"\n{HOST_SHELL_HINT}\n\nunderlying error: {type(exc).__name__}: {exc}")
            raise SystemExit(2) from exc

    def run(self, sql: str) -> list[tuple]:
        cur = self.conn.cursor()
        cur.execute(sql)
        return cur.fetchall()

    def scalar(self, sql: str) -> int:
        return self.run(sql)[0][0]

    # ------------------------------------------------------------------ steps
    def rebuild(self, table: Table) -> None:
        ddl_path = DDL_DIR / f"{table.name}.sql"
        ddl = parse_ddl_file(ddl_path)
        create_sql = render_ddl(ddl_path.read_text(), self.bucket, self.prefix)
        columns = ddl.data_columns
        col_list = ", ".join(_quote(c.name) for c in columns)

        if table.seed:
            selects = [seed_select(table, columns, self.run_id, self.built_at)]
        else:
            selects = self.dml_selects(table)

        if self.args.dry_run:
            print(f"\n===== {table.name} (dry run) =====")
            print(f"-- 1. DROP TABLE IF EXISTS {table.name}")
            print(f"-- 2. purge s3a://{self.bucket}/{self._table_prefix(table)}")
            print(f"-- 3. {create_sql.strip().splitlines()[0]} ...")
            for i, select in enumerate(selects, 1):
                label = f" (chunk {i}/{len(selects)})" if len(selects) > 1 else ""
                print(f"-- 4{label}\nINSERT INTO {table.name} ({col_list})\n{select}\n")
            return

        self.run(f"DROP TABLE IF EXISTS {table.name}")
        purged = _purge_storage(self.bucket, self._table_prefix(table))
        self.run(create_sql)
        stale = self.scalar(f"SELECT COUNT(*) FROM {table.name}")
        if stale:
            raise SystemExit(
                f"{table.name}: {stale} rows visible on a freshly created table — "
                f"the storage purge did not take (purged {purged} objects). "
                f"Building on top of this would union two generations."
            )
        for i, select in enumerate(selects, 1):
            if len(selects) > 1:
                print(f"    chunk {i}/{len(selects)}", flush=True)
            self.run(f"INSERT INTO {table.name} ({col_list})\n{select}")

    @staticmethod
    def _seed_row_gate(table: Table) -> list[Gate]:
        """A seed table must end up with exactly as many rows as its CSV has.

        Derived from the file rather than written down twice: the count is not
        an independent fact about the table, it is a fact about the seed.
        """
        if not table.seed:
            return []
        with (SEED_DIR / table.seed).open(newline="", encoding="utf-8") as handle:
            rows = sum(1 for _ in csv.reader(handle)) - 1
        return [
            Gate(
                source_text=f"COUNT(*) = {rows} (rows in {table.seed})",
                predicate=None,
                expected=rows,
            )
        ]

    def _table_prefix(self, table: Table) -> str:
        parts = [p for p in (self.prefix, "gold", table.name) if p]
        return "/".join(parts)

    def dml_selects(self, table: Table) -> list[str]:
        """Read sql/dml/<table>.sql and expand it into one SELECT per chunk.

        The file holds a bare SELECT, not a full statement: the INSERT and its
        explicit column list are composed from the DDL so that the columns have
        exactly one source of truth, and so a chunk predicate can be threaded
        in without rewriting the file.
        """
        path = DML_DIR / f"{table.name}.sql"
        if not path.exists():
            raise SystemExit(f"{path} does not exist — nothing to build {table.name} from.")
        # Not render_ddl(): that one ends by slicing from "CREATE TABLE", which
        # a DML file has none of. Only the location substitution is shared.
        text = path.read_text().replace("{bucket}", self.bucket)
        prefix = normalise_prefix(self.prefix)
        if prefix:
            for layer in LAYERS:
                text = text.replace(
                    f"s3a://{self.bucket}/{layer}/", f"s3a://{self.bucket}/{prefix}/{layer}/"
                )
        # Double braces, not single: {silver} sits outside any string literal
        # (a FROM/JOIN schema qualifier), and single-brace text there is not
        # valid SQL — sqlfluff parses it as unparsable rather than as a
        # placeholder. {{ silver }} is real jinja, resolved for lint purposes
        # by the [sqlfluff:templater:jinja:context] entry in .sqlfluff; this
        # is the matching runtime substitution. See .sqlfluff for the rest of
        # the reasoning.
        text = text.replace("{{ silver }}", self.silver_schema)
        for name, value in seed_placeholders().items():
            text = text.replace("{" + name + "}", value)
        text = text.replace("{etl_run_id}", self.run_id).replace("{built_at}", self.built_at)
        text = text.strip().rstrip(";")
        if table.name not in CHUNKED:
            return [text]
        # The chunk boundaries are substituted *inside* the date literals the
        # file already writes, rather than replacing a whole predicate. That
        # keeps every DML file parseable SQL — sqlfluff cannot parse a bare
        # {placeholder} in a WHERE clause, and an unlintable file is one nobody
        # checks for SELECT * or a missing partition predicate either.
        first, last = CHUNKED[table.name]
        return [
            text.replace("{chunk_start}", f"{y}-01-01").replace("{chunk_end}", f"{y + 1}-01-01")
            for y in range(first, last + 1)
        ]

    def check_gates(self, table: Table) -> list[str]:
        failures = []
        ddl_text = (DDL_DIR / f"{table.name}.sql").read_text()
        gates, notes = parse_gates(ddl_text)
        gates = list(gates) + self._seed_row_gate(table)
        for gate in gates:
            actual = self.scalar(gate.sql(table.name))
            status = "ok " if actual == gate.expected else "FAIL"
            print(f"    [{status}] {gate.source_text}  -> {actual:,}")
            if actual != gate.expected:
                failures.append(f"{table.name}: {gate.source_text} but got {actual:,}")
        for description, sql, expected in table.extra_gates:
            actual = self.scalar(sql.format(silver=self.silver_schema))
            status = "ok " if actual == expected else "FAIL"
            print(f"    [{status}] {description} -> {actual:,} (expected {expected:,})")
            if actual != expected:
                failures.append(f"{table.name}: {description} -> {actual:,}, expected {expected:,}")
        for note in notes:
            print(f"    [note] not machine-checked: {note}")
        return failures

    # ------------------------------------------------------------------- main
    def build(self) -> int:
        check_order()
        selected = [t for t in TABLES if self.args.only in (None, "all", t.stage, t.name)]
        if not selected:
            print(f"--only {self.args.only!r} matched no table or stage.")
            return 2

        print(f"run_id={self.run_id}  built_at={self.built_at}")
        print(f"schema={self.gold_schema}  bucket={self.bucket}  tables={len(selected)}")
        self.connect()
        batch_started = dt.datetime.now(dt.UTC)

        failures: list[str] = []
        try:
            for table in selected:
                print(f"\n--- {table.name} ({table.stage}) ---", flush=True)
                started = dt.datetime.now(dt.UTC)
                self.rebuild(table)
                if self.args.dry_run:
                    continue
                rows = self.scalar(f"SELECT COUNT(*) FROM {table.name}")
                elapsed = (dt.datetime.now(dt.UTC) - started).total_seconds()
                print(f"    {rows:,} rows in {elapsed:.1f}s")
                failures += self.check_gates(table)
        except BaseException as exc:
            # BaseException, not Exception: a long build is also abandoned by
            # Ctrl-C and SIGTERM (an SSH drop), and those are exactly the runs
            # nobody is watching. The notice is best-effort and the original
            # exception always propagates untouched.
            self._notify_outcome(batch_started, f"crashed: {type(exc).__name__}: {exc}")
            raise

        if failures:
            print("\n\n#### GATES FAILED ####")
            for line in failures:
                print(f"  {line}")
            print(
                "\nThe built tables are NOT rolled back. A rebuild is idempotent, "
                "so fix the cause and re-run; do not patch a table by hand."
            )
            self._notify_outcome(batch_started, f"FAILED {len(failures)} gate(s): " + "; ".join(failures))
            return 1
        if not self.args.dry_run:
            print("\nall gates green")
            self._notify_outcome(batch_started, "succeeded, all gates green")
        return 0

    def _notify_outcome(self, batch_started: dt.datetime, outcome: str) -> None:
        """Push the run's outcome to Discord if it ran long enough to walk away from."""
        if self.args.dry_run:
            return
        elapsed = (dt.datetime.now(dt.UTC) - batch_started).total_seconds()
        if elapsed < SLOW_BUILD_THRESHOLD_SECS:
            return
        only = self.args.only or "all"
        notify_build_outcome(
            f"[gold-build] run {self.run_id} (--only {only}) {outcome} — {elapsed:.0f}s",
            elapsed,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", default=None)
    parser.add_argument(
        "--only",
        default=None,
        help="a stage (seeds|dims|facts|all) or a single table name",
    )
    parser.add_argument("--dry-run", action="store_true", help="print the SQL, execute nothing")
    parser.add_argument("--location-prefix", default="", help="smoke namespace, as in apply_ddl")
    return parser


def main(argv: list[str] | None = None) -> int:
    load_cli_env()
    args = build_parser().parse_args(argv)
    if not args.bucket:
        import os

        args.bucket = os.environ.get("S3_BUCKET_NAME")
    if not args.bucket:
        print("Missing bucket: pass --bucket or set S3_BUCKET_NAME.", file=sys.stderr)
        return 2
    try:
        return Builder(args).build()
    except TrinoConfigError as exc:
        print(f"{exc}\n\n{HOST_SHELL_HINT}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
