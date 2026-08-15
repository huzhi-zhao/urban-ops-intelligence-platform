"""Create, smoke-test and tear down the 25 Silver/Gold tables in Trino.

This is the S4 acceptance criterion ("an empty table can actually be created")
made executable. It is deliberately *not* an ETL entry point: it proves the
Trino + Hive Metastore + MinIO path works end to end and then removes every
trace of itself.

    python -m scripts.ddl.apply_ddl create   --location-prefix smoke-20260814
    python -m scripts.ddl.apply_ddl smoke    --location-prefix smoke-20260814
    python -m scripts.ddl.apply_ddl teardown --location-prefix smoke-20260814

Why ``--location-prefix`` exists and why the smoke run must use one
--------------------------------------------------------------------
``silver/weather_archive/`` and ``silver/plow_zone_boundary/`` already hold
real data (the 2026-08-12 runs). A teardown that deleted the paths named in
the DDL verbatim would delete that data. So the prefix shifts both the schema
names and the storage paths into a disposable namespace:

    s3a://uoip/silver/weather_archive/            <- real, never touched
    s3a://uoip/smoke-20260814/silver/weather_archive/  <- created and deleted

``teardown`` **refuses to run without a prefix** for that reason. Dropping the
production tables is a deliberate act that should not share a command with
"clean up after the rehearsal".

Trino connection settings come from the environment (see ``.env.example``);
the catalog and schema are injected here rather than written into the DDL
files, so the files stay deployment-independent.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from typing import Any

from scripts._env import load_cli_env
from scripts.ddl.ddl_parser import Ddl, DdlColumn, load_all_ddl

DEFAULT_CATALOG = "hive"
SCHEMA_PREFIX = "uoip"
LAYERS = ("silver", "gold")

# Two rows: one all-populated, one with every nullable column left NULL. A
# single row would pass even if the connector rejected NULLs in Parquet, which
# is the failure mode worth catching before an ETL run discovers it.
SMOKE_ROWS = 2

TEARDOWN_WITHOUT_PREFIX = (
    "teardown requires --location-prefix. Without one it would drop the "
    "production tables and delete silver/ and gold/ from the bucket — including "
    "the weather-archive and plow-zone-boundary data already written."
)


class TrinoConfigError(OSError):
    """Raised when required Trino environment variables are missing."""


@dataclass(frozen=True)
class TrinoSettings:
    host: str
    port: int
    user: str
    catalog: str

    def __str__(self) -> str:
        return f"trino://{self.user}@{self.host}:{self.port}/{self.catalog}"


def load_trino_settings(env: dict[str, str] | None = None) -> TrinoSettings:
    """Read Trino connection settings from the environment.

    Trino here is shared platform infrastructure rather than a container this
    repo starts, so its address is configuration, not a compose service name.
    See ADR 0006 §9.
    """
    source = os.environ if env is None else env

    host = (source.get("TRINO_HOST") or "").strip()
    if not host:
        raise TrinoConfigError(
            "Missing required Trino environment variable: TRINO_HOST. See .env.example.",
        )

    raw_port = (source.get("TRINO_PORT") or "8080").strip()
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise TrinoConfigError(f"TRINO_PORT must be an integer, got {raw_port!r}.") from exc

    return TrinoSettings(
        host=host,
        port=port,
        user=(source.get("TRINO_USER") or "").strip() or "uoip",
        catalog=(source.get("TRINO_CATALOG") or "").strip() or DEFAULT_CATALOG,
    )


def normalise_prefix(location_prefix: str) -> str:
    """Reduce a prefix to bare path segments, or "" if it carries nothing.

    Whitespace is stripped on both sides of the slashes, not just outside
    them: `" / "` looks non-empty to a bare `.strip("/")` and would otherwise
    satisfy the teardown guard while naming no real path at all.
    """
    return "/".join(part for part in location_prefix.strip().split("/") if part.strip())


def schema_name(layer: str, location_prefix: str = "") -> str:
    """``uoip_silver``, or ``uoip_silver_smoke_20260814`` under a prefix.

    The prefix travels into the schema name as well as the storage path so a
    rehearsal cannot collide with the real tables in the Metastore either.
    """
    prefix = normalise_prefix(location_prefix)
    base = f"{SCHEMA_PREFIX}_{layer}"
    if not prefix:
        return base
    return f"{base}_{prefix.replace('-', '_').replace('/', '_')}"


def storage_location(bucket: str, layer: str, table: str, location_prefix: str = "") -> str:
    normalised = normalise_prefix(location_prefix)
    prefix = f"{normalised}/" if normalised else ""
    return f"s3a://{bucket}/{prefix}{layer}/{table}/"


def render_ddl(text: str, bucket: str, location_prefix: str = "") -> str:
    """Substitute ``{bucket}`` and shift the location under the prefix.

    The DDL files carry a ``{bucket}`` placeholder and no catalog/schema
    qualification: which deployment they are applied to is not a property of
    the schema.
    """
    rendered = text.replace("{bucket}", bucket)
    prefix = normalise_prefix(location_prefix)
    if prefix:
        for layer in LAYERS:
            rendered = rendered.replace(
                f"s3a://{bucket}/{layer}/",
                f"s3a://{bucket}/{prefix}/{layer}/",
            )
    # Everything before CREATE TABLE is the generated comment header; Trino
    # accepts it, but stripping it keeps error messages pointing at real SQL.
    return rendered[rendered.index("CREATE TABLE") :].rstrip().rstrip(";")


def literal_for(column: DdlColumn, row: int) -> str:
    """A typed SQL literal for smoke data.

    Row 0 populates everything. Row 1 leaves every nullable column NULL, which
    is what exercises the Parquet null encoding. Partition columns are always
    populated: a NULL partition key becomes Hive's ``__HIVE_DEFAULT_PARTITION__``
    and would make the readback assertion lie about what was written.
    """
    if row > 0 and not column.not_null:
        return "NULL"

    base = column.sql_type.split("(")[0]
    if base == "VARCHAR":
        return f"'smoke_{row}'"
    if base == "INTEGER":
        return str(row + 1)
    if base == "DOUBLE":
        return f"{row + 1}.0"
    if base == "BOOLEAN":
        return "TRUE" if row == 0 else "FALSE"
    if base == "DATE":
        return f"DATE '2026-01-0{row + 1}'"
    if base == "TIMESTAMP":
        return f"TIMESTAMP '2026-01-0{row + 1} 00:00:00.000000'"
    raise ValueError(f"No smoke literal defined for Trino type {column.sql_type!r}.")


def insert_statement(ddl: Ddl, table: str) -> str:
    """One INSERT carrying both smoke rows.

    Columns are listed explicitly and in DDL order — partition keys last, which
    is the order the Hive connector requires and the reason three DDL files
    reorder their columns relative to the contract.
    """
    columns = ", ".join(f'"{c.name}"' for c in ddl.columns)
    rows = ", ".join(
        "(" + ", ".join(literal_for(c, row) for c in ddl.columns) + ")"
        for row in range(SMOKE_ROWS)
    )
    return f"INSERT INTO {table} ({columns}) VALUES {rows}"


def _connect(settings: TrinoSettings, schema: str) -> Any:
    try:
        import trino
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise SystemExit(
            "The `trino` client is not installed. Run `make install`.",
        ) from exc

    return trino.dbapi.connect(
        host=settings.host,
        port=settings.port,
        user=settings.user,
        catalog=settings.catalog,
        schema=schema,
    )


def _run(connection: Any, sql: str) -> list[tuple]:
    cursor = connection.cursor()
    cursor.execute(sql)
    return cursor.fetchall()


def tables_by_layer() -> dict[str, list[tuple[str, Ddl]]]:
    grouped: dict[str, list[tuple[str, Ddl]]] = {layer: [] for layer in LAYERS}
    for table, ddl in load_all_ddl().items():
        grouped[ddl.layer].append((table, ddl))
    return grouped


def cmd_create(settings: TrinoSettings, bucket: str, location_prefix: str) -> int:
    from scripts.ddl.ddl_parser import DDL_DIR

    grouped = tables_by_layer()
    failures = 0

    for layer in LAYERS:
        schema = schema_name(layer, location_prefix)
        normalised = normalise_prefix(location_prefix)
        prefix = f"{normalised}/" if normalised else ""
        # The schema location matters: without it the Metastore falls back to
        # its own warehouse dir, and a table created without an explicit
        # external_location would land somewhere nobody is looking.
        with _connect(settings, schema) as connection:
            _run(
                connection,
                f"CREATE SCHEMA IF NOT EXISTS {settings.catalog}.{schema} "
                f"WITH (location = 's3a://{bucket}/{prefix}{layer}/')",
            )
            print(f"schema {settings.catalog}.{schema} ready")

            for table, _ddl in grouped[layer]:
                sql = render_ddl(
                    (DDL_DIR / f"{table}.sql").read_text(), bucket, location_prefix
                )
                try:
                    _run(connection, sql)
                    print(f"  created {schema}.{table}")
                except Exception as exc:  # noqa: BLE001 - report all, fail at the end
                    failures += 1
                    print(f"  FAILED  {schema}.{table}: {exc}", file=sys.stderr)

    return 2 if failures else 0


def cmd_smoke(settings: TrinoSettings, bucket: str, location_prefix: str) -> int:
    grouped = tables_by_layer()
    failures = 0

    for layer in LAYERS:
        schema = schema_name(layer, location_prefix)
        with _connect(settings, schema) as connection:
            for table, ddl in grouped[layer]:
                try:
                    _run(connection, insert_statement(ddl, table))
                    # Read back through Trino rather than trusting the insert:
                    # the failure this catches is a write that lands in Parquet
                    # but is unreadable through the Metastore's column list.
                    count = _run(connection, f"SELECT COUNT(*) FROM {table}")[0][0]
                    non_null = _run(
                        connection,
                        f'SELECT COUNT("{ddl.columns[0].name}") FROM {table}',
                    )[0][0]
                    if count != SMOKE_ROWS:
                        raise AssertionError(f"read back {count} rows, expected {SMOKE_ROWS}")
                    print(f"  ok {schema}.{table}: {count} rows, first col non-null={non_null}")
                except Exception as exc:  # noqa: BLE001 - report all, fail at the end
                    failures += 1
                    print(f"  FAILED {schema}.{table}: {exc}", file=sys.stderr)

    return 2 if failures else 0


def _purge_storage(bucket: str, location_prefix: str) -> int:
    """Delete the prefix's objects from MinIO.

    Dropping an *external* table leaves its files behind — that is what
    "external" means. Without this step a second create/smoke cycle would read
    the previous run's rows and the count assertion would be meaningless.
    """
    from ingestion.loaders.s3_client import build_s3_client

    # Belt and braces: the prefix is required and validated by the caller, but
    # an empty one here would enumerate the whole bucket, bronze included.
    normalised = normalise_prefix(location_prefix)
    if not normalised:
        raise ValueError("refusing to purge without a location prefix")
    prefix = f"{normalised}/"

    client = build_s3_client()
    paginator = client.get_paginator("list_objects_v2")
    deleted = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
        if keys:
            client.delete_objects(Bucket=bucket, Delete={"Objects": keys})
            deleted += len(keys)
    return deleted


def cmd_teardown(settings: TrinoSettings, bucket: str, location_prefix: str) -> int:
    if not normalise_prefix(location_prefix):
        print(TEARDOWN_WITHOUT_PREFIX, file=sys.stderr)
        return 2

    grouped = tables_by_layer()
    failures = 0

    for layer in LAYERS:
        schema = schema_name(layer, location_prefix)
        with _connect(settings, schema) as connection:
            for table, _ddl in grouped[layer]:
                try:
                    _run(connection, f"DROP TABLE IF EXISTS {table}")
                    print(f"  dropped {schema}.{table}")
                except Exception as exc:  # noqa: BLE001 - report all, fail at the end
                    failures += 1
                    print(f"  FAILED  {schema}.{table}: {exc}", file=sys.stderr)
            try:
                _run(connection, f"DROP SCHEMA IF EXISTS {settings.catalog}.{schema}")
                print(f"dropped schema {settings.catalog}.{schema}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"FAILED  schema {schema}: {exc}", file=sys.stderr)

    deleted = _purge_storage(bucket, location_prefix)
    print(f"purged {deleted} object(s) under s3a://{bucket}/{normalise_prefix(location_prefix)}/")

    return 2 if failures else 0


COMMANDS = {"create": cmd_create, "smoke": cmd_smoke, "teardown": cmd_teardown}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.ddl.apply_ddl",
        description="Create / smoke-test / tear down the Silver and Gold tables in Trino.",
    )
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument(
        "--bucket",
        default=None,
        help="Object-storage bucket. Defaults to the S3_BUCKET_NAME env var.",
    )
    parser.add_argument(
        "--location-prefix",
        default="",
        help=(
            "Isolate this run under a disposable path and schema suffix, e.g. "
            "smoke-20260814. Required for teardown."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    load_cli_env()
    args = build_parser().parse_args(argv)

    # The teardown guard runs before anything else can fail. Its message names
    # the actual mistake; reaching it only after a missing TRINO_HOST would
    # report the wrong problem for the run that most needs the right one.
    if args.command == "teardown" and not normalise_prefix(args.location_prefix):
        print(TEARDOWN_WITHOUT_PREFIX, file=sys.stderr)
        return 2

    bucket = args.bucket or (os.environ.get("S3_BUCKET_NAME") or "").strip()
    if not bucket:
        print("No bucket: pass --bucket or set S3_BUCKET_NAME.", file=sys.stderr)
        return 2

    # A missing variable is a configuration mistake, not a crash: exit 2 like
    # the backfill CLI does, with the message and no traceback.
    try:
        settings = load_trino_settings()
    except TrinoConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"{args.command}: {settings} bucket={bucket} prefix={args.location_prefix or '(none)'}")
    return COMMANDS[args.command](settings, bucket, args.location_prefix)


if __name__ == "__main__":
    raise SystemExit(main())
