"""BO-1 probe: does the reporting unit mean the same thing in 2008 and 2026?

``ward`` and ``neighbourhood`` on the request source are **free text**, not keys
into a dimension table. Every downstream artefact treats them as if they were
stable: M1's panel is ward x event, BO-4's crossmap is keyed on the ward label,
and BO-6 scores per reporting unit. Nothing has ever checked that the 15 wards
of today are the 15 wards of 2008 — and a city redraws its electoral boundaries
on a fixed cycle, so the prior should be that they are *not*.

Four numbers come out of here:

    ward by year          which labels exist in which year; renames and gaps
    neighbourhood by year is 242 the current count or the whole-period union?
    keyword misfire       what the winter-request filter actually matches
    deduplicated rows     the winter row count before and after the real key

The keyword check is not busywork. ``%ICE%`` once matched ``Serv-ice`` /
``Pol-ice`` / ``Not-ice`` / ``Invo-ice`` and read the winter share as 10.40%
against a true 1.50%. The narrowed pattern is asserted to be clean in
``winnipeg-data-sources.md`` §3.4; this prints the matched ``type`` values so
the assertion is checkable rather than remembered.

The dedup check exists because 220,580 is quoted throughout without anyone
saying which side of deduplication it sits on. The row grain is an
**interaction**, not a case: ``case_id`` alone is 1.87% duplicated, and the key
is ``(case_id, interaction_id)``.

Usage::

    uv run python -m scripts.analysis.reporting_unit_drift
    uv run python -m scripts.analysis.reporting_unit_drift --json out.json

Read-only against public APIs. Writes nothing to object storage.
"""

from __future__ import annotations

import argparse
import logging
from collections import defaultdict
from datetime import date

from scripts.analysis._probe_common import (
    run_probe,
    socrata_dataset_of,
    soda_get,
    soda_group_walk,
    soda_walk,
)
from scripts.analysis.snowfall_events import (
    WINTER_TYPE_PATTERNS,
    winter_type_filter,
)

logger = logging.getLogger(__name__)

REQUEST_SOURCE = "SRC-WPG-311"

# The unique key, per contracts/api-contracts/winnipeg-311.yaml. Not `case_id`.
KEY_FIELDS = ("case_id", "interaction_id")

# The pattern the narrowed winter filter replaced. Kept as a control: the point
# is not that it is wrong, it is *how much* it pulls in, and that number has to
# be reproducible rather than folklore.
LOOSE_CONTROL_PATTERN = "%ICE%"

# A label present in this share of a year's rows or more is treated as that
# year's active set. Below it, a handful of stragglers from a retired boundary
# would read as "the ward still exists" — which is the drift this probe is for.
DEFAULT_MIN_SHARE = 0.001


# ── Upstream reads ───────────────────────────────────────────────────────────


def counts_by_year(field: str) -> dict[int, dict[str, int]]:
    """Row count per (year, value) for one reporting-unit field.

    Grouped rather than walked: the whole table is 18M rows and the question is
    about the *set* of labels, not the rows. Paged through
    :func:`soda_group_walk` because a grouped query silently truncates at 1,000
    rows otherwise — the same defect that once read the ``type`` cardinality as
    2,000 instead of 3,563.
    """
    ds = socrata_dataset_of(REQUEST_SOURCE)
    rows = soda_group_walk(
        ds,
        select=f"date_extract_y(open_date) as year,{field},count(*) as n",
        group=f"year,{field}",
        order=f"year,{field}",
        where=f"{field} IS NOT NULL",
    )
    per_year: dict[int, dict[str, int]] = defaultdict(dict)
    for row in rows:
        per_year[int(row["year"])][row[field]] = int(row["n"])
    return dict(per_year)


def types_matching(pattern: str) -> dict[str, int]:
    """Distinct ``type`` values a winter pattern matches, with row counts."""
    ds = socrata_dataset_of(REQUEST_SOURCE)
    rows = soda_group_walk(
        ds,
        select="type,count(*) as n",
        group="type",
        order="type",
        where=f"upper(type) like '{pattern}'",
    )
    return {row["type"]: int(row["n"]) for row in rows}


