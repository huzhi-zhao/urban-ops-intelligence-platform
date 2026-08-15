"""Three-way consistency: contract YAML <-> sql/ddl/*.sql <-> spark/schemas StructType.

The contracts were frozen on 2026-08-13 (docs/dev/launch/20260813-gold-silver-
schema-derivation-launch.md). From S4 on, every schema change has to travel
through all three artefacts or the pipeline writes columns Trino cannot read.
Twenty-five tables x ~15 columns is past the point where eyeballing a diff
works, so the check is mechanical.

The contract is the **authority**: DDL and StructType are compared against it,
never against each other. A disagreement is always reported as "the DDL/schema
drifted", because the contract is the artefact under change control.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pyspark.sql.types import (
    BooleanType,
    DataType,
    DateType,
    DoubleType,
    IntegerType,
    StringType,
    StructType,
    TimestampType,
)

from scripts.ddl.ddl_parser import Ddl, parse_ddl_file
from spark.schemas.gold_dim_schemas import (
    DIM_ADMIN_LABEL_GOLD_SCHEMA,
    DIM_CHANNEL_GOLD_SCHEMA,
    DIM_PLOW_EVENT_GOLD_SCHEMA,
    DIM_PLOW_ZONE_GOLD_SCHEMA,
    DIM_RECOMMENDATION_RULES_GOLD_SCHEMA,
    DIM_REGION_CROSSWALK_GOLD_SCHEMA,
    DIM_SERVICE_TYPE_GOLD_SCHEMA,
    DIM_SNOWFALL_EVENT_GOLD_SCHEMA,
    DIM_WINTER_CATEGORY_GOLD_SCHEMA,
)
from spark.schemas.gold_fact_schemas import (
    FACT_EVENT_ZONE_RANK_GOLD_SCHEMA,
    FACT_PARKING_BAN_GOLD_SCHEMA,
    FACT_PLOW_SHIFT_GOLD_SCHEMA,
    FACT_RECOMMENDATION_GOLD_SCHEMA,
    FACT_REQUEST_FORECAST_GOLD_SCHEMA,
    FACT_SERVICE_REQUEST_ZONE_EVENT_GOLD_SCHEMA,
    FACT_WINTER_EVENT_ZONE_LOAD_GOLD_SCHEMA,
    FACT_WINTER_REQUEST_DAILY_BY_LABEL_GOLD_SCHEMA,
)
from spark.schemas.parking_ban_schemas import PARKING_BAN_SILVER_SCHEMA
from spark.schemas.plow_shift_schemas import PLOW_SHIFT_SILVER_SCHEMA
from spark.schemas.plow_zone_boundary_schemas import PLOW_ZONE_BOUNDARY_SILVER_SCHEMA
from spark.schemas.service_request_schemas import SERVICE_REQUEST_SILVER_SCHEMA
from spark.schemas.snow_clearing_address_schemas import SNOW_CLEARING_ADDRESS_SILVER_SCHEMA
from spark.schemas.weather_schemas import (
    SNOWFALL_EVENT_SCHEMA,
    WEATHER_ARCHIVE_SILVER_SCHEMA,
    WEATHER_FORECAST_SILVER_SCHEMA,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DIRS = (
    REPO_ROOT / "contracts" / "silver-contracts",
    REPO_ROOT / "contracts" / "gold-contracts",
)
DDL_DIR = REPO_ROOT / "sql" / "ddl"

# Contract type -> (Trino type in the DDL, Spark type in the StructType).
# The six-type vocabulary is closed: an unknown type fails rather than being
# skipped, so a new one cannot enter untranslated on either side.
TYPE_MAP: dict[str, tuple[str, DataType]] = {
    "string": ("VARCHAR", StringType()),
    "int": ("INTEGER", IntegerType()),
    "double": ("DOUBLE", DoubleType()),
    "boolean": ("BOOLEAN", BooleanType()),
    "date": ("DATE", DateType()),
    "timestamp": ("TIMESTAMP(6)", TimestampType()),
}

# Explicit, not derived from a naming convention: a table whose StructType is
# missing must fail loudly here rather than silently drop out of the sweep.
# Keyed on the contract's `table:` field, which is the real table name —
# snowfall_events.yaml declares `silver_snowfall_event` (launch doc C1).
SCHEMA_REGISTRY: dict[str, StructType] = {
    "dim_admin_label": DIM_ADMIN_LABEL_GOLD_SCHEMA,
    "dim_channel": DIM_CHANNEL_GOLD_SCHEMA,
    "dim_plow_event": DIM_PLOW_EVENT_GOLD_SCHEMA,
    "dim_plow_zone": DIM_PLOW_ZONE_GOLD_SCHEMA,
    "dim_recommendation_rules": DIM_RECOMMENDATION_RULES_GOLD_SCHEMA,
    "dim_region_crosswalk": DIM_REGION_CROSSWALK_GOLD_SCHEMA,
    "dim_service_type": DIM_SERVICE_TYPE_GOLD_SCHEMA,
    "dim_snowfall_event": DIM_SNOWFALL_EVENT_GOLD_SCHEMA,
    "dim_winter_category": DIM_WINTER_CATEGORY_GOLD_SCHEMA,
    "fact_event_zone_rank": FACT_EVENT_ZONE_RANK_GOLD_SCHEMA,
    "fact_parking_ban": FACT_PARKING_BAN_GOLD_SCHEMA,
    "fact_plow_shift": FACT_PLOW_SHIFT_GOLD_SCHEMA,
    "fact_recommendation": FACT_RECOMMENDATION_GOLD_SCHEMA,
    "fact_request_forecast": FACT_REQUEST_FORECAST_GOLD_SCHEMA,
    "fact_service_request_zone_event": FACT_SERVICE_REQUEST_ZONE_EVENT_GOLD_SCHEMA,
    "fact_winter_event_zone_load": FACT_WINTER_EVENT_ZONE_LOAD_GOLD_SCHEMA,
    "fact_winter_request_daily_by_label": FACT_WINTER_REQUEST_DAILY_BY_LABEL_GOLD_SCHEMA,
    "silver_parking_ban": PARKING_BAN_SILVER_SCHEMA,
    "silver_plow_shift": PLOW_SHIFT_SILVER_SCHEMA,
    "silver_plow_zone_boundary": PLOW_ZONE_BOUNDARY_SILVER_SCHEMA,
    "silver_service_request": SERVICE_REQUEST_SILVER_SCHEMA,
    "silver_snow_clearing_address": SNOW_CLEARING_ADDRESS_SILVER_SCHEMA,
    "silver_snowfall_event": SNOWFALL_EVENT_SCHEMA,
    "silver_weather_archive": WEATHER_ARCHIVE_SILVER_SCHEMA,
    "silver_weather_forecast": WEATHER_FORECAST_SILVER_SCHEMA,
}

# The DDL parser used to live here. It moved to scripts/ddl/ddl_parser.py when
# apply_ddl needed the same information to create and populate the tables:
# two parsers for one hand-rolled DDL dialect would drift, and the drift would
# show up as this test passing against a shape Trino rejects.


def load_contracts() -> list[dict]:
    contracts = []
    for directory in CONTRACT_DIRS:
        for path in sorted(directory.glob("*.yaml")):
            contract = yaml.safe_load(path.read_text())
            contract["_path"] = path
            contracts.append(contract)
    return contracts


CONTRACTS = load_contracts()
CONTRACTS_BY_TABLE = {c["table"]: c for c in CONTRACTS}


@pytest.fixture(scope="module")
def ddls() -> dict[str, Ddl]:
    return {path.stem: parse_ddl_file(path) for path in DDL_DIR.glob("*.sql")}


def _pytest_ids() -> list[str]:
    return sorted(CONTRACTS_BY_TABLE)


@pytest.fixture(params=_pytest_ids())
def table(request: pytest.FixtureRequest) -> str:
    return request.param


def test_every_contract_has_a_ddl_and_vice_versa(ddls: dict[str, Ddl]) -> None:
    """No table may exist in only one of the two places.

    A DDL without a contract is an unreviewed table; a contract without a DDL
    is a table the build will fail to create. The DDL file is named after the
    contract's `table:` field, not its filename — snowfall_events.yaml ->
    sql/ddl/silver_snowfall_event.sql.
    """
    contract_tables = set(CONTRACTS_BY_TABLE)
    ddl_tables = set(ddls)
    assert contract_tables == ddl_tables, (
        f"contract-only tables: {sorted(contract_tables - ddl_tables)}; "
        f"DDL-only tables: {sorted(ddl_tables - contract_tables)}"
    )


def test_every_contract_has_a_structtype() -> None:
    contract_tables = set(CONTRACTS_BY_TABLE)
    registered = set(SCHEMA_REGISTRY)
    assert contract_tables == registered, (
        f"contracts with no StructType in SCHEMA_REGISTRY: "
        f"{sorted(contract_tables - registered)}; "
        f"registered but no contract: {sorted(registered - contract_tables)}"
    )


def test_contract_types_are_in_the_known_vocabulary(table: str) -> None:
    """The type vocabulary is closed at six entries.

    A seventh type would land in the DDL and the StructType untranslated and
    be compared against nothing, so it fails here instead.
    """
    unknown = {
        c["name"]: c["type"] for c in CONTRACTS_BY_TABLE[table]["columns"] if c["type"] not in TYPE_MAP
    }
    assert not unknown, f"{table}: types outside TYPE_MAP: {unknown}"


def test_ddl_columns_match_contract(table: str, ddls: dict[str, Ddl]) -> None:
    contract_names = [c["name"] for c in CONTRACTS_BY_TABLE[table]["columns"]]
    ddl = ddls[table]
    missing = [n for n in contract_names if n not in ddl.names]
    extra = [n for n in ddl.names if n not in contract_names]
    assert not missing and not extra, (
        f"{table}: DDL drifted from contract — missing {missing}, extra {extra}"
    )


def test_ddl_types_match_contract(table: str, ddls: dict[str, Ddl]) -> None:
    ddl = ddls[table]
    mismatches = {}
    for column in CONTRACTS_BY_TABLE[table]["columns"]:
        expected, _ = TYPE_MAP[column["type"]]
        actual = ddl.column(column["name"])
        if actual is not None and actual.sql_type != expected:
            mismatches[column["name"]] = f"contract {column['type']} -> {expected}, DDL {actual.sql_type}"
    assert not mismatches, f"{table}: {mismatches}"


def test_ddl_not_null_comments_match_contract(table: str, ddls: dict[str, Ddl]) -> None:
    """`-- not_null` is the DDL's only nullability carrier — it must be right.

    The Hive connector enforces nothing, so this comment is what a human reads
    when deciding whether a column can be left empty. A stale one is worse than
    none at all.
    """
    ddl = ddls[table]
    mismatches = {}
    for column in CONTRACTS_BY_TABLE[table]["columns"]:
        actual = ddl.column(column["name"])
        if actual is None:
            continue
        expected_not_null = not column["nullable"]
        if actual.not_null != expected_not_null:
            mismatches[column["name"]] = (
                f"contract nullable={column['nullable']}, DDL not_null={actual.not_null}"
            )
    assert not mismatches, f"{table}: {mismatches}"


def test_structtype_matches_contract(table: str) -> None:
    """Field names, order, types and nullability, all against the contract.

    Order is checked here but deliberately *not* against the DDL: Hive requires
    partition columns last, so a partitioned table's DDL order legitimately
    differs from the contract's. The StructType has no such constraint.
    """
    contract_columns = CONTRACTS_BY_TABLE[table]["columns"]
    schema = SCHEMA_REGISTRY[table]

    expected_names = [c["name"] for c in contract_columns]
    actual_names = [f.name for f in schema.fields]
    assert actual_names == expected_names, (
        f"{table}: StructType fields drifted from contract.\n"
        f"  contract: {expected_names}\n"
        f"  schema:   {actual_names}"
    )

    mismatches = {}
    for column, field in zip(contract_columns, schema.fields, strict=True):
        _, expected_type = TYPE_MAP[column["type"]]
        if field.dataType != expected_type:
            mismatches[column["name"]] = f"contract {column['type']}, schema {field.dataType}"
        if field.nullable != column["nullable"]:
            mismatches.setdefault(column["name"], "")
            mismatches[column["name"]] += (
                f" nullable: contract {column['nullable']}, schema {field.nullable}"
            )
    assert not mismatches, f"{table}: {mismatches}"


def test_primary_key_columns_exist_and_are_not_nullable(table: str) -> None:
    """A nullable PK component would make the grain unenforceable.

    ADR 0010 D1 gives every fact exactly one grain; the PK is how that grain is
    written down. Nothing downstream enforces it, so this is the only gate.
    """
    contract = CONTRACTS_BY_TABLE[table]
    by_name = {c["name"]: c for c in contract["columns"]}
    for key_column in contract["primary_key"]:
        assert key_column in by_name, f"{table}: primary_key column '{key_column}' is not declared"
        assert not by_name[key_column]["nullable"], (
            f"{table}: primary_key column '{key_column}' is nullable"
        )


def test_partitioning_agrees_and_partition_columns_come_last(
    table: str, ddls: dict[str, Ddl]
) -> None:
    """Hive reads partition columns positionally — last, or the table is wrong.

    Getting this wrong does not raise at CREATE time; it shifts every value in
    the partitioned columns by one position at read time.
    """
    contract = CONTRACTS_BY_TABLE[table]
    declared = contract.get("partition_by")
    expected = [] if declared is None else [declared] if isinstance(declared, str) else list(declared)
    ddl = ddls[table]

    assert ddl.partitioned_by == expected, (
        f"{table}: contract partition_by={expected}, DDL partitioned_by={ddl.partitioned_by}"
    )
    if expected:
        assert ddl.names[-len(expected) :] == expected, (
            f"{table}: partition columns must be the last columns in the DDL — "
            f"declared {expected}, DDL ends with {ddl.names[-len(expected):]}"
        )
