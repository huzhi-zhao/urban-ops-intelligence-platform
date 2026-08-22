"""Rule-file parsing and comparator semantics for the out-of-pipeline DQ audit.

The load-bearing test here is
`test_no_out_of_pipeline_row_count_rule_is_an_equality`: it is the mirror of
the in-pipeline `test_only_the_live_upstream_number_is_a_lower_bound`, and the
two together state where each shape belongs.
"""

from __future__ import annotations

import textwrap

import pytest
import yaml

from scripts.dq.rules import (
    CHECK_TYPES,
    DqRuleError,
    Rule,
    evaluate,
    load_rules,
)


def write_rules(tmp_path, rules: list[dict]):
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump({"version": 1, "rules": rules}))
    return path


def a_rule(**overrides) -> dict:
    base = {
        "id": "R1",
        "layer": "gold",
        "table": "dim_plow_zone",
        "dimension": "statistical",
        "check": {"type": "row_count"},
        "comparator": ">=",
        "expected": 22,
        "severity": "error",
        "cadence": "daily",
    }
    base.update(overrides)
    return base


def rule(**overrides) -> Rule:
    raw = a_rule(**overrides)
    return Rule(
        id=raw["id"],
        layer=raw["layer"],
        table=raw["table"],
        dimension=raw["dimension"],
        check=raw["check"],
        comparator=raw["comparator"],
        expected=raw["expected"],
        severity=raw["severity"],
        cadence=raw["cadence"],
    )


# ── the shipped rule file ────────────────────────────────────────────────────


def test_the_shipped_rule_file_parses():
    rules = load_rules()
    assert rules
    assert len({r.id for r in rules}) == len(rules)


def test_no_out_of_pipeline_row_count_rule_is_an_equality():
    """design §4.2 — the whole reason this audit's numbers differ from the gates'.

    In-pipeline gates compare a build against itself, so equality is right
    there. Out here Gold gets rebuilt and the upstream appends, so an equality
    goes stale and the rule gets muted instead of updated.
    """
    for r in load_rules():
        if r.check_type == "row_count":
            assert r.comparator != "==", f"{r.id} pins a row count to an exact value"


def test_every_shipped_rule_records_where_its_number_came_from():
    for r in load_rules():
        assert r.note.strip(), f"{r.id} has no anchor — design §3.3 forbids unanchored rules"


def test_every_check_type_used_is_implemented():
    for r in load_rules():
        assert r.check_type in CHECK_TYPES


# ── validation ───────────────────────────────────────────────────────────────


def test_duplicate_ids_are_rejected(tmp_path):
    path = write_rules(tmp_path, [a_rule(), a_rule()])
    with pytest.raises(DqRuleError, match="duplicate rule id"):
        load_rules(path)


def test_equality_on_a_row_count_is_rejected(tmp_path):
    path = write_rules(tmp_path, [a_rule(comparator="==", expected=25)])
    with pytest.raises(DqRuleError, match="may not be an equality"):
        load_rules(path)


def test_equality_is_allowed_on_a_check_that_is_not_a_row_count(tmp_path):
    path = write_rules(
        tmp_path, [a_rule(check={"type": "empty_tables"}, comparator="==", expected=0)]
    )
    assert load_rules(path)[0].comparator == "=="


@pytest.mark.parametrize(
    "field,value",
    [("layer", "meta"), ("dimension", "vibes"), ("severity", "fatal"), ("cadence", "hourly")],
)
def test_unknown_enum_values_are_rejected(tmp_path, field, value):
    path = write_rules(tmp_path, [a_rule(**{field: value})])
    with pytest.raises(DqRuleError, match=field):
        load_rules(path)


def test_unknown_check_type_is_rejected(tmp_path):
    path = write_rules(tmp_path, [a_rule(check={"type": "eyeball"})])
    with pytest.raises(DqRuleError, match="unknown check type"):
        load_rules(path)


def test_between_requires_two_bounds(tmp_path):
    path = write_rules(tmp_path, [a_rule(comparator="between", expected=25)])
    with pytest.raises(DqRuleError, match=r"\[low, high\]"):
        load_rules(path)


def test_scalar_comparator_rejects_a_pair(tmp_path):
    path = write_rules(tmp_path, [a_rule(comparator=">=", expected=[1, 2])])
    with pytest.raises(DqRuleError, match="expects a scalar"):
        load_rules(path)


