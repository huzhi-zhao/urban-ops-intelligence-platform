"""Offline tests for the Gold builder. No Trino, no MinIO."""

from __future__ import annotations

import csv
import re
from pathlib import Path

import pytest

from scripts.ddl.ddl_parser import DDL_DIR, parse_ddl_file
from scripts.gold import build_gold
from scripts.gold.build_gold import (
    LINEAGE_COLUMNS,
    SEED_DIR,
    TABLES,
    Table,
    check_order,
    seed_select,
    sql_literal,
)
from scripts.gold.gates import parse_gates

DML_DIR = build_gold.DML_DIR


def test_dependency_order_is_topological():
    check_order()


def test_dependency_order_catches_a_bad_order(monkeypatch):
    broken = (Table("b", "dims", deps=("a",)), Table("a", "dims"))
    monkeypatch.setattr(build_gold, "TABLES", broken)
    with pytest.raises(ValueError, match="depends on"):
        check_order()


def test_every_table_has_a_ddl():
    for table in TABLES:
        assert (DDL_DIR / f"{table.name}.sql").exists(), table.name


def test_no_duplicate_tables():
    names = [t.name for t in TABLES]
    assert len(names) == len(set(names))


def test_stages_are_grouped_and_ordered():
    """seeds before dims before facts before scoring — --only relies on it."""
    order = {"seeds": 0, "dims": 1, "facts": 2, "scoring": 3}
    seen = [order[t.stage] for t in TABLES]
    assert seen == sorted(seen)


# --------------------------------------------------------------------- seeds


def test_seed_csv_columns_match_their_ddl():
    for table in TABLES:
        if not table.seed:
            continue
        ddl = parse_ddl_file(DDL_DIR / f"{table.name}.sql")
        expected = [c.name for c in ddl.data_columns if c.name not in LINEAGE_COLUMNS]
        header = next(csv.reader((SEED_DIR / table.seed).open(encoding="utf-8")))
        assert [h.strip() for h in header] == expected, table.seed


def test_seed_row_counts():
    counts = {"winter_category.csv": 7, "channel.csv": 15}
    for name, expected in counts.items():
        rows = list(csv.DictReader((SEED_DIR / name).open(encoding="utf-8")))
        assert len(rows) == expected, name


def test_winter_category_arbitration_order_is_pinned():
    """The CSV's row order IS the multi-hit arbitration order.

    dim_winter_category's schema is frozen and has no priority column, so row
    order is the only place this rule can live. Measured 2026-08-19: all 13
    multi-hit `type` values contain SNOW, and both WINDROW (1 type) and
    ICE_CONTROL (3 types) have *every* one of their types in that list. With
    SNOW first, those two effective classes would resolve to zero types and
    their panel cells would read as an empty data fact rather than as the
    consequence of an arbitration rule. Hence most-specific-first.

    If you are here because this test failed: reordering the CSV changes which
    winter_category 13 type values land in. That is a semantic change and needs
    a sign-off, not a test update.
    """
    rows = list(csv.DictReader((SEED_DIR / "winter_category.csv").open(encoding="utf-8")))
    assert [r["winter_category"] for r in rows] == [
        "WINDROW",
        "ICE_CONTROL",
        "PLOW",
        "PLOUGH",
        "SANDING",
        "FROZEN",
        "SNOW",
    ]


def test_winter_category_patterns_stay_exact():
    """`%ICE%` matched Serv-ice / Pol-ice and inflated winter share to 10.40%."""
    rows = list(csv.DictReader((SEED_DIR / "winter_category.csv").open(encoding="utf-8")))
    patterns = {r["winter_category"]: r["keyword_pattern"] for r in rows}
    assert patterns["ICE_CONTROL"] == "%ICE CONTROL%"
    assert all(p.count("%") == 2 for p in patterns.values())
    assert not any(" OR " in p.upper() for p in patterns.values())


def test_exactly_one_ineffective_winter_category():
    rows = list(csv.DictReader((SEED_DIR / "winter_category.csv").open(encoding="utf-8")))
    ineffective = [r["winter_category"] for r in rows if r["is_effective"] == "false"]
    assert ineffective == ["PLOUGH"]


