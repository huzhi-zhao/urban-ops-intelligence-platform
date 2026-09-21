"""`sql/presentation/` is checked for the things sqlfluff cannot see.

sqlfluff proves a figure file is parseable Trino. It cannot prove the file has
a caption, that the caption's figure exists in the ledger, or that the bare
table names resolve in the schema the header declares — and that last one does
not fail loudly at runtime, it resolves somewhere else (R6's failure mode).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scripts.eda.run import (
    KNOWN_CARRIERS,
    PRESENTATION_DIR,
    REQUIRED_KEYS,
    FigureHeaderError,
    load_figures,
    parse_header,
)

REPO = Path(__file__).resolve().parents[2]
LEDGER = REPO / "docs" / "dev" / "requirements" / "bo-conclusions-and-figures.md"

# Only real table names, never CTE aliases — every table in this repo is named
# by its layer prefix, which is what makes the filter safe.
TABLE_PREFIXES = ("dim_", "fact_", "silver_")
META_TABLES = {"dq_audit_log", "gold_certification"}
TABLE_REFERENCE = re.compile(r"\b(?:FROM|JOIN)\s+([a-z_][a-z0-9_]*)", re.IGNORECASE)


def _tables_of(layer: str) -> set[str]:
    if layer == "meta":
        return {path.stem for path in (REPO / "sql" / "meta").glob("*.sql")}
    ddl = REPO / "sql" / "ddl"
    if layer == "silver":
        return {path.stem for path in ddl.glob("silver_*.sql")}
    return {path.stem for path in ddl.glob("*.sql") if not path.stem.startswith("silver_")}


FIGURES = load_figures()


def test_the_directory_is_not_empty() -> None:
    assert FIGURES, "sql/presentation/ has no fig_*.sql files"


@pytest.mark.parametrize("figure", FIGURES, ids=lambda figure: figure.path.name)
def test_every_figure_declares_the_full_header(figure) -> None:  # noqa: ANN001
    for key in REQUIRED_KEYS:
        assert figure.header.get(key), f"{figure.path.name} lacks {key}"
    assert figure.carrier in KNOWN_CARRIERS


@pytest.mark.parametrize("figure", FIGURES, ids=lambda figure: figure.path.name)
def test_the_filename_matches_the_fig_id(figure) -> None:  # noqa: ANN001
    # FIG-BO2-01 → fig_bo2_01_<slug>.sql. A mismatch makes the ledger's图 ID
    # unfindable in the repo, which is the orphan case design A2 forbids.
    expected = figure.fig_id.lower().replace("-", "_") + "_"
    assert figure.path.name.startswith(expected), f"{figure.path.name} vs {figure.fig_id}"


@pytest.mark.parametrize("figure", FIGURES, ids=lambda figure: figure.path.name)
def test_every_figure_is_named_in_the_ledger(figure) -> None:  # noqa: ANN001
    assert figure.fig_id in LEDGER.read_text(encoding="utf-8"), (
        f"{figure.fig_id} has SQL but no ledger row — an orphan figure (design A2)"
    )


@pytest.mark.parametrize("figure", FIGURES, ids=lambda figure: figure.path.name)
def test_every_table_resolves_in_the_declared_schema(figure) -> None:  # noqa: ANN001
    known = _tables_of(figure.layer)
    for name in TABLE_REFERENCE.findall(figure.sql):
        lowered = name.lower()
        if not (lowered.startswith(TABLE_PREFIXES) or lowered in META_TABLES):
            continue  # a CTE alias
        assert lowered in known, (
            f"{figure.path.name} reads {lowered}, which is not in schema "
            f"'{figure.layer}' — a bare name in the wrong schema does not fail "
            f"to resolve, it resolves elsewhere (gold-sql.md R6)"
        )


@pytest.mark.parametrize("figure", FIGURES, ids=lambda figure: figure.path.name)
def test_no_carrier_template_syntax_reaches_the_repo(figure) -> None:  # noqa: ANN001
    # design §3.3: Grafana's $__timeFilter and Superset's Jinja stay on the
    # carrier side. sqlfluff would report one unparsable section and then stop
    # enforcing every other rule on the file.
    text = figure.path.read_text(encoding="utf-8")
    assert "$__" not in text, f"{figure.path.name} carries Grafana macro syntax"
    assert "{{" not in figure.sql, f"{figure.path.name} carries jinja in its body"


@pytest.mark.parametrize("figure", FIGURES, ids=lambda figure: figure.path.name)
def test_one_statement_per_figure(figure) -> None:  # noqa: ANN001
    assert ";" not in figure.sql, f"{figure.path.name} holds more than one statement"


@pytest.mark.parametrize("figure", FIGURES, ids=lambda figure: figure.path.name)
def test_no_select_star(figure) -> None:  # noqa: ANN001
    assert not re.search(r"SELECT\s+\*", figure.sql, re.IGNORECASE)


def test_a_missing_header_key_is_refused() -> None:
    with pytest.raises(FigureHeaderError):
        parse_header("-- fig_id: FIG-X-01\nSELECT 1", Path("fig_x_01_a.sql"))


def test_a_caption_may_span_continuation_lines() -> None:
    header, sql = parse_header(
        "-- fig_id: FIG-X-01\n-- bo: BO-X\n-- carrier: echarts\n-- schema: gold\n"
        "-- criterion: c\n-- caption: first\n--   second\n-- must_not_say: n\nSELECT 1",
        Path("fig_x_01_a.sql"),
    )
    assert header["caption"] == "first second"
    assert sql == "SELECT 1"


def test_presentation_dir_points_at_the_repo() -> None:
    assert PRESENTATION_DIR == REPO / "sql" / "presentation"


def test_no_ledger_row_claims_sql_that_is_not_in_the_repo() -> None:
    """The other direction of design A2's orphan check.

    🔴 `test_every_figure_is_named_in_the_ledger` only walks SQL → ledger, so a
    ledger row that says 「SQL 已进仓」 while no such file exists passes every
    gate. That is not hypothetical: four rows (FIG-BO3-01/02, FIG-BO1-01/02)
    carried the claim through the whole of stage 5a. A status is a claim about
    the repository, and an unverified claim in the one document people quote
    from is worse than no status at all.
    """
    shipped = {figure.fig_id for figure in load_figures()}
    claimed = {
        match.group(0)
        for line in LEDGER.read_text(encoding="utf-8").splitlines()
        if "SQL 已进仓" in line
        for match in [re.search(r"FIG-[A-Z0-9-]+", line)]
        if match
    }
    assert claimed <= shipped, f"ledger claims SQL that does not exist: {sorted(claimed - shipped)}"