def test_missing_field_names_the_rule_and_the_field(tmp_path):
    raw = a_rule()
    del raw["severity"]
    path = write_rules(tmp_path, [raw])
    with pytest.raises(DqRuleError, match="rule R1: missing required field 'severity'"):
        load_rules(path)


# ── the two Silver guardrails (gold-sql.md R1 / R6) ──────────────────────────


def test_a_bare_silver_table_in_a_sql_check_is_rejected(tmp_path):
    sql = textwrap.dedent(
        """
        SELECT COUNT(*) FROM silver_service_request
        WHERE open_date_local >= DATE '{window_start}'
        """
    )
    path = write_rules(tmp_path, [a_rule(check={"type": "sql", "sql": sql})])
    with pytest.raises(DqRuleError, match="R6"):
        load_rules(path)


def test_a_silver_read_without_a_partition_predicate_is_rejected(tmp_path):
    sql = "SELECT COUNT(*) FROM {{ silver }}.silver_service_request WHERE open_ts_utc IS NULL"
    path = write_rules(tmp_path, [a_rule(check={"type": "sql", "sql": sql})])
    with pytest.raises(DqRuleError, match="R1"):
        load_rules(path)


def test_a_gold_only_sql_check_needs_neither(tmp_path):
    sql = "SELECT COUNT(*) FROM fact_parking_ban WHERE matched_plow_event_id IS NULL"
    path = write_rules(tmp_path, [a_rule(check={"type": "sql", "sql": sql})])
    assert load_rules(path)[0].check["sql"] == sql


def test_a_sql_check_without_sql_is_rejected(tmp_path):
    path = write_rules(tmp_path, [a_rule(check={"type": "sql"})])
    with pytest.raises(DqRuleError, match="must carry a 'sql' field"):
        load_rules(path)


def test_every_shipped_silver_read_is_windowed_and_qualified():
    for r in load_rules():
        if r.check_type == "sql" and "silver_" in r.check["sql"]:
            assert "{{ silver }}." in r.check["sql"]
            assert "open_date_local" in r.check["sql"]


# ── comparators ──────────────────────────────────────────────────────────────


def test_lower_bound():
    r = rule(comparator=">=", expected=880)
    assert evaluate(r, 908)
    assert evaluate(r, 880)
    assert not evaluate(r, 879)


def test_upper_bound():
    r = rule(check={"type": "partition_freshness"}, comparator="<=", expected=2)
    assert evaluate(r, 2)
    assert not evaluate(r, 3)


def test_between():
    r = rule(comparator="between", expected=[10, 20])
    assert evaluate(r, 10) and evaluate(r, 20)
    assert not evaluate(r, 21)


def test_pct_change_passes_on_the_first_run():
    """The first run is the trend's point zero; failing it would start every
    fresh dq_audit_log red."""
    r = rule(comparator="pct_change", expected=10)
    assert evaluate(r, 13068, previous=None)


def test_pct_change_compares_against_the_previous_observation():
    r = rule(comparator="pct_change", expected=10)
    assert evaluate(r, 13068, previous=13000)
    assert not evaluate(r, 6000, previous=13068)


def test_pct_change_from_zero_only_passes_on_zero():
    r = rule(comparator="pct_change", expected=10)
    assert evaluate(r, 0, previous=0)
    assert not evaluate(r, 5, previous=0)


def test_pct_point_change_is_points_not_percent():
    """±5 points, not ±5%. On a 93.46% baseline the two differ by 4.7 points,
    which is the whole seven-column null baseline's margin."""
    r = rule(check={"type": "null_rate", "column": "priority_weight"},
             comparator="pct_point_change", expected=5)
    assert evaluate(r, 97.0, previous=93.46)
    assert not evaluate(r, 99.9, previous=93.46)


def test_a_missing_observation_fails_rather_than_passing_quietly():
    assert not evaluate(rule(), None)


# ── cadence ──────────────────────────────────────────────────────────────────


def test_a_daily_run_takes_only_daily_rules():
    assert rule(cadence="daily").runs_on("daily")
    assert not rule(cadence="weekly").runs_on("daily")
    assert not rule(cadence="manual").runs_on("daily")


def test_a_weekly_run_also_takes_the_daily_ones():
    """Otherwise the weekly sweep leaves a hole in the daily trend."""
    assert rule(cadence="daily").runs_on("weekly")
    assert rule(cadence="weekly").runs_on("weekly")
    assert not rule(cadence="manual").runs_on("weekly")


def test_a_manual_full_sweep_takes_everything():
    for cadence in ("daily", "weekly", "manual"):
        assert rule(cadence=cadence).runs_on("manual")
