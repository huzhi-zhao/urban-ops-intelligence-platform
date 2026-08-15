"""Unit tests for the DDL applier's SQL generation and safety rails.

These run without Trino: what is worth testing offline is the SQL this builds
and the guards around teardown, not the network round-trip. The round-trip is
the point of `make ddl-create` / `ddl-smoke` themselves.
"""

from __future__ import annotations

import pytest

from scripts.ddl.apply_ddl import (
    TrinoConfigError,
    _purge_storage,
    cmd_teardown,
    insert_statement,
    literal_for,
    load_trino_settings,
    normalise_prefix,
    render_ddl,
    schema_name,
    storage_location,
    tables_by_layer,
)
from scripts.ddl.ddl_parser import DdlColumn, load_all_ddl, parse_ddl_text

SAMPLE_DDL = """-- Silver contract: contracts/silver-contracts/example.yaml
CREATE TABLE IF NOT EXISTS example (
    -- not_null
    id VARCHAR,
    value DOUBLE,
    -- not_null
    "date" VARCHAR
)
WITH (
    format = 'PARQUET',
    external_location = 's3a://uoip/silver/example/',
    partitioned_by = ARRAY['date']
);
"""


# ── Parsing ───────────────────────────────────────────────────────────────────


def test_parse_reads_columns_partitions_and_location() -> None:
    ddl = parse_ddl_text(SAMPLE_DDL)
    assert ddl.names == ["id", "value", "date"]
    assert ddl.partitioned_by == ["date"]
    assert ddl.external_location == "s3a://uoip/silver/example/"
    assert ddl.layer == "silver"
    assert [c.name for c in ddl.data_columns] == ["id", "value"]


def test_layer_is_read_from_location_not_the_table_name() -> None:
    """A gold table whose name has no prefix still resolves to the gold schema."""
    ddl = parse_ddl_text(SAMPLE_DDL.replace("/silver/", "/gold/"))
    assert ddl.layer == "gold"


def test_layer_survives_a_location_prefix() -> None:
    ddl = parse_ddl_text(SAMPLE_DDL.replace("uoip/silver/", "uoip/smoke-20260814/silver/"))
    assert ddl.layer == "silver"


def test_layer_rejects_an_unrecognised_location() -> None:
    ddl = parse_ddl_text(SAMPLE_DDL.replace("/silver/", "/bronze/"))
    with pytest.raises(ValueError, match="neither a silver/ nor a gold/"):
        _ = ddl.layer


# ── Rendering ─────────────────────────────────────────────────────────────────


def test_render_substitutes_bucket_and_strips_the_comment_header() -> None:
    sql = render_ddl(SAMPLE_DDL, bucket="uoip")
    assert sql.startswith("CREATE TABLE")
    assert "{bucket}" not in sql
    # The trailing semicolon goes: Trino rejects it on a single-statement call.
    assert not sql.endswith(";")


def test_render_shifts_the_location_under_a_prefix() -> None:
    sql = render_ddl(SAMPLE_DDL, bucket="uoip", location_prefix="smoke-20260814")
    assert "'s3a://uoip/smoke-20260814/silver/example/'" in sql


def test_render_leaves_the_real_location_alone_without_a_prefix() -> None:
    """The guard that keeps a plain create from writing into a rehearsal path."""
    sql = render_ddl(SAMPLE_DDL, bucket="uoip")
    assert "'s3a://uoip/silver/example/'" in sql


# ── Schema naming ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("smoke-20260814", "smoke-20260814"),
        ("/smoke-1/", "smoke-1"),
        ("", ""),
        ("   ", ""),
        # A bare .strip("/") leaves this non-empty, which would let the teardown
        # guard through on a prefix naming no path at all.
        ("   /  ", ""),
    ],
)
def test_normalise_prefix(raw: str, expected: str) -> None:
    assert normalise_prefix(raw) == expected


def test_schema_names_are_per_layer() -> None:
    assert schema_name("silver") == "uoip_silver"
    assert schema_name("gold") == "uoip_gold"


def test_prefix_travels_into_the_schema_name() -> None:
    """Otherwise a rehearsal and the real tables would collide in the Metastore."""
    assert schema_name("silver", "smoke-20260814") == "uoip_silver_smoke_20260814"


