"""
Shared Trino helper for Silver DAGs: registers newly written Hive partition
directories with the Metastore.

Spark writes Silver partitions directly to object storage via s3a, never
through Hive Metastore — Trino (and anything reading through it, including
Superset) sees nothing for a new date partition until told to look.
`sync_partition_metadata` is that "look again" call. Without it, a Spark job
can finish cleanly and write correct Parquet while `SELECT COUNT(*)` on the
table stays 0 until someone runs this by hand — discovered the hard way
during L1's single-season gate (see
docs/dev/launch/20260817-silver-etl-runnable-launch.md §3.1/§5). Every DAG
that writes a Silver partitioned table must chain a call to this after its
Spark task, not just the two service-request DAGs this was found on.

Connection settings mirror scripts/ddl/apply_ddl.py's `load_trino_settings`
(TRINO_HOST/PORT/USER/CATALOG) — kept as an independent minimal copy rather
than imported, the same way dags/_spark_common.py independently redefines
the S3A jar coordinates instead of importing them from the CLI side. Trino
is platform infrastructure this repo does not start (ADR 0006 §9).
"""

from __future__ import annotations

import os


def sync_partition_metadata(schema: str, table: str) -> None:
    """CALL system.sync_partition_metadata(..., mode => 'FULL') on one table."""
    import trino

    host = (os.environ.get("TRINO_HOST") or "").strip()
    if not host:
        raise ValueError(
            "TRINO_HOST is not set — cannot sync Hive partitions for "
            f"{schema}.{table}. See .env.example."
        )
    port = int((os.environ.get("TRINO_PORT") or "8080").strip())
    user = (os.environ.get("TRINO_USER") or "").strip() or "uoip"
    catalog = (os.environ.get("TRINO_CATALOG") or "").strip() or "hive"

    conn = trino.dbapi.connect(
        host=host, port=port, user=user, catalog=catalog, schema=schema,
    )
    cursor = conn.cursor()
    cursor.execute(
        f"CALL {catalog}.system.sync_partition_metadata("
        f"schema_name => '{schema}', table_name => '{table}', mode => 'FULL')"
    )
    cursor.fetchall()
