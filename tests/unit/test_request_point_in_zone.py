"""Unit tests for the BO-4 request-in-zone probe's pure functions.

Two numbers come out of this probe and each has a way of being computed wrongly
that no amount of staring at the output would catch:

- the unscheduled share, whose denominator must be *requests placed in a zone*
  rather than every request, and
- the ward <-> zone crossmap, whose headline figure must be weighted by request
  count — an unweighted mean lets a ward with 40 requests count as much as one
  with 40,000, which is how a non-nesting pair of partitions comes out looking
  like it nests.
"""

from __future__ import annotations

from datetime import date

from scripts.analysis.request_point_in_zone import crossmap, zone_shares

ERA = date(2015, 11, 1)


def placed(*triples: tuple[str, str | None, str | None]) -> list:
    return [(date.fromisoformat(d), w, z) for d, w, z in triples]


# ── zone_shares ──────────────────────────────────────────────────────────────


def test_requests_before_the_era_are_excluded_entirely() -> None:
    """"Unscheduled" is undefined before a schedule exists."""
    report = zone_shares(
        placed(("2010-01-01", "W1", "A"), ("2020-01-01", "W1", "A")), {"A"}, ERA,
    )
    assert report["requests_with_geometry"] == 1
    assert report["assigned_to_a_zone"] == 1


def test_the_unscheduled_share_is_measured_against_placed_requests() -> None:
    """Not against every request: a request outside every zone is neither
    scheduled nor unscheduled, and putting it in the denominator would shrink
    the share by however many points fall outside the city."""
    report = zone_shares(
        placed(
            ("2020-01-01", "W1", "A"),
            ("2020-01-01", "W1", "B/D"),
            ("2020-01-01", "W1", None),
        ),
        {"A"},
        ERA,
    )
    assert report["assigned_to_a_zone"] == 2
    assert report["outside_every_zone"] == 1
    assert report["requests_in_unscheduled_zones"] == 1
    assert report["unscheduled_share"] == 0.5
    # The hit rate keeps the wider denominator — it answers a different question.
    assert report["hit_rate"] == round(2 / 3, 4)


def test_an_empty_era_reports_none_rather_than_zero() -> None:
    report = zone_shares([], {"A"}, ERA)
    assert report["hit_rate"] is None
    assert report["unscheduled_share"] is None


# ── crossmap ─────────────────────────────────────────────────────────────────


def test_perfectly_nested_partitions_score_one() -> None:
    report = crossmap(placed(
        ("2020-01-01", "W1", "A"),
        ("2020-01-01", "W1", "A"),
        ("2020-01-01", "W2", "B"),
    ))
    assert report["ward_to_zone_weighted"] == 1.0
    assert report["zone_to_ward_weighted"] == 1.0
    assert report["ward_to_zone"]["W1"]["counterparts"] == 1


def test_a_ward_split_evenly_across_zones_scores_its_dominant_share() -> None:
    report = crossmap(placed(
        ("2020-01-01", "W1", "A"),
        ("2020-01-01", "W1", "B"),
    ))
    assert report["ward_to_zone"]["W1"]["dominant_share"] == 0.5
    assert report["ward_to_zone_weighted"] == 0.5


def test_the_headline_figure_is_weighted_by_request_count() -> None:
    """A tiny, perfectly-nested ward must not offset a large, split one.

    W1 carries 4 requests split 3/1; W2 carries 1 and is perfectly nested. The
    unweighted mean over wards is 0.875, which would read as "nearly nests";
    the weighted figure is 0.80, which is what the 134k-request panel actually
    experiences.
    """
    report = crossmap(placed(
        ("2020-01-01", "W1", "A"),
        ("2020-01-01", "W1", "A"),
        ("2020-01-01", "W1", "A"),
        ("2020-01-01", "W1", "B"),
        ("2020-01-01", "W2", "C"),
    ))
    assert report["ward_to_zone_dominant_share"]["mean"] == 0.88  # unweighted
    assert report["ward_to_zone_weighted"] == 0.8


def test_rows_missing_either_label_are_not_counted() -> None:
    report = crossmap(placed(
        ("2020-01-01", "W1", "A"),
        ("2020-01-01", None, "A"),
        ("2020-01-01", "W1", None),
    ))
    assert report["requests_with_both_labels"] == 1
