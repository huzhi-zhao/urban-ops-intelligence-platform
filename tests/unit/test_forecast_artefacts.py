"""Offline tests for F5's artefact loader. No MinIO, no Trino, no pandas.

The loader is the executable half of design §5's resolution to the conflict
between R4 ("rebuild the table whole") and F5's contract ("old versions are
never overwritten"). What these tests defend is that resolution, not the CSV
parsing: an artefact that is silently dropped, or a version that is silently
merged into another, would leave a table that looks entirely plausible.
"""

from __future__ import annotations

import datetime as dt

import pytest

from scripts.ddl.ddl_parser import DDL_DIR, parse_ddl_file
from scripts.gold import build_gold
from scripts.gold.build_gold import (
    BY_NAME,
    LINEAGE_COLUMNS,
    PREDICTION_CELLS,
    artefact_select,
)
from scripts.gold.forecast_artefacts import (
    ARTEFACT_ROOT,
    PREDICTION_COLUMNS,
    Artefact,
    ArtefactError,
    artefact_root,
    load_artefacts,
    parse_predictions,
)

HEADER = ",".join(PREDICTION_COLUMNS)
V1 = "m1-poisson-20260822-df31d954"
V2 = "m1-poisson-20260901-aabbccdd"


def _csv(version: str, rows: int = 2) -> bytes:
    body = [HEADER]
    for i in range(rows):
        body.append(f"EVT-{i},A,{version},12.5,9.0,11")
    return ("\n".join(body) + "\n").encode()


class FakeS3:
    """Just enough of the boto3 client surface for the loader."""

    def __init__(self, objects: dict[str, bytes], modified: dt.datetime | None = None) -> None:
        self.objects = objects
        self.modified = modified or dt.datetime(2026, 8, 22, 15, 0, tzinfo=dt.UTC)

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        outer = self

        class _P:
            def paginate(self, Bucket, Prefix):  # noqa: N803 - boto3's own casing
                contents = [
                    {"Key": k, "LastModified": outer.modified}
                    for k in sorted(outer.objects)
                    if k.startswith(Prefix)
                ]
                yield {"Contents": contents}

        return _P()

    def get_object(self, Bucket, Key):  # noqa: N803 - boto3's own casing
        return {"Body": _Body(self.objects[Key])}


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


# ------------------------------------------------------------------ discovery


def test_every_version_directory_becomes_one_artefact():
    client = FakeS3(
        {
            f"{ARTEFACT_ROOT}/{V1}/predictions.csv": _csv(V1),
            f"{ARTEFACT_ROOT}/{V1}/metrics.json": b"{}",
            f"{ARTEFACT_ROOT}/{V2}/predictions.csv": _csv(V2),
            f"{ARTEFACT_ROOT}/{V2}/metrics.json": b"{}",
        }
    )
    found = load_artefacts("uoip", client=client)
    assert [a.model_version for a in found] == [V1, V2]
    # metrics.json is not a prediction panel and must not become an INSERT.
    assert all(a.key.endswith("predictions.csv") for a in found)


def test_artefacts_are_ordered_so_two_builds_emit_identical_sql():
    """Gate a6 rests on this: same artefacts in, same SQL out."""
    keys = {
        f"{ARTEFACT_ROOT}/{V2}/predictions.csv": _csv(V2),
        f"{ARTEFACT_ROOT}/{V1}/predictions.csv": _csv(V1),
    }
    assert [a.model_version for a in load_artefacts("uoip", client=FakeS3(keys))] == [V1, V2]


def test_an_empty_root_names_the_command_that_fills_it():
    with pytest.raises(ArtefactError, match="train_m1"):
        load_artefacts("uoip", client=FakeS3({}))


def test_a_smoke_prefix_reads_smoke_artefacts():
    assert artefact_root("smoke-20260822") == "smoke-20260822/gold/_forecast_runs"
    assert artefact_root("") == ARTEFACT_ROOT


def test_the_purged_prefix_is_the_table_not_the_artefacts():
    """Design §5 in one assertion: R4's purge must not reach the record.

    If these two prefixes ever nest, a rebuild deletes the very versions it is
    supposed to preserve — and the row-count gate, which only knows about the
    current version, stays green while it happens.
    """
    table_prefix = "gold/fact_request_forecast"
    assert not artefact_root("").startswith(table_prefix + "/")
    assert not table_prefix.startswith(artefact_root("") + "/")


# ---------------------------------------------------------------------- parse


def test_a_row_claiming_another_version_is_refused():
    body = f"{HEADER}\nEVT-0,A,{V2},1.0,1.0,1\n".encode()
    with pytest.raises(ArtefactError, match="one of the two is wrong"):
        parse_predictions(body, V1, "k")