def winter_key_rows() -> list[dict]:
    """The key columns of every winter request carrying a ward label.

    Walked row by row rather than counted server-side: SoQL has no
    ``count(distinct a, b)``, and counting on either column alone answers a
    different question than the one asked.
    """
    ds = socrata_dataset_of(REQUEST_SOURCE)
    where = f"({winter_type_filter()}) AND ward IS NOT NULL"
    return soda_walk(ds, ",".join(KEY_FIELDS), where=where)


def total_rows() -> int:
    ds = socrata_dataset_of(REQUEST_SOURCE)
    return int(soda_get(ds, **{"$select": "count(*) as n"})[0]["n"])


# ── Analysis ─────────────────────────────────────────────────────────────────


def drift(per_year: dict[int, dict[str, int]], min_share: float) -> dict:
    """Per-year label set, and the years where that set changes.

    Reported as explicit added/removed lists per boundary year rather than as a
    stability flag: if the set does drift, the remediation is a normalisation
    dictionary in ``config/``, and whoever writes it needs the labels, not a
    boolean.
    """
    active: dict[int, set[str]] = {}
    for year, counts in per_year.items():
        total = sum(counts.values())
        active[year] = {
            label for label, n in counts.items() if total and n / total >= min_share
        }

    years = sorted(active)
    changes = []
    for previous, current in zip(years, years[1:], strict=False):
        added = sorted(active[current] - active[previous])
        removed = sorted(active[previous] - active[current])
        if added or removed:
            changes.append({"year": current, "added": added, "removed": removed})

    # Labels differing only in case are the drift that a set-difference alone
    # reports as a rename, and that a GROUP BY reports as two units. They are
    # also the cheapest to fix — a casefold in the normalisation dictionary —
    # so they are separated from genuine additions and removals.
    by_fold: dict[str, dict[str, int]] = defaultdict(dict)
    for counts in per_year.values():
        for label, n in counts.items():
            by_fold[label.casefold()][label] = by_fold[label.casefold()].get(label, 0) + n
    case_variants = {
        fold: dict(sorted(spellings.items(), key=lambda kv: -kv[1]))
        for fold, spellings in by_fold.items()
        if len(spellings) > 1
    }

    return {
        "years": [
            {
                "year": year,
                "labels": len(active[year]),
                "rows": sum(per_year[year].values()),
            }
            for year in years
        ],
        "labels_all_time": len({label for labels in active.values() for label in labels}),
        "labels_all_time_casefolded": len(by_fold),
        "case_variants": case_variants,
        "labels_present_every_year": len(set.intersection(*active.values())) if active else 0,
        "changes": changes,
        "stable": not changes,
        "min_share": min_share,
    }


def keyword_audit() -> dict:
    """What the winter filter matches, and what the loose control would add."""
    narrow: dict[str, int] = {}
    per_pattern = {}
    for pattern in WINTER_TYPE_PATTERNS:
        matched = types_matching(pattern)
        per_pattern[pattern] = {
            "type_values": len(matched),
            "rows": sum(matched.values()),
        }
        narrow.update(matched)

    loose = types_matching(LOOSE_CONTROL_PATTERN)
    # Types the loose pattern would sweep in that the narrowed one excludes.
    # These are the false positives, named — the audit row is only checkable if
    # the reader can see what was excluded.
    false_positives = {t: n for t, n in loose.items() if t not in narrow}

    return {
        "per_pattern": per_pattern,
        "matched_type_values": len(narrow),
        "matched_rows": sum(narrow.values()),
        "loose_control": {
            "pattern": LOOSE_CONTROL_PATTERN,
            "type_values": len(loose),
            "rows": sum(loose.values()),
        },
        "false_positive_type_values": len(false_positives),
        "false_positive_rows": sum(false_positives.values()),
        "false_positive_examples": sorted(
            false_positives, key=lambda t: -false_positives[t],
        )[:10],
    }