def test_channel_vof_merge():
    rows = list(csv.DictReader((SEED_DIR / "channel.csv").open(encoding="utf-8")))
    not_comparable = {r["channel_raw"] for r in rows if r["is_comparable_pre_2022"] == "false"}
    assert not_comparable == {"Self Service", "Mobile", "SMS In"}
    for row in rows:
        if row["channel_raw"] in not_comparable:
            assert row["channel_normalized"] == "VOF"


def test_recommendation_rules_have_a_fallback():
    """Without one, L3 degrades to empty text instead of saying it degraded."""
    rows = list(csv.DictReader((SEED_DIR / "recommendation_rules.csv").open(encoding="utf-8")))
    assert sum(r["is_fallback"] == "true" for r in rows) >= 1


def test_service_type_keywords_carry_no_vof():
    """`_vof` is a channel marker, not a priority one — 48 types carry it."""
    text = (SEED_DIR / "service_type_keywords.csv").read_text(encoding="utf-8").lower()
    assert "vof" not in text
    rows = list(csv.DictReader((SEED_DIR / "service_type_keywords.csv").open(encoding="utf-8")))
    assert {r["priority_weight"] for r in rows} == {"1", "2", "3"}


# ------------------------------------------------------------------ literals


@pytest.mark.parametrize(
    ("value", "sql_type", "expected"),
    [
        ("SNOW", "VARCHAR", "'SNOW'"),
        ("O'Connor", "VARCHAR", "'O''Connor'"),
        ("true", "BOOLEAN", "true"),
        ("7", "INTEGER", "7"),
        ("1.5", "DOUBLE", "1.5"),
        ("2026-08-19", "DATE", "DATE '2026-08-19'"),
        ("", "VARCHAR", "NULL"),
    ],
)
def test_sql_literal(value, sql_type, expected):
    assert sql_literal(value, sql_type) == expected


def test_sql_literal_rejects_a_non_boolean():
    with pytest.raises(ValueError, match="boolean"):
        sql_literal("yes", "BOOLEAN")


def test_seed_select_rejects_a_column_mismatch(tmp_path, monkeypatch):
    bad = tmp_path / "winter_category.csv"
    bad.write_text("keyword_pattern,winter_category,is_effective\n%SNOW%,SNOW,true\n")
    monkeypatch.setattr(build_gold, "SEED_DIR", tmp_path)
    ddl = parse_ddl_file(DDL_DIR / "dim_winter_category.sql")
    table = Table("dim_winter_category", "seeds", seed="winter_category.csv")
    with pytest.raises(ValueError, match="do not match"):
        seed_select(table, ddl.data_columns, "run", "2026-08-19 00:00:00.000000")


# ----------------------------------------------------------------- DML files


def _dml_files() -> list[Path]:
    return sorted(DML_DIR.glob("*.sql"))


def test_dml_files_have_no_select_star():
    for path in _dml_files():
        body = re.sub(r"--.*", "", path.read_text(encoding="utf-8"))
        assert not re.search(r"SELECT\s+\*", body, re.I), path.name


def test_dml_files_are_bare_selects():
    """The INSERT and its column list are composed from the DDL, not written here."""
    for path in _dml_files():
        body = re.sub(r"--.*", "", path.read_text(encoding="utf-8")).upper()
        assert "CREATE TABLE" not in body, path.name
        assert "INSERT INTO" not in body, path.name


def test_dml_files_carry_no_catalog_or_schema_qualification():
    for path in _dml_files():
        body = re.sub(r"--.*", "", path.read_text(encoding="utf-8"))
        assert "hive." not in body, path.name
        assert "uoip_gold" not in body, path.name


