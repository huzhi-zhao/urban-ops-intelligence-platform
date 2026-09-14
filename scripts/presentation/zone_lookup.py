"""Fold the two `carrier: lookup` figures into the records the portfolio's
zone page reads — one per plow zone, plus the city-wide totals.

`scripts.presentation.portfolio` calls :func:`build_context` and writes the
result to `dashboard/public/data/lookup.json`; `dashboard/src/zone.jsx` renders
it. There is no second renderer and no standalone page: the portfolio site is
where a reader already is, and a second HTML surface answering the same two
questions would be one more thing to keep in step.

🔴 **Every number is computed here, at build time, from the frozen payloads.**
The browser only chooses which pre-computed record to show. Nothing is
recomputed in JavaScript and nothing is fetched beyond the frozen JSON, which
is the same constraint the rest of the site keeps: it never reaches Trino or
MinIO (design 20260903 §3.1, C7).

🔴 **Ratios are computed after summing counts, never averaged across zones.**
`fig_bo2_07` deliberately emits transition *counts* at
(plow_zone, prev_shift, next_shift) and no percentages, for the reason
`.claude/rules/gold-sql.md` R3 gives: a mean of per-zone hit rates weights a
zone with 18 transitions the same as the whole city, and returns a plausible
wrong number.

🔴 **This is not a zone-status lookup, and the page's wording has to keep
saying so.** BO §0.1 rules out "除雪状态查询地图、分区查询 app、实时进度看板" because the
City already ships Know Your Zone and a near-real-time clearing map, which
answer *current state for one address*. The page answers neither: it reports a
zone's position across the 19 completed operations and the backtested hit rate
of one rule. It must never carry the City tool's name, must never imply it
knows about the operation happening now, and must never be described as
telling anyone whether their street has been cleared.

🔴 **The three no-schedule zones are rendered as an answer, not as an empty
card.** B/D · X · Downtown carry 6.02% of the city's addresses and are absent
from the 22-zone rank panel by construction (ADR 0010 §5 Q5). A lookup that
silently returned nothing for them would read as a bug to the one reader who
most needs to be told the data does not cover them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROFILE_FIG = "FIG-BO2-06"
TRANSITION_FIG = "FIG-BO2-07"
SHIFTS = (1, 2, 3, 4, 5)


class LookupError(ValueError):
    """A payload is missing, is the wrong figure, or lost a column."""


def _rows_as_dicts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    columns = payload["columns"]
    return [dict(zip(columns, row, strict=True)) for row in payload["rows"]]


def load_payload(path: Path, expected_fig_id: str) -> dict[str, Any]:
    """Read one `scripts.eda.run --json` export and refuse anything else.

    The check is on `fig_id`, not on the filename: two payloads in the wrong
    order would otherwise build a page whose every number is silently drawn
    from the wrong query.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LookupError(f"{path}: not found — run `make eda-export` first") from exc
    for key in ("fig_id", "columns", "rows", "header"):
        if key not in payload:
            raise LookupError(f"{path}: missing '{key}' — not a scripts.eda.run --json export")
    if payload["fig_id"] != expected_fig_id:
        raise LookupError(f"{path}: holds {payload['fig_id']}, expected {expected_fig_id}")
    return payload