def dedup(rows: list[dict]) -> dict:
    """Winter row count against the composite key and against ``case_id`` alone.

    Both are reported because the gap between them *is* the finding: whichever
    of the two 220,580 turns out to be, the other one is what a Silver job
    written from memory would produce.
    """
    pairs = {(r["case_id"], r["interaction_id"]) for r in rows}
    cases = {r["case_id"] for r in rows}
    return {
        "rows": len(rows),
        "distinct_composite_key": len(pairs),
        "distinct_case_id": len(cases),
        "duplicate_rows_on_composite_key": len(rows) - len(pairs),
        "rows_lost_if_deduplicated_on_case_id": len(rows) - len(cases),
        "case_id_duplicate_share": (
            round(1 - len(cases) / len(rows), 4) if rows else None
        ),
    }


# ── Report ───────────────────────────────────────────────────────────────────


def build_report(args: argparse.Namespace) -> dict:
    logger.info("counting ward labels by year...")
    ward = counts_by_year("ward")
    logger.info("counting neighbourhood labels by year...")
    neighbourhood = counts_by_year("neighbourhood")
    logger.info("auditing the winter keyword filter...")
    keywords = keyword_audit()
    logger.info("walking winter request keys...")
    keys = winter_key_rows()

    return {
        "probe": "reporting_unit_drift",
        "measured_on": date.today().isoformat(),
        "table_rows": total_rows(),
        "ward_drift": drift(ward, args.min_share),
        "neighbourhood_drift": drift(neighbourhood, args.min_share),
        "winter_keywords": keywords,
        "winter_dedup": dedup(keys),
    }


def print_report(report: dict) -> None:
    print(f"table rows: {report['table_rows']:,}\n")

    for field in ("ward", "neighbourhood"):
        block = report[f"{field}_drift"]
        print(f"── {field} ──────────────────────────────────────────────")
        print(
            f"  {block['labels_all_time']} labels all-time, "
            f"{block['labels_present_every_year']} present every year "
            f"(>= {block['min_share']:.1%} of the year's rows)",
        )
        if block["case_variants"]:
            print(
                f"  {len(block['case_variants'])} labels spelled more than one "
                f"way ({block['labels_all_time_casefolded']} distinct casefolded):",
            )
            for spellings in block["case_variants"].values():
                rendered = ", ".join(f"{s} ({n:,})" for s, n in spellings.items())
                print(f"    {rendered}")
        for change in block["changes"]:
            print(f"  {change['year']}: +{change['added']} -{change['removed']}")
        print(f"  stable: {block['stable']}\n")

    keywords = report["winter_keywords"]
    print("── winter keyword filter ───────────────────────────────")
    for pattern, block in keywords["per_pattern"].items():
        print(f"  {pattern:<16}{block['type_values']:>5} types{block['rows']:>12,} rows")
    print(
        f"  total: {keywords['matched_type_values']} type values, "
        f"{keywords['matched_rows']:,} rows",
    )
    print(
        f"  control {keywords['loose_control']['pattern']}: "
        f"{keywords['loose_control']['type_values']} types / "
        f"{keywords['loose_control']['rows']:,} rows, of which "
        f"{keywords['false_positive_type_values']} types / "
        f"{keywords['false_positive_rows']:,} rows are false positives",
    )
    for name in keywords["false_positive_examples"]:
        print(f"    would wrongly match: {name}")

    dedup_block = report["winter_dedup"]
    print("\n── winter rows with a ward label ───────────────────────")
    print(f"  rows                     {dedup_block['rows']:,}")
    print(f"  distinct (case, action)  {dedup_block['distinct_composite_key']:,}")
    print(f"  distinct case_id         {dedup_block['distinct_case_id']:,}")
    print(
        f"  deduplicating on case_id alone would drop "
        f"{dedup_block['rows_lost_if_deduplicated_on_case_id']:,} genuine rows",
    )


def main(argv: list[str] | None = None) -> int:
    def add_arguments(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--min-share", type=float, default=DEFAULT_MIN_SHARE,
            help="share of a year's rows a label needs to count as active "
                 "(default 0.001, so stragglers from a retired boundary do not "
                 "read as the label still existing)",
        )

    return run_probe(__doc__, build_report, print_report, add_arguments, argv)


if __name__ == "__main__":
    raise SystemExit(main())