def test_reordered_columns_are_refused_rather_than_matched_by_name():
    swapped = list(PREDICTION_COLUMNS)
    swapped[0], swapped[1] = swapped[1], swapped[0]
    body = (",".join(swapped) + f"\nA,EVT-0,{V1},1.0,1.0,1\n").encode()
    with pytest.raises(ArtefactError, match="order matters"):
        parse_predictions(body, V1, "k")


def test_a_header_only_artefact_is_refused():
    with pytest.raises(ArtefactError, match="no rows"):
        parse_predictions((HEADER + "\n").encode(), V1, "k")


def test_a_short_row_is_refused():
    body = f"{HEADER}\nEVT-0,A,{V1},1.0\n".encode()
    with pytest.raises(ArtefactError, match="cells"):
        parse_predictions(body, V1, "k")


# --------------------------------------------------------------------- select


def _columns():
    return parse_ddl_file(DDL_DIR / "fact_request_forecast.sql").data_columns


def _artefact(rows: list[list[str]]) -> Artefact:
    return Artefact(
        model_version=V1,
        key=f"{ARTEFACT_ROOT}/{V1}/predictions.csv",
        source_date=dt.date(2026, 8, 22),
        rows=rows,
    )


def test_select_renders_types_and_appends_lineage():
    sql = artefact_select(
        _artefact([["EVT-1", "A", V1, "12.5", "9.0", "11"]]),
        _columns(),
        "l3-run",
        "2026-08-22 15:00:00.000000",
    )
    assert "'EVT-1', 'A'" in sql
    assert "12.5, 9.0, 11" in sql
    assert "'l3-run'" in sql
    # The artefact's own age, not the build date — ADR 0010 D7.
    assert "DATE '2026-08-22'" in sql
    assert sql.rstrip().endswith(
        "AS t(" + ", ".join([*PREDICTION_COLUMNS, *LINEAGE_COLUMNS]) + ")"
    )


def test_an_empty_baseline_cell_becomes_null_not_zero():
    """`baseline_count` is null for a cell with no prior history. 0 would be a
    real forecast of zero requests and would quietly beat the model on MAE."""
    sql = artefact_select(
        _artefact([["EVT-1", "A", V1, "12.5", "", "11"]]),
        _columns(),
        "r",
        "2026-08-22 15:00:00.000000",
    )
    assert "12.5, NULL, 11" in sql


def test_select_refuses_a_ddl_that_no_longer_matches_the_artefact():
    columns = [c for c in _columns() if c.name != "actual_count"]
    with pytest.raises(ValueError, match="frozen"):
        artefact_select(_artefact([["EVT-1", "A", V1, "1.0", "1.0"]]), columns, "r", "t")


# ---------------------------------------------------------------------- gates


def test_f5_is_its_own_stage_so_only_facts_cannot_touch_it():
    """A Silver repair rebuilds `--only facts`. F5's inputs are training
    artefacts and have nothing to do with a Silver window."""
    assert BY_NAME["fact_request_forecast"].stage == "scoring"
    assert BY_NAME["fact_request_forecast"].from_artefacts


def test_per_version_row_count_gate_uses_the_configured_panel_size():
    """1,298 lives in config/models/m1.yaml, which the trainer also gates on."""
    assert PREDICTION_CELLS == 1298
    gate_sql = " ".join(g[1] for g in BY_NAME["fact_request_forecast"].extra_gates)
    assert f"n <> {PREDICTION_CELLS}" in gate_sql


def test_f5_declares_no_dml_file():
    """Its rows come from object storage; a stray sql/dml file would be dead
    code that looks authoritative."""
    assert not (DDL_DIR.parent / "dml" / "fact_request_forecast.sql").exists()


def test_version_count_gate_is_derived_from_the_artefacts_found():
    """The claim "a rebuild loses no version", executable.

    Neither number can be a constant: both grow with every retrain. Without
    this gate, a purge that ate two versions and a build that only ever had
    one produce the same table.
    """
    from types import SimpleNamespace

    builder = build_gold.Builder.__new__(build_gold.Builder)
    builder.args = SimpleNamespace(dry_run=False)
    builder._artefacts = [_artefact([]), _artefact([])]
    gates = builder._dynamic_extra_gates(BY_NAME["fact_request_forecast"])
    expected = {g[2] for g in gates}
    assert expected == {2, 2 * PREDICTION_CELLS}
    assert all(len(g) == 3 for g in gates), "these detect a broken build — equalities, not bounds"


def test_a_table_built_from_silver_gets_no_dynamic_gates():
    from types import SimpleNamespace

    builder = build_gold.Builder.__new__(build_gold.Builder)
    builder.args = SimpleNamespace(dry_run=False)
    assert builder._dynamic_extra_gates(BY_NAME["fact_plow_shift"]) == ()
