"""Cross-layer reconciliation and Gold certification (third batch).

Every test here corresponds to one concrete way the layer could be wrong while
looking right — design §8.1. The pattern the first two batches established
holds: a rule that runs fine and answers a different question produces no
noise, so only a test that compares it against the thing it is supposed to
mirror will ever catch it.

🔴 One judgement this file cannot make: `bronze_manifest_sum` reads real
MinIO, and a mock only proves the call shape. W2 is a production-run criterion
and green tests here are not a substitute (design §8.1's closing note).
"""

from __future__ import annotations

import datetime as dt

import pytest
import yaml

from scripts.dq.certify import CERTIFIED, SUSPECT, UNKNOWN, Verdict, decide, insert_sql
from scripts.dq.rules import DqRuleError, load_rules
from scripts.dq.run_audit import _chunk_bounds

CROSS_LAYER_IDS = (
    "XLAYER-BRONZE-SILVER-CONSERVATION",
    "XLAYER-SILVER-GOLD-F1-ROLLUP",
    "XLAYER-SILVER-GOLD-F8-ROLLUP-WARD",
    "XLAYER-SILVER-GOLD-F8-ROLLUP-NEIGHBOURHOOD",
    "XLAYER-SILVER-GOLD-F8-UNKNOWN-LABEL",
    "XLAYER-GOLD-INTERNAL-F5-F6-F7",
)


def write_rules(tmp_path, rules: list[dict]):
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump({"version": 1, "rules": rules}))
    return path


def a_chunked_rule(**check_overrides) -> dict:
    check = {
        "type": "chunked_sql",
        "chunk_by": "calendar_year",
        "chunk_range": [2008, 2027],
        "combine": "additive",
        "sql": (
            "SELECT COUNT(*), COUNT(*) FROM {{ silver }}.silver_service_request "
            "WHERE open_date_local >= DATE '{chunk_start}' "
            "AND open_date_local < DATE '{chunk_end}'"
        ),
    }
    check.update(check_overrides)
    return {
        "id": "X1",
        "layer": "gold",
        "table": "fact_service_request_zone_event",
        "dimension": "cross_layer",
        "check": check,
        "comparator": ">=",
        "expected": 0,
        "severity": "warn",
        "cadence": "weekly",
        "note": "过滤条件：测试用",
    }


# ── chunking: R2 / R3 ────────────────────────────────────────────────────────


def test_combine_accepts_only_additive(tmp_path):
    """🔴 R3: averaging per-chunk percentages weights 2008 the same as 2019 and
    returns a number that looks entirely plausible. Rejected at load time so
    nobody has to notice it at read time."""
    path = write_rules(tmp_path, [a_chunked_rule(combine="ratio")])
    with pytest.raises(DqRuleError, match="not combinable across chunks|combine="):
        load_rules(path)


def test_additive_is_accepted(tmp_path):
    rules = load_rules(write_rules(tmp_path, [a_chunked_rule()]))
    assert rules[0].check["combine"] == "additive"


def test_chunk_by_accepts_only_calendar_year(tmp_path):
    path = write_rules(tmp_path, [a_chunked_rule(chunk_by="snow_season")])
    with pytest.raises(DqRuleError, match="chunk_by"):
        load_rules(path)


def test_chunked_sql_must_carry_both_boundary_placeholders(tmp_path):
    """Without them every chunk issues the identical whole-history query — a
    4,878-partition scan N times over, which reads as could-not-run."""
    path = write_rules(
        tmp_path,
        [a_chunked_rule(sql="SELECT COUNT(*), 1 FROM {{ silver }}.silver_service_request "
                            "WHERE open_date_local >= DATE '{chunk_start}'")],
    )
    with pytest.raises(DqRuleError, match=r"\{chunk_end\}"):
        load_rules(path)


def test_an_empty_chunk_range_is_rejected(tmp_path):
    path = write_rules(tmp_path, [a_chunked_rule(chunk_range=[2026, 2008])])
    with pytest.raises(DqRuleError, match="empty"):
        load_rules(path)


def test_the_executor_generates_the_dates_the_rule_never_writes():
    """R5: no hardcoded date string in committed SQL, and a rule file is
    committed SQL. The rule declares years; the boundaries come from here."""
    bounds = _chunk_bounds({"chunk_range": [2008, 2011]})
    assert bounds == [
        ("2008-01-01", "2009-01-01"),
        ("2009-01-01", "2010-01-01"),
        ("2010-01-01", "2011-01-01"),
    ]