def test_dml_files_qualify_every_silver_table_with_the_silver_placeholder():
    """A Silver table read from a DML file must be reached through {{ silver }}.

    🔴 Measured 2026-08-19: six DML files read `FROM silver_service_request`
    unqualified and every one of them failed at execution with
    `TABLE_NOT_FOUND: hive.uoip_gold.silver_service_request`. The connection
    injects the *Gold* schema as the default (`_connect(settings,
    self.gold_schema)`), so a bare Silver table name resolves in the wrong
    schema — and the sibling test above only forbids hardcoding `hive.` /
    `uoip_gold`, which leaves exactly this gap between the two rules.

    The placeholder is double-braced jinja rather than a single-brace
    substitution because it sits in a FROM/JOIN clause, outside any string
    literal: sqlfluff parses `{silver}.t` as an unparsable section and then
    silently stops enforcing the SELECT * and date-predicate checks on the
    rest of the file. `.sqlfluff` resolves it for linting; build_gold.py
    substitutes the real schema at run time.
    """
    for path in _dml_files():
        body = re.sub(r"--.*", "", path.read_text(encoding="utf-8"))
        for match in re.finditer(r"\b(?:FROM|JOIN)\s+(\S+)", body, re.I):
            reference = match.group(1)
            if "silver_" not in reference:
                continue
            assert reference.startswith("{{ silver }}."), (
                f"{path.name} reads {reference} without the {{{{ silver }}}} schema "
                f"qualifier — that resolves in the Gold schema and fails with "
                f"TABLE_NOT_FOUND at execution time."
            )


def test_dml_files_emit_the_three_lineage_columns():
    for path in _dml_files():
        body = path.read_text(encoding="utf-8")
        for column in LINEAGE_COLUMNS:
            assert column in body, f"{path.name} is missing {column}"


def test_dml_reading_silver_service_request_carries_a_date_predicate():
    """gold-sql.md R1. A whole-table scan of 4,878 partitions times out (O13).

    Automated because R1 cannot be met by remembering it: the failure is a
    read timeout minutes into a build, not a syntax error.
    """
    for path in _dml_files():
        body = re.sub(r"--.*", "", path.read_text(encoding="utf-8"))
        if "silver_service_request" not in body:
            continue
        assert "open_date_local" in body, (
            f"{path.name} reads silver_service_request without an open_date_local "
            f"predicate — that is a full 4,878-partition scan (R1/O13)."
        )


def test_chunked_dml_declares_its_chunking():
    for name in build_gold.CHUNKED:
        path = DML_DIR / f"{name}.sql"
        if not path.exists():
            continue
        head = path.read_text(encoding="utf-8")
        assert "-- chunked_by:" in head, name
        assert "-- combine:" in head, name
        assert "{chunk_start}" in head and "{chunk_end}" in head, name


# --------------------------------------------------------------------- gates


def test_every_table_has_at_least_one_gate():
    """A table with no acceptance check is a table nothing would notice breaking."""
    for table in TABLES:
        gates, _ = parse_gates((DDL_DIR / f"{table.name}.sql").read_text())
        assert gates or table.extra_gates, (
            f"{table.name} has neither a COUNT(*) gate in its DDL header nor an "
            f"extra_gates entry in build_gold.TABLES"
        )


def test_gate_parser_reads_a_predicate_gate():
    gates, _ = parse_gates((DDL_DIR / "dim_winter_category.sql").read_text())
    assert [(g.predicate, g.expected) for g in gates] == [(None, 7), ("is_effective = true", 6)]


def test_gate_sql_is_scoped_to_the_table():
    gates, _ = parse_gates((DDL_DIR / "dim_winter_category.sql").read_text())
    assert gates[1].sql("dim_winter_category") == (
        "SELECT COUNT(*) FROM dim_winter_category WHERE is_effective = true"
    )


def test_reserved_column_names_are_quoted():
    """`type` and `date` are declared quoted in the DDL; an unquoted reference
    parses as the keyword and fails at build time rather than at review time."""
    assert build_gold._quote("type") == '"type"'
    assert build_gold._quote("date") == '"date"'
    assert build_gold._quote("plow_zone") == "plow_zone"
    for table in TABLES:
        ddl = parse_ddl_file(DDL_DIR / f"{table.name}.sql")
        for column in ddl.data_columns:
            if column.name in build_gold._RESERVED:
                assert f'"{column.name}"' in (DDL_DIR / f"{table.name}.sql").read_text()


def test_prose_relationships_are_reported_as_unenforced():
    """dim_service_type's DDL states its anti-join rule in prose only."""
    gates, notes = parse_gates((DDL_DIR / "dim_service_type.sql").read_text())
    assert gates == []
    assert notes
    table = next(t for t in TABLES if t.name == "dim_service_type")
    assert table.extra_gates, "the anti-join must be enforced somewhere"