def test_storage_location_matches_the_ddl_convention() -> None:
    assert storage_location("uoip", "gold", "dim_channel") == "s3a://uoip/gold/dim_channel/"
    assert (
        storage_location("uoip", "gold", "dim_channel", "smoke-1")
        == "s3a://uoip/smoke-1/gold/dim_channel/"
    )


# ── Smoke literals ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("sql_type", "expected"),
    [
        ("VARCHAR", "'smoke_0'"),
        ("INTEGER", "1"),
        ("DOUBLE", "1.0"),
        ("BOOLEAN", "TRUE"),
        ("DATE", "DATE '2026-01-01'"),
        ("TIMESTAMP(6)", "TIMESTAMP '2026-01-01 00:00:00.000000'"),
    ],
)
def test_literal_for_every_type_the_ddl_uses(sql_type: str, expected: str) -> None:
    assert literal_for(DdlColumn("c", sql_type, not_null=True), row=0) == expected


def test_second_row_nulls_the_nullable_columns() -> None:
    assert literal_for(DdlColumn("c", "DOUBLE", not_null=False), row=1) == "NULL"


def test_second_row_keeps_not_null_columns_populated() -> None:
    assert literal_for(DdlColumn("c", "DOUBLE", not_null=True), row=1) == "2.0"


def test_unknown_type_raises_rather_than_guessing() -> None:
    with pytest.raises(ValueError, match="No smoke literal"):
        literal_for(DdlColumn("c", "VARBINARY", not_null=True), row=0)


def test_every_type_used_by_the_real_ddl_has_a_literal() -> None:
    """Discovery-based: adding a new Trino type to a DDL fails here, not in Trino."""
    for ddl in load_all_ddl().values():
        for column in ddl.columns:
            assert literal_for(column, row=0)


def test_insert_lists_columns_explicitly_and_keeps_partition_keys_last() -> None:
    sql = insert_statement(parse_ddl_text(SAMPLE_DDL), "example")
    assert sql.startswith('INSERT INTO example ("id", "value", "date") VALUES ')
    # Both rows in one statement; the second nulls `value` but not the two
    # not_null columns, one of which is the partition key.
    assert "('smoke_0', 1.0, 'smoke_0')" in sql
    assert "('smoke_1', NULL, 'smoke_1')" in sql


def test_partition_column_is_never_null_in_smoke_data() -> None:
    """A NULL partition key becomes __HIVE_DEFAULT_PARTITION__ and hides the bug."""
    for ddl in load_all_ddl().values():
        for name in ddl.partitioned_by:
            column = ddl.column(name)
            assert column is not None
            for row in range(2):
                assert literal_for(column, row) != "NULL", f"{ddl.table}.{name}"


# ── Wiring against the real DDL set ───────────────────────────────────────────


def test_all_tables_resolve_to_a_layer() -> None:
    grouped = tables_by_layer()
    assert len(grouped["silver"]) == 8
    assert len(grouped["gold"]) == 17


def test_teardown_refuses_to_run_without_a_prefix() -> None:
    """The single most destructive path in this repo: it must fail closed.

    Without a prefix, teardown would drop the production tables and delete
    silver/ and gold/ from the bucket — including the weather-archive and
    plow-zone-boundary data already written on 2026-08-12. It returns before
    opening any connection, so this needs no Trino.
    """
    settings = load_trino_settings(env={"TRINO_HOST": "compute-node"})
    assert cmd_teardown(settings, bucket="uoip", location_prefix="") == 2


def test_purge_storage_refuses_an_empty_prefix() -> None:
    """Second, independent guard: an empty prefix would enumerate all of bronze/."""
    with pytest.raises(ValueError, match="refusing to purge"):
        _purge_storage("uoip", "   /  ")


def test_trino_settings_require_a_host() -> None:
    with pytest.raises(TrinoConfigError, match="TRINO_HOST"):
        load_trino_settings(env={})


def test_trino_settings_default_everything_but_the_host() -> None:
    settings = load_trino_settings(env={"TRINO_HOST": "compute-node"})
    assert (settings.port, settings.user, settings.catalog) == (8080, "uoip", "hive")


def test_trino_port_must_be_numeric() -> None:
    with pytest.raises(TrinoConfigError, match="TRINO_PORT"):
        load_trino_settings(env={"TRINO_HOST": "h", "TRINO_PORT": "eight-thousand"})