def test_every_committed_cross_layer_rule_generates_its_own_dates():
    for rule in load_rules():
        if rule.check_type == "chunked_sql":
            assert "{chunk_start}" in rule.check["sql"]
            assert "{chunk_end}" in rule.check["sql"]


# ── cross_layer is a claim about the check, not a label ─────────────────────


def test_a_cross_layer_rule_may_not_be_an_ordinary_single_layer_check(tmp_path):
    rule = a_chunked_rule()
    rule["check"] = {"type": "row_count"}
    with pytest.raises(DqRuleError, match="cross_layer"):
        load_rules(write_rules(tmp_path, [rule]))


def test_bronze_manifest_sum_declares_both_sides(tmp_path):
    rule = a_chunked_rule()
    rule["check"] = {"type": "bronze_manifest_sum", "source_id": "SRC-WPG-311"}
    with pytest.raises(DqRuleError, match="dataset"):
        load_rules(write_rules(tmp_path, [rule]))


# ── the committed rules ─────────────────────────────────────────────────────


def cross_layer_rules():
    return {r.id: r for r in load_rules() if r.dimension == "cross_layer"}


def test_all_six_cross_layer_rules_are_present():
    assert set(cross_layer_rules()) == set(CROSS_LAYER_IDS)


def test_every_cross_layer_rule_restates_its_filter_in_the_note():
    """§0.2 — a reader has to be able to tell 「数据坏了」 from 「口径不同」
    without opening the SQL. A delta with no stated filter is unreadable."""
    for rule in cross_layer_rules().values():
        assert rule.note.strip(), f"{rule.id} has no note"
        assert "过滤条件" in rule.note or "口径" in rule.note, (
            f"{rule.id}'s note does not restate the filter it reconciles under"
        )


def test_the_f8_rollups_are_directional_and_never_equalities():
    """🔴 §2.3: F8's last step is `INNER JOIN dim_admin_label`, which silently
    drops a label the dimension has never seen. Silver's label occurrences can
    therefore only be >= F8's sum — writing that as `==` makes the rule red on
    the day the drop happens *and* on every ordinary day it does not."""
    for rule_id in (
        "XLAYER-SILVER-GOLD-F8-ROLLUP-WARD",
        "XLAYER-SILVER-GOLD-F8-ROLLUP-NEIGHBOURHOOD",
    ):
        rule = cross_layer_rules()[rule_id]
        assert rule.comparator == ">=", f"{rule_id} must be a direction, not an equality"


def test_the_two_f8_rollups_are_split_by_label_type_and_never_merged():
    """🔴 §2.3: one request carrying both a ward and a neighbourhood produces
    two F8 rows. Summed together, fan-out and duplication are the same number."""
    ward = cross_layer_rules()["XLAYER-SILVER-GOLD-F8-ROLLUP-WARD"]
    hood = cross_layer_rules()["XLAYER-SILVER-GOLD-F8-ROLLUP-NEIGHBOURHOOD"]
    assert "label_type = 'ward'" in ward.check["sql"]
    assert "label_type = 'neighbourhood'" in hood.check["sql"]
    assert "ward_raw" in ward.check["sql"] and "neighbourhood_raw" not in ward.check["sql"]
    assert "neighbourhood_raw" in hood.check["sql"] and "ward_raw" not in hood.check["sql"]


def _clauses(sql: str) -> str:
    return " ".join(sql.split())


def test_the_f1_rollup_carries_all_three_filters_the_dml_carries():
    """🔴 §2.2 / §8.1: the rule runs fine with two of the three and answers a
    different question, quietly. These are the exact three predicates
    sql/dml/fact_service_request_zone_event.sql applies."""
    from pathlib import Path

    dml = _clauses(
        Path("sql/dml/fact_service_request_zone_event.sql").read_text()
    )
    rule = _clauses(cross_layer_rules()["XLAYER-SILVER-GOLD-F1-ROLLUP"].check["sql"])
    for clause in (
        "WHERE z.has_plow_schedule",
        "WHERE t.winter_category IS NOT NULL",
        "open_date_local BETWEEN e.start_date AND e.end_date",
        "DATE '{chunk_end}' + INTERVAL '45' DAY",
        "WHERE c.is_effective",
    ):
        assert clause in dml, f"the DML no longer contains {clause!r} — re-derive the rule"
        assert clause in rule, f"the F1 roll-up rule dropped {clause!r}"