def test_extra_gates_are_equalities_unless_they_ask_for_a_lower_bound():
    """A four-element gate must spell out ">=" — nothing else is a comparison.

    The shape check matters because the fourth element is optional: a typo
    (">", "gte") would silently fall through to the equality branch and gate
    on a number that was only ever meant as a floor.
    """
    for table in TABLES:
        for gate in table.extra_gates:
            assert len(gate) in (3, 4), (table.name, gate[0])
            description, sql, expected, *rest = gate
            assert isinstance(expected, int)
            assert "SELECT" in sql.upper()
            if rest:
                assert rest[0] == ">=", (table.name, description, rest)


def test_only_the_live_upstream_number_is_a_lower_bound():
    """Everything that detects a broken build stays an equality.

    916 drifted because it was measured off the live Socrata API, not because
    a build broke (launch doc §4.9). The row-count and panel-shape gates did
    not drift and must not be relaxed the same way — a failed storage purge
    shows up as doubled rows and nothing else raises (gold-sql.md R4).
    """
    bounded = [
        (t.name, g[0]) for t in TABLES for g in t.extra_gates if len(g) == 4
    ]
    assert bounded == [
        (
            "fact_service_request_zone_event",
            "scheduling-era cells with at least one request "
            "(908 measured 2026-08-19; design's 916 is stale, see §4.9)",
        )
    ]


# ------------------------------------------------- seed-derived placeholders


def test_winter_category_order_placeholder_follows_the_csv_row_order():
    """The arbitration rule travels as data, not as a CASE ladder in SQL."""
    value = build_gold.seed_placeholders()["winter_category_order"]
    rows = list(csv.DictReader((SEED_DIR / "winter_category.csv").open(encoding="utf-8")))
    assert value.split(",") == [r["winter_category"] for r in rows]


def test_service_type_keywords_placeholder_encodes_all_nine_patterns():
    value = build_gold.seed_placeholders()["service_type_keywords"]
    entries = [e.split(";") for e in value.split("|")]
    rows = list(csv.DictReader((SEED_DIR / "service_type_keywords.csv").open(encoding="utf-8")))
    assert len(entries) == len(rows) == 9
    assert all(len(e) == 3 for e in entries)
    assert [e[1] for e in entries] == [r["pattern_regex"] for r in rows]


def test_seed_placeholder_rejects_a_cell_containing_a_delimiter(tmp_path, monkeypatch):
    """A pattern with a `|` in it would silently split into two bogus rows."""
    seed_dir = tmp_path
    (seed_dir / "winter_category.csv").write_text(
        "winter_category,keyword_pattern,is_effective\nSNOW,%SNOW%,true\n", encoding="utf-8"
    )
    (seed_dir / "service_type_keywords.csv").write_text(
        "match_priority,pattern_regex,priority_weight,note\n10,(?i)snow|ice,3,bad\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(build_gold, "SEED_DIR", seed_dir)
    with pytest.raises(ValueError, match="delimiter"):
        build_gold.seed_placeholders()


def test_new_dim_dml_leaves_no_unsubstituted_placeholder():
    """Every {placeholder} a DML file writes must be one the builder fills."""
    known = {
        "bucket",
        "silver",
        "etl_run_id",
        "built_at",
        "chunk_start",
        "chunk_end",
        *build_gold.seed_placeholders(),
    }
    for path in _dml_files():
        body = re.sub(r"--.*", "", path.read_text(encoding="utf-8"))
        found = set(re.findall(r"\{(\w+)\}", body))
        assert found <= known, f"{path.name} uses unknown placeholder(s) {found - known}"


def test_chunked_tables_anti_join_what_earlier_chunks_inserted():
    """Overlapping chunks keep their PK only via the anti-join. R2/§7.3."""
    for name in ("dim_admin_label", "dim_service_type"):
        body = re.sub(r"--.*", "", (DML_DIR / f"{name}.sql").read_text(encoding="utf-8"))
        assert f"FROM {name}" in body, f"{name} does not anti-join against itself"
