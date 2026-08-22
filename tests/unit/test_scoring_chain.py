"""Offline tests for the L3-b scoring chain (F6 / F7). No Trino, no MinIO.

These files cannot be unit-tested for their *arithmetic* without a query
engine, so what is pinned here is everything a reader of the SQL cannot
verify by looking: that the shape rules that apply to sql/dml/ apply here too,
that the design's口径 decisions actually made it into the text, and that the
serving-version choice is never made by guessing.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.ddl.ddl_parser import DDL_DIR
from scripts.gold import build_gold
from scripts.gold.build_gold import BY_NAME, INTELLIGENCE_DIR, Builder
from scripts.gold.forecast_artefacts import Artefact

SCORING = ("fact_winter_event_zone_load", "fact_recommendation")


def _files() -> list[Path]:
    return sorted(INTELLIGENCE_DIR.glob("*.sql"))


def _body(path: Path) -> str:
    return re.sub(r"--.*", "", path.read_text(encoding="utf-8"))


# ------------------------------------------------------- the sql/dml rules, here too


def test_every_scoring_table_has_a_select_file():
    for name in SCORING:
        assert (INTELLIGENCE_DIR / f"{name}.sql").exists(), name


def test_no_select_star():
    for path in _files():
        assert not re.search(r"SELECT\s+\*", _body(path), re.I), path.name


def test_files_are_bare_selects():
    """The INSERT and its column list come from the DDL, exactly as in sql/dml/."""
    for path in _files():
        body = _body(path).upper()
        assert "CREATE TABLE" not in body, path.name
        assert "INSERT INTO" not in body, path.name


def test_no_catalog_or_schema_qualification():
    for path in _files():
        body = _body(path)
        assert "hive." not in body, path.name
        assert "uoip_gold" not in body, path.name


def test_the_scoring_chain_reads_no_silver():
    """Design §6: F6/F7 read Gold only, which is why gold-sql.md R1's date
    predicate is satisfied without one appearing in the file."""
    for path in _files():
        assert "silver" not in _body(path).lower(), path.name


def test_lineage_columns_are_emitted():
    for path in _files():
        body = _body(path)
        for column in ("etl_run_id", "built_at", "source_max_ingest_date"):
            assert column in body, f"{path.name} does not emit {column}"


def test_source_max_ingest_date_is_not_the_run_date():
    """ADR 0010 D7: this column answers "how old is the input". CURRENT_DATE
    would answer "when did we run", which built_at already does."""
    for path in _files():
        assert "CURRENT_DATE" not in _body(path), path.name


# --------------------------------------------------------------- design decisions


def test_normalisation_spans_the_whole_panel_not_each_event():
    """Design §6.1 decision 1. `OVER ()` is the whole panel; an `OVER (PARTITION
    BY snowfall_event_id)` here would put a 1.0 and a 0.0 in every event and
    destroy the cross-event comparability load_level depends on."""
    for path in _files():
        for window in re.findall(r"(MIN|MAX)\(demand_density\)\s*OVER\s*\(([^)]*)\)", _body(path)):
            assert window[1].strip() == "", f"{path.name} normalises per {window[1]!r}"


def test_weights_are_not_renormalised_when_rank_is_missing():
    """BO-6 forbids silent renormalisation: the demand_weather_only profile sums
    to 0.70 and its ceiling is 70, not 100."""
    body = _body(INTELLIGENCE_DIR / "fact_winter_event_zone_load.sql")
    assert "0.40" in body and "0.30" in body
    assert "70.0" in body, "the 0.70 profile's ceiling is missing"
    # 1/0.7 and friends — any attempt to scale the partial profile back to 1.0.
    assert not re.search(r"/\s*0\.7", body)


def test_partial_rows_still_get_a_score():
    """O1's ruling (launch §4.2), as a property of the SQL rather than of a gate
    that would only fail after a production build."""
    body = _body(INTELLIGENCE_DIR / "fact_winter_event_zone_load.sql")
    assert "COALESCE(rank_factor, 0.0)" in body
    gate_sql = " ".join(g[1] for g in BY_NAME["fact_winter_event_zone_load"].extra_gates)
    assert "score_status = 'partial_no_rank' AND load_score IS NULL" in gate_sql


def test_load_level_thresholds_are_fixed_fractions_not_quantiles():
    """Data quantiles would make one new event shift historical events' levels,
    and a rebuild's output would stop being a function of its inputs alone."""
    body = _body(INTELLIGENCE_DIR / "fact_winter_event_zone_load.sql")
    for fraction in ("0.25", "0.50", "0.75"):
        assert f"{fraction} * score_ceiling" in body
    assert "APPROX_PERCENTILE" not in body.upper()
    assert "NTILE" not in body.upper()