def test_the_f8_rollups_use_the_same_winter_filter_as_their_dml():
    from pathlib import Path

    dml = _clauses(Path("sql/dml/fact_winter_request_daily_by_label.sql").read_text())
    assert "WHERE t.winter_category IS NOT NULL" in dml
    for rule_id in (
        "XLAYER-SILVER-GOLD-F8-ROLLUP-WARD",
        "XLAYER-SILVER-GOLD-F8-ROLLUP-NEIGHBOURHOOD",
        "XLAYER-SILVER-GOLD-F8-UNKNOWN-LABEL",
    ):
        assert "WHERE t.winter_category IS NOT NULL" in _clauses(
            cross_layer_rules()[rule_id].check["sql"]
        )


def test_the_scoring_chain_relation_is_dynamic_not_a_constant():
    """🔴 §2.4: without COUNT(DISTINCT model_version) in the arithmetic, "the
    purge ate a version" and "only one version was ever trained" are the same
    row count. L3 already paid for that lesson once."""
    sql = cross_layer_rules()["XLAYER-GOLD-INTERNAL-F5-F6-F7"].check["sql"]
    assert sql.count("COUNT(DISTINCT model_version)") == 2
    for literal in ("1298", "2596", "748"):
        assert literal not in sql.replace(",", "")


# ── the three-state certification machine ───────────────────────────────────


def test_a_clean_run_certifies():
    status, note = decide(error_count=0, warn_count=0, checks_total=87, checks_could_not_run=0)
    assert status == CERTIFIED
    assert "87" in note


def test_warnings_do_not_block_certification():
    """§0.3 rule 1 inverted: warns must not block, or the only way to stay
    certified is to demote the warn to nothing. They stay counted and visible."""
    status, note = decide(error_count=0, warn_count=4, checks_total=87, checks_could_not_run=0)
    assert status == CERTIFIED
    assert "4 warn" in note


def test_an_error_finding_makes_it_suspect():
    status, _ = decide(error_count=1, warn_count=0, checks_total=87, checks_could_not_run=0)
    assert status == SUSPECT


def test_a_check_that_could_not_run_makes_it_unknown_not_suspect():
    """🔴 §0.3 rule 2 — the whole reason there is a third state. `suspect` says
    we looked and found a problem; `unknown` says we could not look. Collapsed,
    the day the audit itself broke produces a conclusion-shaped answer."""
    status, note = decide(error_count=0, warn_count=0, checks_total=87, checks_could_not_run=3)
    assert status == UNKNOWN
    assert status != SUSPECT
    assert "could not look" in note


def test_unknown_outranks_suspect_when_both_apply():
    """A run with a finding *and* a broken check has not established that the
    finding is the whole story — reporting `suspect` would overstate it."""
    status, _ = decide(error_count=2, warn_count=0, checks_total=87, checks_could_not_run=1)
    assert status == UNKNOWN


def test_a_run_that_produced_no_observations_is_unknown_not_certified():
    """Nothing ran is the case §0.3 says must never default to silence."""
    status, _ = decide(error_count=0, warn_count=0, checks_total=0, checks_could_not_run=0)
    assert status == UNKNOWN


def test_the_insert_lists_its_columns_and_quotes_the_note():
    sql = insert_sql(
        "uoip_meta",
        Verdict("dq-1", "daily", SUSPECT, 1, 0, 87, 0, "it's wrong"),
        dt.datetime(2026, 8, 22, 9, 0, 0),
    )
    assert sql.startswith("INSERT INTO uoip_meta.gold_certification (run_id, cadence")
    assert "'it''s wrong'" in sql
    assert "TIMESTAMP '2026-08-22 09:00:00.000000'" in sql


# ── isolation, re-verified because a table was added (V7) ───────────────────


def test_the_certification_table_is_invisible_to_the_three_gold_iterations():
    """Second batch V5, re-run because §1 added a table to `uoip_meta`. Each of
    the three "every Gold table" iterations must still see exactly 17."""
    from scripts.ddl.ddl_parser import load_all_ddl
    from scripts.gold.build_gold import TABLES

    assert "gold_certification" not in {t.name for t in TABLES}
    assert "gold_certification" not in load_all_ddl()
    assert len(TABLES) == 17


def test_the_certification_ddl_lives_in_sql_meta_not_sql_ddl():
    from pathlib import Path

    assert Path("sql/meta/gold_certification.sql").exists()
    assert not Path("sql/ddl/gold_certification.sql").exists()
