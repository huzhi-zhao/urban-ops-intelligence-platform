"""Reproducible diagnostics behind the R6 batch-B findings (H2 ledger §2.1).

Three numbers in the ledger were first computed ad hoc in a working session;
this module is where they can be re-run from files, not from memory:

1. **Holdout coverage per snowfall** — how many holdout cells fall inside,
   above or below the served request range. Pass the Poisson summary and the
   negative-binomial one in turn to reproduce both tables.
2. **Direction counts** on each planned holdout snowfall — the prompt's own
   direction (top K by estimated rate *and* shift >= S) and its mirror (bottom
   K *and* shift <= 6 - S), both before the stability test. R6 design §6 item 2
   asks for the mirror so readers can see the rule is not one-sided; "bottom K"
   was confirmed as the mirror by the author on 2026-09-27.
3. **The stability ceiling** — the highest top-K share any zone reaches per
   holdout snowfall, and, for one snowfall, how the replicas whose zone order
   inverts line up with the sign of one coefficient (the ledger traces the
   0.782 ceiling to `expanding_mean` being negative in 109 of 500 replicas).

Standard library only: it reads the frozen exports and the R13 artefacts
directly, so it runs without the ML environment.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from scripts.models.bootstrap_m1 import BOOTSTRAP_PREDICTIONS_FILE

BOOTSTRAP_COEFFICIENTS_FILE = "bootstrap_coefficients.csv"
SHIFT_COUNT = 5


def _panel_rows(panel: dict[str, Any], model_version: str) -> dict[tuple[str, str], dict]:
    columns = panel["columns"]
    rows = {}
    for raw in panel["rows"]:
        row = dict(zip(columns, raw, strict=True))
        if row["model_version"] == model_version:
            rows[(row["snowfall_event_id"], row["plow_zone"])] = row
    if not rows:
        raise ValueError(f"panel has no rows for {model_version!r}")
    return rows


def _check_version(summary: dict[str, Any], model_version: str) -> None:
    if summary.get("model_version") != model_version:
        raise ValueError(
            f"summary belongs to {summary.get('model_version')!r}, not {model_version!r}"
        )


def coverage_by_event(
    panel: dict[str, Any], summary: dict[str, Any], model_version: str
) -> list[dict[str, Any]]:
    """Inside / above / below the served range, per holdout snowfall."""
    _check_version(summary, model_version)
    rows = _panel_rows(panel, model_version)
    tally: dict[str, dict[str, Any]] = {}
    for cell in summary["cells"]:
        row = rows[(cell["snowfall_event_id"], cell["plow_zone"])]
        if row["fit_role"] != "holdout" or row["actual_count"] is None:
            continue
        event = tally.setdefault(cell["snowfall_event_id"], {
            "snowfall_event_id": cell["snowfall_event_id"],
            "has_plan": row["shift_number"] is not None,
            "n_cells": 0, "covered": 0, "above": 0, "below": 0,
        })
        actual = row["actual_count"]
        event["n_cells"] += 1
        if actual > cell["prediction_high"]:
            event["above"] += 1
        elif actual < cell["prediction_low"]:
            event["below"] += 1
        else:
            event["covered"] += 1
    return [tally[k] for k in sorted(tally)]


def direction_counts(
    panel: dict[str, Any], summary: dict[str, Any], model_version: str
) -> list[dict[str, Any]]:
    """The prompt's direction and its mirror, per planned holdout snowfall.

    Neither side applies the stability test: this counts what the rule's first
    two conditions select, which is the comparison design §6 item 2 asks for.
    """
    _check_version(summary, model_version)
    rule = summary["review_prompt"]
    top_k, min_shift = int(rule["top_k"]), int(rule["minimum_shift"])
    max_early_shift = SHIFT_COUNT + 1 - min_shift
    rows = _panel_rows(panel, model_version)
    by_event: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    for cell in summary["cells"]:
        row = rows[(cell["snowfall_event_id"], cell["plow_zone"])]
        if row["fit_role"] == "holdout" and row["shift_number"] is not None:
            by_event[cell["snowfall_event_id"]].append(
                (int(cell["point_rank"]), int(row["shift_number"]), cell["plow_zone"])
            )
    out = []
    for event_id in sorted(by_event):
        cells = by_event[event_id]
        n = len(cells)
        forward = sorted(z for rank, shift, z in cells if rank <= top_k and shift >= min_shift)
        reverse = sorted(
            z for rank, shift, z in cells if rank > n - top_k and shift <= max_early_shift
        )
        out.append({
            "snowfall_event_id": event_id,
            "zones": n,
            "forward": {"rule": f"rank <= {top_k} and shift >= {min_shift}", "zones": forward},
            "reverse": {
                "rule": f"rank > {n - top_k} and shift <= {max_early_shift}", "zones": reverse,
            },
        })
    return out


def stability_ceiling(
    panel: dict[str, Any], summary: dict[str, Any], model_version: str
) -> list[dict[str, Any]]:
    """Highest top-K share any zone reaches, per holdout snowfall."""
    _check_version(summary, model_version)
    rows = _panel_rows(panel, model_version)
    key = str(summary["review_prompt"]["top_k"])
    best: dict[str, float] = {}
    for cell in summary["cells"]:
        if rows[(cell["snowfall_event_id"], cell["plow_zone"])]["fit_role"] != "holdout":
            continue
        event = cell["snowfall_event_id"]
        best[event] = max(best.get(event, 0.0), float(cell["top_k_probabilities"][key]))
    return [
        {"snowfall_event_id": e, "max_top_k_share": best[e], "top_k": int(key)}
        for e in sorted(best)
    ]


def _spearman(a: list[float], b: list[float]) -> float:
    def ranks(values: list[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        for position, index in enumerate(order):
            out[index] = float(position)
        return out

    ra, rb = ranks(a), ranks(b)
    n = len(a)
    mean = (n - 1) / 2
    cov = sum((x - mean) * (y - mean) for x, y in zip(ra, rb, strict=True))
    var = sum((x - mean) ** 2 for x in ra)
    return cov / var


def rank_flip_vs_coefficient(
    panel: dict[str, Any],
    bootstrap_dir: Path,
    model_version: str,
    event_id: str,
    term: str,
) -> dict[str, Any]:
    """Replicas whose zone order inverts on one snowfall, against one term's sign.

    A replica "inverts" when the Spearman correlation between its per-address
    rates and the served point estimate's is negative. The overlap with the
    replicas where `term` is negative is what the ledger reports as 109/109.
    """
    rows = _panel_rows(panel, model_version)
    point = {z: r["predicted_count"] / r["address_count"] for (e, z), r in rows.items()
             if e == event_id}
    if not point:
        raise ValueError(f"{event_id} is not in the panel")
    zones = sorted(point)
    rates: dict[int, dict[str, float]] = defaultdict(dict)
    with (bootstrap_dir / BOOTSTRAP_PREDICTIONS_FILE).open(newline="", encoding="utf-8") as fh:
        for rec in csv.DictReader(fh):
            if rec["snowfall_event_id"] == event_id:
                zone = rec["plow_zone"]
                rates[int(rec["replicate_id"])][zone] = (
                    float(rec["predicted_mean"]) / rows[(event_id, zone)]["address_count"]
                )
    negative: set[int] = set()
    with (bootstrap_dir / BOOTSTRAP_COEFFICIENTS_FILE).open(newline="", encoding="utf-8") as fh:
        for rec in csv.DictReader(fh):
            if rec["term"] == term and float(rec["estimate"]) < 0:
                negative.add(int(rec["replicate_id"]))
    inverted = {
        rid for rid, by_zone in rates.items()
        if _spearman([by_zone[z] for z in zones], [point[z] for z in zones]) < 0
    }
    return {
        "snowfall_event_id": event_id,
        "term": term,
        "replicates": len(rates),
        "inverted_order": len(inverted),
        "term_negative": len(negative),
        "overlap": len(inverted & negative),
        "term_positive_share": 1 - len(negative) / len(rates),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--panel", type=Path, required=True, help="Frozen FIG-BO8-03 JSON.")
    parser.add_argument("--uncertainty", type=Path, required=True,
                        help="FIG-BO8-03-uncertainty JSON (Poisson or negative-binomial).")
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--bootstrap-dir", type=Path, default=None,
                        help="R13 artefacts; enables the rank-flip check.")
    parser.add_argument("--flip-event", default="SNOW-20251218")
    parser.add_argument("--flip-term", default="expanding_mean")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    panel = json.loads(args.panel.read_text(encoding="utf-8"))
    summary = json.loads(args.uncertainty.read_text(encoding="utf-8"))
    report: dict[str, Any] = {
        "model_version": args.model_version,
        "noise_family": (summary.get("noise") or {}).get("family", summary.get("model_family")),
        "coverage_by_event": coverage_by_event(panel, summary, args.model_version),
        "direction_counts": direction_counts(panel, summary, args.model_version),
        "stability_ceiling": stability_ceiling(panel, summary, args.model_version),
    }
    if args.bootstrap_dir is not None:
        report["rank_flip"] = rank_flip_vs_coefficient(
            panel, args.bootstrap_dir, args.model_version, args.flip_event, args.flip_term
        )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
