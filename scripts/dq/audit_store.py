"""`uoip_meta.dq_audit_log` — creating it, appending to it, reading the trend.

🔴 Append-only. Everything else in the platform that writes Gold rebuilds the
table whole (`.claude/rules/gold-sql.md` R4: DROP → purge → CREATE → INSERT).
This table must never take that path: the previous runs *are* the product. The
only DDL statement here is `CREATE TABLE IF NOT EXISTS`, and there is no
teardown command — a disposable run gets a `--location-prefix`, which puts it
in its own schema and its own storage prefix, and `apply_ddl`-style cleanup
happens by deleting that prefix.

Why a separate module rather than a flag on apply_ddl: the audit table is not a
data product. `sql/ddl/` is globbed by the contract-consistency test and by
`ddl_parser`, whose layer regex knows only `silver|gold`, so a file dropped in
there fails two ways before it ever reaches Trino (launch §0.1). One small
entry point costs less than reshaping the table creator for one table.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.ddl.apply_ddl import (
    TrinoSettings,
    _connect,
    normalise_prefix,
    schema_name,
)

META_LAYER = "meta"
REPO_ROOT = Path(__file__).resolve().parents[2]
DDL_PATH = REPO_ROOT / "sql" / "meta" / "dq_audit_log.sql"
TABLE = "dq_audit_log"

COLUMNS = (
    "run_id",
    "cadence",
    "layer",
    "table_name",
    "rule_id",
    "dimension",
    "severity",
    "observed",
    "expected",
    "comparator",
    "passed",
    "previous_observed",
    "rows_checked",
    "rows_failed",
    "elapsed_s",
    "checked_at",
    "error_text",
)


@dataclass
class Observation:
    """One executed check, as it lands in the log."""

    run_id: str
    cadence: str
    layer: str
    table_name: str
    rule_id: str
    dimension: str
    severity: str
    observed: float | None
    expected: str
    comparator: str
    passed: bool
    previous_observed: float | None = None
    rows_checked: int | None = None
    rows_failed: int | None = None
    elapsed_s: float = 0.0
    checked_at: dt.datetime | None = None
    error_text: str | None = None

    @property
    def could_not_run(self) -> bool:
        """A check that never produced a number. The one thing that raises."""
        return self.error_text is not None


def meta_schema(location_prefix: str = "") -> str:
    return schema_name(META_LAYER, location_prefix)


def render_ddl(text: str, bucket: str, location_prefix: str = "") -> str:
    """Substitute the bucket and shift the storage path under a prefix.

    `apply_ddl.render_ddl` only shifts the `silver/` and `gold/` path segments,
    so the meta layer needs its own two lines rather than a fourth argument to
    a function whose LAYERS tuple is used elsewhere to mean "the data layers".
    """
    rendered = text.replace("{bucket}", bucket)
    prefix = normalise_prefix(location_prefix)
    if prefix:
        rendered = rendered.replace(
            f"s3a://{bucket}/{META_LAYER}/",
            f"s3a://{bucket}/{prefix}/{META_LAYER}/",
        )
    return rendered[rendered.index("CREATE TABLE") :].rstrip().rstrip(";")


def ensure_table(
    settings: TrinoSettings, bucket: str, location_prefix: str = "", ddl_text: str | None = None
) -> str:
    """Create the schema and the table if they are not there. Returns the schema."""
    schema = meta_schema(location_prefix)
    prefix = normalise_prefix(location_prefix)
    path_prefix = f"{prefix}/" if prefix else ""
    text = ddl_text if ddl_text is not None else DDL_PATH.read_text()
    with _connect(settings, schema) as connection:
        cursor = connection.cursor()
        cursor.execute(
            f"CREATE SCHEMA IF NOT EXISTS {settings.catalog}.{schema} "
            f"WITH (location = 's3a://{bucket}/{path_prefix}{META_LAYER}/')"
        )
        cursor.fetchall()
        cursor = connection.cursor()
        cursor.execute(render_ddl(text, bucket, location_prefix))
        cursor.fetchall()
    return schema


def _literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dt.datetime):
        return f"TIMESTAMP '{value.strftime('%Y-%m-%d %H:%M:%S.%f')}'"
    return "'" + str(value).replace("'", "''") + "'"


def insert_sql(schema: str, observations: list[Observation]) -> str:
    """One multi-row INSERT. Columns are listed explicitly (R5)."""
    rows = []
    for obs in observations:
        values = [
            obs.run_id,
            obs.cadence,
            obs.layer,
            obs.table_name,
            obs.rule_id,
            obs.dimension,
            obs.severity,
            obs.observed,
            obs.expected,
            obs.comparator,
            obs.passed,
            obs.previous_observed,
            obs.rows_checked,
            obs.rows_failed,
            obs.elapsed_s,
            obs.checked_at or dt.datetime.now(dt.UTC).replace(tzinfo=None),
            obs.error_text,
        ]
        rows.append("(" + ", ".join(_literal(v) for v in values) + ")")
    columns = ", ".join(COLUMNS)
    return f"INSERT INTO {schema}.{TABLE} ({columns}) VALUES " + ", ".join(rows)


def append(connection: Any, schema: str, observations: list[Observation]) -> int:
    if not observations:
        return 0
    cursor = connection.cursor()
    cursor.execute(insert_sql(schema, observations))
    cursor.fetchall()
    return len(observations)


PREVIOUS_SQL = """
SELECT rule_id, table_name, observed
FROM (
    SELECT rule_id, table_name, observed,
           ROW_NUMBER() OVER (
               PARTITION BY rule_id, table_name ORDER BY checked_at DESC
           ) AS recency
    FROM {schema}.{table}
    WHERE observed IS NOT NULL
)
WHERE recency = 1
"""


def previous_observations(connection: Any, schema: str) -> dict[tuple[str, str], float]:
    """The most recent non-null observation per (rule, table).

    Keyed by both because a `table: "*"` rule produces one trend per table, and
    collapsing them would compare `dim_channel` against `fact_recommendation`.
    Checks that could not run are excluded — a failed run must not become the
    baseline the next run is compared against.
    """
    cursor = connection.cursor()
    cursor.execute(PREVIOUS_SQL.format(schema=schema, table=TABLE))
    return {(row[0], row[1]): float(row[2]) for row in cursor.fetchall()}