def test_ranks_use_row_number_so_they_are_permutations():
    """b12 asks for a permutation of 1..22; RANK leaves holes on a tie."""
    body = _body(INTELLIGENCE_DIR / "fact_recommendation.sql")
    assert body.count("ROW_NUMBER() OVER") == 2
    assert not re.search(r"\bRANK\(\)\s*OVER", body)
    # A deterministic tiebreak, or two builds could order tied zones differently.
    assert body.count("plow_zone ASC") == 2


def test_the_baseline_is_read_not_recomputed():
    """ADR 0010 D5: the baseline is stored on F5's row, never recomputed live —
    otherwise a past backtest's baseline would move when the data does."""
    body = _body(INTELLIGENCE_DIR / "fact_recommendation.sql")
    assert "baseline_count" in body
    assert "AVG(" not in body.upper()


def test_attribution_text_substitutes_every_template_placeholder():
    """A placeholder the SQL forgets ships as literal `{shift_number}` to a
    reader. The gate catches it after a build; this catches it now."""
    body = _body(INTELLIGENCE_DIR / "fact_recommendation.sql")
    seeds = (build_gold.SEED_DIR / "recommendation_rules.csv").read_text(encoding="utf-8")
    for placeholder in set(re.findall(r"\{(\w+)\}", seeds)):
        assert f"'{{{placeholder}}}'" in body, f"{placeholder} is never substituted"


# ----------------------------------------------------------- serving version choice


def _builder(versions: list[str], requested: str | None = None) -> Builder:
    builder = Builder.__new__(Builder)
    builder.args = SimpleNamespace(dry_run=False, forecast_version=requested)
    builder._artefacts = [
        Artefact(model_version=v, key=f"k/{v}", source_date=None, rows=[]) for v in versions
    ]
    return builder


def test_a_single_version_needs_no_flag():
    assert _builder(["m1-poisson-20260822-df31d954"]).serving_version() == (
        "m1-poisson-20260822-df31d954"
    )


def test_two_versions_without_a_flag_is_refused_not_guessed():
    """🔴 The failure this prevents: `m1-poisson-nomonth-...` sorts *after*
    `m1-poisson-...`, so any "take the last one" rule would put a deliberately
    degraded test model in front of Superset."""
    with pytest.raises(SystemExit, match="more than one model_version"):
        _builder(["m1-poisson-20260822-df31d954", "m1-poisson-nomonth-20260822-30af82f4"]).serving_version()


def test_a_named_version_that_does_not_exist_is_refused():
    with pytest.raises(SystemExit, match="not in"):
        _builder(["m1-poisson-a"], requested="m1-poisson-b").serving_version()


def test_a_named_version_wins_even_with_several_present():
    builder = _builder(["m1-poisson-a", "m1-poisson-b"], requested="m1-poisson-a")
    assert builder.serving_version() == "m1-poisson-a"


# ------------------------------------------------------------------------- wiring


def test_f7_is_chunked_per_model_version_and_f6_is_not():
    """F6's grain has no model_version and is capped at the 1,298-cell panel;
    F7's PK contains it, because BO-8 needs an old backtest to stay queryable."""
    assert BY_NAME["fact_recommendation"].per_model_version
    assert not BY_NAME["fact_winter_event_zone_load"].per_model_version


def test_scoring_tables_build_after_what_they_read():
    order = [t.name for t in build_gold.TABLES]
    assert order.index("fact_request_forecast") < order.index("fact_winter_event_zone_load")
    assert order.index("fact_winter_event_zone_load") < order.index("fact_recommendation")


def test_f7_row_count_gate_scales_with_the_version_count():
    builder = _builder(["a", "b"])
    gates = builder._dynamic_extra_gates(BY_NAME["fact_recommendation"])
    assert {g[2] for g in gates} == {2, 2 * 374}


def test_every_scoring_table_declares_its_ddl_gates():
    for name in SCORING:
        assert (DDL_DIR / f"{name}.sql").exists()
        assert BY_NAME[name].extra_gates, name


def test_every_extra_gate_survives_the_silver_substitution():
    """check_gates runs every gate SQL through str.format(silver=...), so a
    literal brace in a gate (`LIKE '%{%'`) raises *after* the table is written.
    L3-b's first production run died exactly there."""
    for table in build_gold.TABLES:
        for _description, sql, *_rest in table.extra_gates:
            sql.format(silver="uoip_silver")
