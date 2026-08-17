"""Register newly written Hive partition directories with the Metastore.

Spark writes Silver partitions straight to object storage via s3a and never
talks to Hive Metastore, so Trino (and Superset behind it) reports 0 rows for
a freshly-written table until something calls ``sync_partition_metadata``.
Discovered during L1's single-season gate: the Spark job exited 0, Parquet
was on disk, and `SELECT COUNT(*)` stayed 0 until this was run by hand — see
docs/dev/launch/20260817-silver-etl-runnable-launch.md §3.1/§5.

This is the CLI-side counterpart of dags/_trino_common.py's
``sync_partition_metadata``: the DAGs call that after every Spark task, and
``scripts/backfill/plan_silver_service_request.sh`` — which runs Spark
outside Airflow entirely — calls this after every backfill window, via its
``WINDOW_RUNNER`` hook. Both must exist; fixing only the DAG path leaves the
CLI-driven full-history backfill (19 windows, run once) with the same gap.

Usage:
    python -m scripts.ddl.sync_partitions --schema uoip_silver --table silver_service_request
"""

from __future__ import annotations

import argparse
import sys

from scripts._env import load_cli_env
from scripts.ddl.apply_ddl import TrinoConfigError, load_trino_settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--table", required=True)
    args = parser.parse_args()

    load_cli_env()

    try:
        settings = load_trino_settings()
    except TrinoConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 64

    import trino

    conn = trino.dbapi.connect(
        host=settings.host,
        port=settings.port,
        user=settings.user,
        catalog=settings.catalog,
        schema=args.schema,
    )
    cursor = conn.cursor()
    cursor.execute(
        f"CALL {settings.catalog}.system.sync_partition_metadata("
        f"schema_name => '{args.schema}', table_name => '{args.table}', mode => 'FULL')"
    )
    cursor.fetchall()
    print(f"synced partitions: {settings.catalog}.{args.schema}.{args.table}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
