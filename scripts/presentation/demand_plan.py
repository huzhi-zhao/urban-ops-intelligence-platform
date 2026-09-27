"""Gate the frozen demand-and-plan panel (FIG-BO8-03) before anything reads it.

Design: `docs/dev/design/20260926-demand-plan-comparison.md` §5, batch A.
Decision: ADR 0015.

The panel puts M1's estimate next to the published shift for one past snowfall
event and one work zone, and combines them into nothing. Every later step — the
rate per 1,000 addresses, the review prompt, the page — folds this payload, so a
wrong payload surfaces downstream as a different, plausible answer. The gates
below are the batch-A acceptance criteria as code, checked per model version,
because F5 carries several versions side by side (one deliberately broken).

🔴 The counts are equalities, not lower bounds: every one of them is fixed by a
Gold gate that already holds (F5 1,298 · 17 aligned operations × 22 zones), so a
drift here is a build fault, not upstream growth.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

DEMAND_PLAN_FIG = "FIG-BO8-03"

# F5's grain for one version: 59 scheduling-era events x 22 scheduled zones.
EXPECTED_CELLS = 1298
# Cells with both an estimate and a published shift: 17 aligned operations x 22.
EXPECTED_PLANNED_CELLS = 374
EXPECTED_PLANNED_EVENTS = 17
# Holdout season 2025-2026: 7 events x 22 zones.
EXPECTED_HOLDOUT_CELLS = 154
# Of those, only SNOW-20251218 has a published operation (measured 2026-09-27).
# These are the only cells a review prompt may be computed on before R2 lands.
EXPECTED_HOLDOUT_PLANNED_CELLS = 22

FIT_ROLES = frozenset({"holdout", "in_sample"})


class PanelGateError(ValueError):
    """The frozen panel does not have the shape batch A accepted."""


def _rows_as_dicts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    columns = payload["columns"]
    return [dict(zip(columns, row, strict=True)) for row in payload["rows"]]


def gate_failures(payload: dict[str, Any], model_version: str) -> list[str]:
    """Return every batch-A gate one model version fails; empty means accepted.

    All failures are collected rather than stopping at the first, so one run of
    the check names the whole problem.
    """
    rows = [r for r in _rows_as_dicts(payload) if r["model_version"] == model_version]
    failures: list[str] = []
    if not rows:
        return [f"no rows for model_version {model_version!r}"]

    def expect(label: str, actual: int, expected: int) -> None:
        if actual != expected:
            failures.append(f"{label}: {actual}, expected {expected}")

    cells = [(r["snowfall_event_id"], r["plow_zone"]) for r in rows]
    expect("cells", len(rows), EXPECTED_CELLS)
    expect("distinct (event, zone)", len(set(cells)), len(rows))

    planned = [r for r in rows if r["shift_number"] is not None]
    expect("cells with a published shift", len(planned), EXPECTED_PLANNED_CELLS)
    expect(
        "events with a published shift",
        len({r["snowfall_event_id"] for r in planned}),
        EXPECTED_PLANNED_EVENTS,
    )

    roles = {r["fit_role"] for r in rows}
    if not roles <= FIT_ROLES:
        failures.append(f"unknown fit_role values: {sorted(roles - FIT_ROLES)}")
    holdout = [r for r in rows if r["fit_role"] == "holdout"]
    expect("holdout cells", len(holdout), EXPECTED_HOLDOUT_CELLS)
    expect(
        "holdout cells with a published shift",
        sum(r["shift_number"] is not None for r in holdout),
        EXPECTED_HOLDOUT_PLANNED_CELLS,
    )

    # One published operation per event: every zone of a planned event carries
    # a shift, and no event mixes planned and unplanned zones.
    per_event: dict[str, set[bool]] = defaultdict(set)
    for r in rows:
        per_event[r["snowfall_event_id"]].add(r["shift_number"] is not None)
    mixed = sorted(e for e, states in per_event.items() if len(states) > 1)
    if mixed:
        failures.append(f"events with a shift on only some zones: {mixed}")

    missing_denominator = sum(not r["address_count"] for r in rows)
    if missing_denominator:
        failures.append(f"cells without an address_count: {missing_denominator}")
    return failures


def check_panel(payload: dict[str, Any], model_version: str) -> None:
    """Raise with every failed gate, or return quietly when batch A holds."""
    if payload.get("fig_id") != DEMAND_PLAN_FIG:
        raise PanelGateError(f"payload holds {payload.get('fig_id')}, expected {DEMAND_PLAN_FIG}")
    failures = gate_failures(payload, model_version)
    if failures:
        raise PanelGateError(f"{model_version}: " + "; ".join(failures))


# ── Fold for the #/zone section (design §3.8, batch C skeleton) ───────────────
#
# 🔴 The estimate is withheld until its range exists. dashboard §10.2 ② forbids
# a point estimate on the page without an interval, and the interval is H2-R13's
# delivery, not this module's. So until `intervals` is supplied, every cell's
# `estimate` is None and its `estimate_status` says why. The fold drops the
# point value rather than trusting the page to hide it: a number in lookup.json
# is one careless line of JSX away from being rendered.

ESTIMATE_RANGE_PENDING = "range_pending"


class ForecastVersionError(ValueError):
    """The panel holds several model versions and none was named."""


def choose_version(payload: dict[str, Any], requested: str | None) -> str:
    """The one model version the page serves — always explicit when there is a choice.

    Same rule as F6's FORECAST_VERSION (L3 launch §4.6): F5 carries a
    deliberately broken `nomonth` version beside the real one, and every
    automatic pick (lexical order, newest build) can land on it.
    """
    versions = sorted({r["model_version"] for r in _rows_as_dicts(payload)})
    if requested is not None:
        if requested not in versions:
            raise ForecastVersionError(f"{requested!r} is not in {DEMAND_PLAN_FIG}: {versions}")
        return requested
    if len(versions) == 1:
        return versions[0]
    raise ForecastVersionError(
        f"{DEMAND_PLAN_FIG} holds {len(versions)} model versions {versions}; "
        "name one with --forecast-version / FORECAST_VERSION"
    )


def _default_event(events: list[dict[str, Any]]) -> str | None:
    """The event the section opens on: the latest holdout event with a plan.

    Only there does an estimate sit next to a plan without the model having
    seen the answer (design §3.5). Falls back to the latest planned event, then
    the latest event, so the section always opens on something.
    """
    for keep in (
        lambda e: e["has_plan"] and e["fit_role"] == "holdout",
        lambda e: e["has_plan"],
        lambda e: True,
    ):
        picked = [e for e in events if keep(e)]
        if picked:
            return max(picked, key=lambda e: e["start_date"])["snowfall_event_id"]
    return None


def build_demand_plan(payload: dict[str, Any], model_version: str) -> dict[str, Any]:
    """Per-zone, per-event records for the demand-and-plan section.

    Gates first: nothing is folded from a panel batch A would reject.
    """
    check_panel(payload, model_version)
    rows = [r for r in _rows_as_dicts(payload) if r["model_version"] == model_version]

    events: dict[str, dict[str, Any]] = {}
    zones: dict[str, dict[str, Any]] = {}
    for r in rows:
        event_id = r["snowfall_event_id"]
        has_plan = r["shift_number"] is not None
        events.setdefault(event_id, {
            "snowfall_event_id": event_id,
            "start_date": str(r["event_start_date"]),
            "end_date": str(r["event_end_date"]),
            "total_snowfall_cm": round(float(r["total_snowfall_cm"]), 1),
            "snow_season": r["snow_season"],
            "fit_role": r["fit_role"],
            "has_plan": has_plan,
            "plow_event_id": r["plow_event_id"],
            "operation_start": None if r["first_shift_start_utc"] is None
            else str(r["first_shift_start_utc"])[:10],
        })
        zone = zones.setdefault(r["plow_zone"], {
            "plow_zone": r["plow_zone"],
            "address_count": int(r["address_count"]),
            "cells": {},
        })
        zone["cells"][event_id] = {
            "shift_number": int(r["shift_number"]) if has_plan else None,
            "actual_count": None if r["actual_count"] is None else int(r["actual_count"]),
            "estimate": None,
            "estimate_status": ESTIMATE_RANGE_PENDING,
            "prompt": None,
        }

    ordered = sorted(events.values(), key=lambda e: e["start_date"])
    first = rows[0]
    return {
        "model_version": model_version,
        "frozen_at": payload.get("frozen_at"),
        "certification": (payload.get("certification") or {}).get("status", "unknown"),
        "event_rule_version": first["event_rule_version"],
        "data_date": str(first["forecast_source_max_ingest_date"]),
        "address_count_snapshot_date": str(first["address_count_snapshot_date"]),
        "holdout_season": next((e["snow_season"] for e in ordered if e["fit_role"] == "holdout"), None),
        "default_event": _default_event(ordered),
        "events": ordered,
        "zones": zones,
        "counts": {
            "events": len(ordered),
            "events_with_plan": sum(e["has_plan"] for e in ordered),
            "zones": len(zones),
        },
    }