def summarise_transitions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold the (zone, prev, next) counts into per-zone and city-wide totals.

    Returned counts only. The page turns them into percentages once, at the
    point of display, so that a city-wide rate is a ratio of city-wide sums.
    """
    per_zone: dict[str, dict[str, int]] = {}
    city = {"transitions": 0, "exact": 0, "within1": 0}
    next_marginal = dict.fromkeys(SHIFTS, 0)
    after_first = {"transitions": 0, "shift_sum": 0}
    after_late = {"transitions": 0, "shift_sum": 0}
    for row in rows:
        zone = row["plow_zone"]
        prev_shift, next_shift = int(row["prev_shift"]), int(row["next_shift"])
        count = int(row["transitions"])
        bucket = per_zone.setdefault(zone, {"transitions": 0, "exact": 0, "within1": 0})
        bucket["transitions"] += count
        city["transitions"] += count
        if prev_shift == next_shift:
            bucket["exact"] += count
            city["exact"] += count
        if abs(prev_shift - next_shift) <= 1:
            bucket["within1"] += count
            city["within1"] += count
        next_marginal[next_shift] = next_marginal.get(next_shift, 0) + count
        # The rotation check: if the city rotated its zones, an operation spent
        # in shift 1 would tend to be followed by a late one. These two sums
        # are what says whether it does, and they are reported as the two means
        # rather than as a verdict word.
        target = after_first if prev_shift == 1 else (after_late if prev_shift >= 4 else None)
        if target is not None:
            target["transitions"] += count
            target["shift_sum"] += count * next_shift
    # The comparator: the single fixed shift that would have scored best under
    # the same +-1 rule. Without it, "83% within one shift" cannot be read —
    # most cells sit in shifts 1-3, so a constant guess already scores highly.
    best_fixed = max(
        SHIFTS,
        key=lambda s: sum(c for shift, c in next_marginal.items() if abs(shift - s) <= 1),
    )
    city["best_fixed_shift"] = best_fixed
    city["best_fixed_within1"] = sum(
        c for shift, c in next_marginal.items() if abs(shift - best_fixed) <= 1
    )
    return {
        "per_zone": per_zone,
        "city": city,
        "after_first": after_first,
        "after_late": after_late,
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if not denominator else round(100.0 * numerator / denominator, 1)


def _mean(total: int, count: int) -> float | None:
    return None if not count else round(total / count, 2)


def build_records(
    profile_rows: list[dict[str, Any]], transitions: dict[str, Any]
) -> list[dict[str, Any]]:
    """One display record per plow zone, in the profile query's own order.

    The average-rank position is computed over the zones that actually carry a
    schedule, so a zone is never told it is "22nd of 25" against three zones
    that were never in the ranking.
    """
    scheduled = [r for r in profile_rows if int(r["operations"] or 0) > 0]
    order = sorted(scheduled, key=lambda r: (float(r["mean_shift"]), r["plow_zone"]))
    position = {r["plow_zone"]: i + 1 for i, r in enumerate(order)}
    per_zone = transitions["per_zone"]
    records = []
    for row in profile_rows:
        zone = row["plow_zone"]
        operations = int(row["operations"] or 0)
        counts = per_zone.get(zone, {"transitions": 0, "exact": 0, "within1": 0})
        records.append(
            {
                "plow_zone": zone,
                "has_plow_schedule": bool(row["has_plow_schedule"]),
                "address_count": int(row["address_count"]),
                "operations": operations,
                "mean_shift": row["mean_shift"],
                "min_shift": row["min_shift"],
                "max_shift": row["max_shift"],
                "mean_early": row["mean_early"],
                "mean_late": row["mean_late"],
                "last_shift": row["last_shift"],
                "distribution": [int(row[f"shift_{s}_count"] or 0) for s in SHIFTS],
                "recent_operations": int(row["recent_operations"] or 0),
                "recent_top2_count": int(row["recent_top2_count"] or 0),
                "position": position.get(zone),
                "of_zones": len(order),
                "transitions": counts["transitions"],
                "exact": counts["exact"],
                "within1": counts["within1"],
                "exact_pct": _ratio(counts["exact"], counts["transitions"]),
                "within1_pct": _ratio(counts["within1"], counts["transitions"]),
            }
        )
    return records


# Certification is a three-state signal (ADR 0012) and the states are ordered by
# how much they should stop a reader: `certified` is the only clean one,
# `suspect` means the audit looked and found something, `unknown` means it could
# not look at all. Anything the export carries that is none of these is treated
# as at least as bad as `unknown` — a status this code does not recognise is not
# evidence of health.
_CERTIFICATION_ORDER = ("certified", "suspect", "unknown")


def _worst_certification(*statuses: str) -> str:
    """The least reassuring of the given statuses.

    🔴 The page is exactly as trustworthy as its worst input. Showing only the
    first figure's status would let a `certified` profile hide a `suspect`
    transition table, and the two feed the same two answers.
    """
    ranked = [
        (_CERTIFICATION_ORDER.index(s) if s in _CERTIFICATION_ORDER else len(_CERTIFICATION_ORDER), s)
        for s in statuses
    ]
    return max(ranked)[1]


def _freshness(profile: dict[str, Any], transition: dict[str, Any]) -> dict[str, Any]:
    """When the page's numbers were frozen, and whether both halves agree.

    🔴 The reported time is the **older** of the two freezes, not the newer. A
    page is only as current as its stalest input, and the two exports are
    written by separate `make eda-export` runs that nothing forces to happen
    together — re-freezing one alone is the ordinary way this drifts.
    """
    stamps = [str(p.get("frozen_at") or "") for p in (profile, transition)]
    statuses = [str((p.get("certification") or {}).get("status") or "unknown") for p in (profile, transition)]
    return {
        "frozen_at": min(stamps) if all(stamps) else None,
        # Same build or not. A reader cannot be asked to compare two timestamps
        # in the footnotes; the page has to say it.
        "same_freeze": stamps[0] == stamps[1] and bool(stamps[0]),
        "certification": _worst_certification(*statuses),
    }


def build_context(
    profile: dict[str, Any], transition: dict[str, Any]
) -> dict[str, Any]:
    """Everything the page needs, as plain JSON-safe data."""
    profile_rows = _rows_as_dicts(profile)
    if not profile_rows:
        raise LookupError(f"{PROFILE_FIG} froze zero rows — nothing to look up")
    transitions = summarise_transitions(_rows_as_dicts(transition))
    city = transitions["city"]
    records = build_records(profile_rows, transitions)
    no_schedule = [r for r in records if not r["has_plow_schedule"]]
    total_addresses = sum(r["address_count"] for r in records)
    return {
        "zones": records,
        "city": {
            "transitions": city["transitions"],
            "exact_pct": _ratio(city["exact"], city["transitions"]),
            "within1_pct": _ratio(city["within1"], city["transitions"]),
            "best_fixed_shift": city["best_fixed_shift"],
            "best_fixed_within1_pct": _ratio(city["best_fixed_within1"], city["transitions"]),
            "mean_after_first": _mean(
                transitions["after_first"]["shift_sum"], transitions["after_first"]["transitions"]
            ),
            "mean_after_late": _mean(
                transitions["after_late"]["shift_sum"], transitions["after_late"]["transitions"]
            ),
            # The panel's operation count, read off the data rather than
            # written as 19: the number grows with every new plow operation,
            # and a literal would keep saying 19 after the next one.
            "operations_total": max((r["operations"] for r in records), default=0),
            "zones_total": len(records),
            "zones_scheduled": sum(1 for r in records if r["has_plow_schedule"]),
            "no_schedule_zones": [r["plow_zone"] for r in no_schedule],
            # Two decimals here and only here: this is the number ADR 0008 and
            # ledger C2-11 both state as 6.02%, and rounding it to 6.0% would
            # make the page and the ledger disagree by eye.
            "no_schedule_address_pct": None
            if not total_addresses
            else round(100.0 * sum(r["address_count"] for r in no_schedule) / total_addresses, 2),
        },
        "freshness": _freshness(profile, transition),
        "provenance": {
            "profile": {
                "source_sql": profile.get("source_sql", "?"),
                "frozen_at": profile.get("frozen_at", "?"),
                "certification": (profile.get("certification") or {}).get("status", "unknown"),
            },
            "transition": {
                "source_sql": transition.get("source_sql", "?"),
                "frozen_at": transition.get("frozen_at", "?"),
                "certification": (transition.get("certification") or {}).get("status", "unknown"),
            },
        },
    }
