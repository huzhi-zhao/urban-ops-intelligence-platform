"""The four test families are parsed off the real DDL headers, offline.

These tests cannot run the SQL — there is no query engine in unit tests — so
they check the two things a reader of the DDL cannot verify by eye: that every
annotation form is actually picked up, and that nullable columns are excluded
so a documented NULL never fails a check it does not violate.
"""

from __future__ import annotations

from scripts.gold.build_gold import TABLES
from scripts.gold.dq_assertions import assertions_for


def _by_family(table: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for a in assertions_for(table):
        out.setdefault(a.family, []).append(a.subject)
    return out


def test_every_gold_table_carries_at_least_a_unique_and_a_not_null_check():
    for table in TABLES:
        families = _by_family(table.name)
        assert "unique" in families, table.name
        assert "not_null" in families, table.name


def test_all_five_annotation_forms_are_parsed():
    families = _by_family("fact_winter_event_zone_load")
    assert families["unique"] == ["(snowfall_event_id, plow_zone)"]
    assert set(families["accepted_values"]) == {
        "score_status",
        "load_level",
        "score_weight_profile",
    }
    assert "load_score in [0, 100]" in families["range"]
    assert "plow_zone -> dim_plow_zone.plow_zone" in families["relationships"]
    assert "score_status" in families["not_null"]


def test_a_rule_stated_both_table_side_and_column_side_is_only_run_once():
    subjects = _by_family("fact_winter_event_zone_load")["accepted_values"]
    assert len(subjects) == len(set(subjects))


def test_nullable_columns_are_checked_only_on_their_non_null_rows():
    """rank_factor is NULL on the 924 partial_no_rank cells by design (O1).
    A range check without the IS NOT NULL guard would report them as
    violations and the whole baseline would get muted."""
    for a in assertions_for("fact_winter_event_zone_load"):
        if a.family in {"range", "accepted_values", "relationships"}:
            assert "IS NOT NULL" in a.sql, a.subject


def test_no_assertion_reads_silver():
    """gold-sql.md R1: a Silver read needs a date predicate. Nothing here has
    one, so nothing here may touch Silver."""
    for table in TABLES:
        for a in assertions_for(table.name):
            assert "silver_" not in a.sql, f"{table.name}: {a.subject}"


def test_no_admin_unit_enters_a_scoring_fact_key():
    """ADR 0010 D2, and the S2 bus matrix's structural criterion (design §6.2).

    The criterion as written there is `grep -l "region_type" sql/ddl/fact_*.sql`
    returns nothing — but that grep matches the `-- forbidden_columns:` header
    line itself, so it reports four files while the property actually holds.
    Parse the columns instead of grepping the text."""
    from scripts.ddl.ddl_parser import DDL_DIR, parse_ddl_file

    forbidden = {"region_type", "ward", "neighbourhood"}
    for path in sorted(DDL_DIR.glob("fact_*.sql")):
        names = {c.name for c in parse_ddl_file(path).columns}
        assert not (names & forbidden), f"{path.stem} carries {names & forbidden}"
