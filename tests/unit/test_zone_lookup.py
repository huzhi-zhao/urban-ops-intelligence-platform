"""The zone-lookup fold is checked for the things the rendered page hides.

Every number is computed here at build time, so a wrong fold is invisible in
the browser — it just reads as a different, plausible answer. These tests pin
the three that are easy to get wrong and impossible to spot afterwards: the
ratio is taken after summing, the ranking covers only the zones that carry a
schedule, and a zone with no schedule gets an answer rather than an empty card.

The last two tests read `dashboard/src/zone.jsx` as text. That is deliberate:
the constraints they check (no arithmetic in the browser, no borrowing the
City tool's name) are properties of the page's source, and there is no other
gate on this repo that would notice either one changing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from scripts.presentation.portfolio import build_lookup
from scripts.presentation.zone_lookup import (
    PROFILE_FIG,
    TRANSITION_FIG,
    LookupError,
    _worst_certification,
    build_context,
    build_records,
    load_payload,
    summarise_transitions,
)

ZONE_PAGE = Path(__file__).resolve().parents[2] / "dashboard" / "src" / "zone.jsx"

PROFILE_COLUMNS = [
    "plow_zone", "has_plow_schedule", "address_count", "operations", "mean_shift",
    "min_shift", "max_shift", "mean_early", "mean_late", "shift_1_count", "shift_2_count",
    "shift_3_count", "shift_4_count", "shift_5_count", "recent_operations",
    "recent_top2_count", "last_shift",
]


def _profile_row(zone: str, mean: float | None, *, scheduled: bool = True) -> list:
    if mean is None:
        return [zone, scheduled, 2590, 0, None, None, None, None, None, 0, 0, 0, 0, 0, 0, 0, None]
    return [zone, scheduled, 9000, 19, mean, 1, 3, mean, mean, 5, 9, 5, 0, 0, 11, 7, 2]


def _profile(rows: list[list], *, frozen_at: str = "2026-09-14T00:00:00+00:00") -> dict:
    return {
        "fig_id": PROFILE_FIG,
        "columns": PROFILE_COLUMNS,
        "rows": rows,
        "header": {},
        "source_sql": "sql/presentation/fig_bo2_06_zone_rank_profile.sql",
        "frozen_at": frozen_at,
        "certification": {"status": "certified", "run_id": "r1"},
    }


def _transitions(rows: list[list], *, frozen_at: str = "2026-09-14T00:00:00+00:00") -> dict:
    return {
        "fig_id": TRANSITION_FIG,
        "columns": ["plow_zone", "prev_shift", "next_shift", "transitions"],
        "rows": rows,
        "header": {},
        "source_sql": "sql/presentation/fig_bo2_07_rank_persistence.sql",
        "frozen_at": frozen_at,
        "certification": {"status": "certified", "run_id": "r1"},
    }


def test_exact_and_within_one_are_counted_from_the_transition_grid() -> None:
    summary = summarise_transitions(
        [
            {"plow_zone": "A", "prev_shift": 1, "next_shift": 1, "transitions": 6},
            {"plow_zone": "A", "prev_shift": 1, "next_shift": 2, "transitions": 3},
            {"plow_zone": "A", "prev_shift": 1, "next_shift": 4, "transitions": 1},
        ]
    )
    assert summary["per_zone"]["A"] == {"transitions": 10, "exact": 6, "within1": 9}
    assert summary["city"]["transitions"] == 10


def test_the_city_rate_is_a_ratio_of_sums_not_a_mean_of_zone_rates() -> None:
    """🔴 The defect this pins: averaging per-zone percentages weights a zone
    with 1 transition the same as one with 99 (gold-sql.md R3). Here zone A is
    1/1 correct and zone B is 0/99, so the two readings are 50.0% and 1.0%."""
    context = build_context(
        _profile([_profile_row("A", 1.0), _profile_row("B", 2.0)]),
        _transitions(
            [
                ["A", 1, 1, 1],
                ["B", 1, 3, 99],
            ]
        ),
    )
    assert context["city"]["exact_pct"] == 1.0


def test_the_ranking_covers_only_the_zones_that_carry_a_schedule() -> None:
    records = build_records(
        [
            {**dict(zip(PROFILE_COLUMNS, _profile_row("A", 1.2), strict=True))},
            {**dict(zip(PROFILE_COLUMNS, _profile_row("C", 3.4), strict=True))},
            {**dict(zip(PROFILE_COLUMNS, _profile_row("X", None, scheduled=False), strict=True))},
        ],
        {"per_zone": {}},
    )
    by_zone = {r["plow_zone"]: r for r in records}
    assert (by_zone["A"]["position"], by_zone["A"]["of_zones"]) == (1, 2)
    assert (by_zone["C"]["position"], by_zone["C"]["of_zones"]) == (2, 2)
    # Never "3rd of 3": X was never in the ranking, so a position would be a
    # claim the panel does not make.
    assert by_zone["X"]["position"] is None


def test_a_zone_with_no_schedule_is_told_so_by_name() -> None:
    context = build_context(
        _profile([_profile_row("A", 1.2), _profile_row("X", None, scheduled=False)]),
        _transitions([["A", 1, 1, 18]]),
    )
    assert context["city"]["no_schedule_zones"] == ["X"]
    # The page owes this reader a sentence, not an empty card.
    assert "no residential plowing schedule" in ZONE_PAGE.read_text(encoding="utf-8")


def test_the_address_share_of_the_unscheduled_zones_keeps_two_decimals() -> None:
    # Ledger C2-11 and ADR 0008 both state 6.02%; rounding to 6.0% would make
    # the page disagree with them by eye.
    context = build_context(
        _profile([_profile_row("A", 1.2), _profile_row("X", None, scheduled=False)]),
        _transitions([["A", 1, 1, 18]]),
    )
    share = context["city"]["no_schedule_address_pct"]
    assert share == round(100 * 2590 / (9000 + 2590), 2)


def test_the_page_does_no_arithmetic_of_its_own() -> None:
    """R3's fold must stay in Python, where it is tested.

    A per-zone rate averaged in JSX would render as a plausible number that no
    test here could see. The page may format and it may subtract the two
    rotation means it is handed; it must not divide, sum a list, or reduce.
    """
    page = ZONE_PAGE.read_text(encoding="utf-8")
    for forbidden in ("reduce(", ".length /", "/ zones.length", "sum("):
        assert forbidden not in page, f"{forbidden} folds data in the browser"
    assert "fetch(" not in page, "the page reads the frozen payload through DataProvider"


def test_a_payload_for_the_wrong_figure_is_refused(tmp_path: Path) -> None:
    """Two payloads passed in the wrong order would otherwise build a page
    whose every number comes from the wrong query, without raising."""
    path = tmp_path / "FIG-BO2-06.json"
    path.write_text(json.dumps(_transitions([])), encoding="utf-8")
    with pytest.raises(LookupError):
        load_payload(path, PROFILE_FIG)


def test_a_missing_payload_names_the_command_that_makes_it(tmp_path: Path) -> None:
    with pytest.raises(LookupError, match="eda-export"):
        load_payload(tmp_path / "absent.json", PROFILE_FIG)


def test_an_empty_profile_is_refused_rather_than_rendered_blank() -> None:
    with pytest.raises(LookupError):
        build_context(_profile([]), _transitions([]))


def _write(directory: Path, payload: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{payload['fig_id']}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_the_packager_needs_both_exports_and_makes_no_half_page(tmp_path: Path) -> None:
    """Half the page is not a smaller page.

    Given only the profile, a reader would see a mean shift with nothing to
    read it against and take it for a prediction — the one reading
    FIG-BO2-06's must_not_say rules out. So the packager withholds the whole
    file rather than shipping the half it has.
    """
    _write(tmp_path, _profile([_profile_row("A", 1.2)]))
    assert build_lookup(tmp_path) is None

    _write(tmp_path, _transitions([["A", 1, 1, 18]]))
    context = build_lookup(tmp_path)
    assert context is not None
    assert [z["plow_zone"] for z in context["zones"]] == ["A"]


def test_the_packager_refuses_a_swapped_pair(tmp_path: Path) -> None:
    # Same shape, wrong query: silently builds a page of wrong numbers.
    swapped = _transitions([])
    swapped["fig_id"] = PROFILE_FIG
    _write(tmp_path, swapped)
    _write(tmp_path, _transitions([["A", 1, 1, 18]]))
    with pytest.raises(ValueError):
        build_lookup(tmp_path)


def test_a_stale_lookup_is_removed_rather_than_left_behind(tmp_path: Path) -> None:
    """An export that disappears must take its packaged file with it.

    In the browser a stale lookup.json is indistinguishable from a fresh one:
    the page has no way to tell, and would report last month's zone order under
    this month's certification line.
    """
    from scripts.presentation.portfolio import build_portfolio_data

    source, out = tmp_path / "src", tmp_path / "out"
    _write(source, _profile([_profile_row("A", 1.2)]))
    _write(source, _transitions([["A", 1, 1, 18]]))
    build_portfolio_data(source, out)
    assert (out / "lookup.json").exists()

    (source / f"{TRANSITION_FIG}.json").unlink()
    build_portfolio_data(source, out)
    assert not (out / "lookup.json").exists()


def test_the_page_does_not_present_itself_as_the_city_status_tool() -> None:
    """BO §0.1 rules out a zone-status lookup because the City ships one.

    This page stays on the other side of that line — retrospective order, not
    current state — and the line is only real if the wording holds it. A title
    borrowing the City tool's name is how a retrospective page gets read as a
    status board.
    """
    page = ZONE_PAGE.read_text(encoding="utf-8")
    assert "Know Your Zone" not in page, "borrowing the City tool's name"
    # It must still send the reader there rather than pretend it does that job.
    assert "does not tell you whether your street has been cleared" in page
    assert "winnipeg.ca" in page


def test_the_page_reads_only_keys_the_fold_actually_emits(tmp_path: Path) -> None:
    """Every `zone.<key>` in the JSX must exist on a real record.

    🔴 A missing key is not an error in a browser — it is `undefined`, which the
    page renders as a zero or a dash. The shift histogram spent its first build
    reading `shift_N_count`, a name the fold does not emit, and drew five empty
    bars: a perfectly readable chart of the wrong thing, with nothing raising
    and no test able to see it. Comparing the two sides is the only check that
    catches a key the fold renamed or never had.
    """
    source = tmp_path / "src"
    _write(source, _profile([_profile_row("A", 1.2)]))
    _write(source, _transitions([["A", 1, 1, 18]]))
    record = build_lookup(source)["zones"][0]

    page = ZONE_PAGE.read_text(encoding="utf-8")
    used = set(re.findall(r"\bzone\.([a-z_][a-z0-9_]*)", page))
    missing = sorted(used - set(record))
    assert not missing, f"the page reads keys the fold never emits: {missing}"

    # A key built at run time (``zone[`shift_${n}_count`]``) is invisible to the
    # check above — which is exactly the shape the histogram bug had. The fold's
    # records are flat and known at build time, so there is no honest reason to
    # index one dynamically.
    assert "zone[" not in page, "index the record by name, not by a built-up key"


def test_the_reported_freeze_is_the_older_of_the_two_exports(tmp_path: Path) -> None:
    """A page is as current as its stalest input, not its freshest.

    The two exports are written by separate `make eda-export` runs and nothing
    forces them to happen together, so re-freezing one alone is the ordinary way
    they drift. Reporting the newer stamp would let a week-old transition table
    be presented under today's date.
    """
    source = tmp_path / "src"
    _write(source, _profile([_profile_row("A", 1.2)], frozen_at="2026-09-10T00:00:00"))
    _write(source, _transitions([["A", 1, 1, 18]], frozen_at="2026-09-02T00:00:00"))
    freshness = build_lookup(source)["freshness"]
    assert freshness["frozen_at"] == "2026-09-02T00:00:00"
    assert freshness["same_freeze"] is False


def test_the_reported_certification_is_the_least_reassuring_of_the_two() -> None:
    """`certified` on one half must not hide `suspect` on the other.

    Both exports feed the same two answers, so the page inherits the worse
    status. An unrecognised status ranks below `unknown` rather than above it —
    a state this code has never heard of is not evidence of health.
    """
    assert _worst_certification("certified", "certified") == "certified"
    assert _worst_certification("certified", "suspect") == "suspect"
    assert _worst_certification("suspect", "unknown") == "unknown"
    assert _worst_certification("certified", "banana") == "banana"


def test_the_page_shows_both_exports_provenance_not_only_the_first() -> None:
    """Naming one figure in the provenance list hides a disagreement.

    This is the rendering half of the rule above: the fold can compute the worse
    status correctly and the page can still print only FIG-BO2-06's, leaving a
    reader who checks the footnotes with the reassuring half.
    """
    page = ZONE_PAGE.read_text(encoding="utf-8")
    assert "FIG-BO2-07" in page
    assert "provenance.profile.frozen_at" not in page, "only one export's stamp is shown"


def test_no_freeze_date_is_written_into_the_dashboard_by_hand() -> None:
    """A hand-written freeze date is a claim nothing updates when data is.

    The footer carried "Figures frozen from a certified build on 8 September
    2026" as literal text, which would have kept saying so through every later
    export. Freeze dates are read off the frozen payloads.

    Only dates claiming a *freeze* are banned. A coverage window in prose
    ("November 2023 to May 2026") describes what the data spans and is not this
    defect — banning those too would push the page into vagueness to satisfy a
    test.
    """
    months = ("January|February|March|April|May|June|July|August|September"
              "|October|November|December")
    claim = re.compile(rf"(?:frozen|certified build)[^<>{{}}]{{0,80}}(?:{months})\s+\d{{4}}", re.I)
    for path in sorted((ZONE_PAGE.parent).glob("*.jsx")):
        # Comments are stripped first: the rule is about what the page renders,
        # and the note explaining why the literal was removed quotes it.
        text = re.sub(r"//[^\n]*|/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
        found = claim.search(text)
        assert not found, f"{path.name} states a freeze date by hand: {found.group(0)!r}"
