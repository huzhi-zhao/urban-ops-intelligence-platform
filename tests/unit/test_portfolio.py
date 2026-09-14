"""Portfolio evidence packaging: complete catalogue, honest states, English-only copy."""

import json
import re
from pathlib import Path

import pytest

from scripts.presentation.portfolio import build_catalogue, build_portfolio_data

CJK = re.compile(r"[㐀-鿿]")


def test_catalogue_covers_every_query_without_inventing_data(tmp_path):
    items = build_catalogue(tmp_path / "missing")
    assert len(items) == 25
    # The core 19 is a number the launch record states; the two lookup queries
    # (ADR 0013) answer a reader's question rather than carrying a finding, so
    # they get their own role instead of inflating it.
    assert sum(item["role"] == "core" for item in items) == 19
    assert sum(item["role"] == "explanatory" for item in items) == 4
    assert sum(item["role"] == "lookup" for item in items) == 2
    assert all(item["state"] == "missing" and "rows" not in item for item in items)
    assert not (tmp_path / "missing").exists()


def test_public_copy_is_english_and_complete(tmp_path):
    for item in build_catalogue(tmp_path):
        copy = item["copy"]
        assert copy["title"] and copy["caption"] and copy["must_not_say"], item["id"]
        assert not CJK.search(json.dumps(copy, ensure_ascii=False)), item["id"]


def _export(directory: Path, fig_id: str, columns, rows, certification=None):
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "fig_id": fig_id,
        "columns": columns,
        "rows": rows,
        "frozen_at": "2026-09-08T00:00:00+00:00",
        "certification": certification or {"status": "certified", "run_id": "dq-x"},
    }
    (directory / f"{fig_id}.json").write_text(json.dumps(payload))


def test_sample_certification_is_never_reported_as_frozen(tmp_path):
    _export(tmp_path, "FIG-BO2-01", ["plow_zone", "mean_shift"], [["S", 1.26]],
            {"status": "SAMPLE"})
    rank = next(i for i in build_catalogue(tmp_path) if i["id"] == "FIG-BO2-01")
    assert rank["state"] == "sample"
    assert rank["rows"] == [["S", 1.26]]


def test_mismatched_export_is_rejected(tmp_path):
    (tmp_path / "FIG-BO2-01.json").write_text(json.dumps({"fig_id": "FIG-BO2-02"}))
    with pytest.raises(ValueError, match="Mismatched"):
        build_catalogue(tmp_path)


def test_ragged_rows_are_rejected(tmp_path):
    _export(tmp_path, "FIG-BO2-01", ["a", "b"], [["only-one"]])
    with pytest.raises(ValueError, match="shape"):
        build_catalogue(tmp_path)


def test_unscheduled_zones_carry_no_value_on_the_map(tmp_path):
    square = "MULTIPOLYGON (((-97.1 49.8, -97.0 49.8, -97.0 49.9, -97.1 49.9, -97.1 49.8)))"
    other = "MULTIPOLYGON (((-97.3 49.8, -97.2 49.8, -97.2 49.9, -97.3 49.9, -97.3 49.8)))"
    _export(tmp_path, "FIG-BO4-00",
            ["plow_zone", "has_plow_schedule", "address_count", "geometry_repaired",
             "area_delta_pct", "geometry_wkt"],
            [["S", True, 10, False, 0.0, square], ["X", False, 5, False, 0.0, other]])
    # Even if a rank export mentioned X, an unscheduled zone must not pick it up.
    _export(tmp_path, "FIG-BO2-01", ["plow_zone", "mean_shift"], [["S", 1.26], ["X", 2.0]])
    build_portfolio_data(tmp_path, tmp_path / "out")
    zones = {z["zone"]: z for z in json.loads((tmp_path / "out/zones.json").read_text())["zones"]}
    assert zones["S"]["mean_shift"] == 1.26
    assert zones["X"]["mean_shift"] is None
    assert zones["X"]["d"].startswith("M")
    geometry = next(
        i for i in json.loads((tmp_path / "out/evidence.json").read_text())
        if i["id"] == "FIG-BO4-00"
    )
    assert "geometry_wkt" not in geometry["columns"]


def test_public_interface_source_is_english():
    root = Path("dashboard")
    sources = [root / "index.html", *sorted((root / "src").rglob("*.js*"))]
    offenders = {str(p): CJK.findall(p.read_text("utf-8")) for p in sources
                 if CJK.search(p.read_text("utf-8"))}
    assert not offenders
