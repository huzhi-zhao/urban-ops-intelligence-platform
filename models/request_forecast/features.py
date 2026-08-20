"""Panel -> feature matrix for M1.

Everything here is a pure function over a DataFrame so it can be unit-tested
without Trino, without Spark and without a fitted model. The panel arrives in
**role names** (see the package docstring); nothing in this file knows the word
``plow_zone``.

Panel contract — one row per (unit, event), which is F1 summed across
``winter_category``:

===============  ====================================================
``unit_id``      the operational unit (Winnipeg: plow zone)
``event_id``     the snowfall event
``event_start``  the event's first day; the panel's time order
``season``       snow-season label, e.g. ``2015-2016``
``unit_size``    addresses in the unit — the GLM offset, never a divisor
``target``       request count, summed across categories
===============  ====================================================

plus the six event attributes listed in ``config/models/m1.yaml``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import-time only
    import pandas as pd

# Role-named columns every panel must carry. Absence is an error rather than a
# silently-missing feature: a typo in the SQL that renames a column would
# otherwise train a model on fewer inputs than the config claims.
REQUIRED_PANEL_COLUMNS = (
    "unit_id",
    "event_id",
    "event_start",
    "season",
    "unit_size",
    "target",
)

# 🔴 Never features, and a unit test asserts their absence from the built
# matrix. Schedule order is BO-6's independent second term (weight 0.30); the
# forecast it would leak into is the first (weight 0.40). Mixing them voids the
# measured independence result — r(rank, requests) = +0.017 — which is the whole
# reason the three-factor formula was allowed to keep its residual form.
# The names are listed in both role and Winnipeg spellings because the panel
# query is the thing most likely to drift, and it speaks Winnipeg.
FORBIDDEN_FEATURES = frozenset(
    {
        "shift_number",
        "rank_factor",
        "rank",
        "unit_rank",
        "plow_zone_rank",
    }
)

# Features derived here rather than read from the panel.
DERIVED_FEATURES = ("season_index", "month", "prev_target", "expanding_mean")

# The two lag features are undefined for a unit's first-ever event.
HISTORY_FEATURES = ("prev_target", "expanding_mean")


class PanelError(ValueError):
    """The panel does not satisfy the contract above."""


def validate_panel(panel: pd.DataFrame) -> None:
    """Check the panel's shape before anything is derived from it.

    Raises:
        PanelError: on a missing column, a duplicated (unit, event) pair, or a
            non-positive ``unit_size``. The last one matters more than it
            looks: ``unit_size`` becomes ``log(unit_size)`` as the GLM offset,
            so a zero would surface as a ``-inf`` row deep inside statsmodels
            rather than as a data problem here.
    """
    missing = [c for c in REQUIRED_PANEL_COLUMNS if c not in panel.columns]
    if missing:
        raise PanelError(
            f"panel is missing required column(s) {missing}; "
            f"got {sorted(panel.columns)}"
        )

    duplicated = panel.duplicated(subset=["unit_id", "event_id"])
    if bool(duplicated.any()):
        pairs = panel.loc[duplicated, ["unit_id", "event_id"]].to_dict("records")
        raise PanelError(
            f"panel has {len(pairs)} duplicated (unit_id, event_id) row(s), "
            f"e.g. {pairs[:3]} — the grain is one row per unit per event, so a "
            f"duplicate means the category sum did not collapse"
        )

    bad_size = panel["unit_size"].isna() | (panel["unit_size"] <= 0)
    if bool(bad_size.any()):
        raise PanelError(
            f"{int(bad_size.sum())} row(s) have a null or non-positive unit_size; "
            f"it is the GLM offset (log), so it must be strictly positive"
        )


def order_panel(panel: pd.DataFrame) -> pd.DataFrame:
    """Sort into the causal order the lag features depend on.

    ``event_id`` breaks ties on ``event_start`` so two events starting the same
    day always order the same way. Without a tie-break the lag features would
    depend on the row order the query happened to return, and gate a6
    ("rerunning the same model_version changes nothing") would fail
    intermittently — the worst kind of failure to chase.
    """
    return panel.sort_values(["unit_id", "event_start", "event_id"]).reset_index(drop=True)


def add_calendar_features(panel: pd.DataFrame) -> pd.DataFrame:
    """Add ``season_index`` and ``month``.

    ``season_index`` is the season's ordinal position across the **whole**
    panel, not within a split. That is deliberate and is not leakage: it uses
    only the event's own date, which is known at prediction time. It carries the
    multi-year growth in ticket volume, which a model trained on 2008-2025 and
    scored on 2026 otherwise has no way to express.
    """
    out = panel.copy()
    seasons = sorted(out["season"].dropna().unique())
    index = {season: i for i, season in enumerate(seasons)}
    out["season_index"] = out["season"].map(index).astype("float64")
    out["month"] = out["event_start"].map(lambda d: float(d.month))
    return out


def add_lag_features(panel: pd.DataFrame) -> pd.DataFrame:
    """Add ``prev_target`` and ``expanding_mean``, both strictly causal.

    Both look only at events *strictly before* the current one for the same
    unit, which is what makes them legitimate at prediction time: when a
    snowfall event is forecast, the previous event's actual ticket count is
    already known.

    🔴 The causality is in the ``shift(1)``, and it is the reason these are not
    computed per split. A statistic taken over the whole panel — "this unit's
    mean" — would carry the held-out season into training rows and inflate the
    result silently. Anything added here must keep the shift.

    The first event of each unit gets NaN in both; see
    :func:`drop_rows_without_history`.
    """
    out = order_panel(panel)
    grouped = out.groupby("unit_id", sort=False)["target"]
    prior = grouped.shift(1)
    out["prev_target"] = prior.astype("float64")
    # expanding() on the already-shifted series: mean of all strictly prior
    # events. Doing it the other way round (expanding then shift) is equivalent
    # here but reads as though the current row were included.
    out["expanding_mean"] = (
        prior.groupby(out["unit_id"], sort=False).expanding().mean().reset_index(level=0, drop=True)
    ).astype("float64")
    return out


def build_panel_features(panel: pd.DataFrame) -> pd.DataFrame:
    """Validate, order and derive every feature. The one entry point to use."""
    validate_panel(panel)
    return add_lag_features(add_calendar_features(panel))


def drop_rows_without_history(panel: pd.DataFrame) -> pd.DataFrame:
    """Drop each unit's first event, which has no lag features.

    Dropping beats imputing here. A filled-in "previous count" for a unit's
    first-ever event is a number the model cannot distinguish from a real one,
    and the rows lost are one per unit — 22 of 2,178 — all of them in 2008,
    long before the scheduling era the prediction panel covers. So the
    prediction side loses nothing at all, which
    :func:`assert_prediction_panel_has_history` checks rather than assumes.
    """
    have = panel[list(HISTORY_FEATURES)].notna().all(axis=1)
    return panel.loc[have].reset_index(drop=True)


def assert_prediction_panel_has_history(panel: pd.DataFrame) -> None:
    """Fail if any row we must predict was dropped for lack of history.

    F5's row count is a hard gate (1,298 per model_version). If a prediction row
    had no lag features it would go missing and the gate would report a count,
    not a cause — this says the cause out loud instead.
    """
    missing = panel[list(HISTORY_FEATURES)].isna().any(axis=1)
    if bool(missing.any()):
        rows = panel.loc[missing, ["unit_id", "event_id"]].to_dict("records")
        raise PanelError(
            f"{len(rows)} prediction row(s) have no lag history, e.g. {rows[:3]}. "
            f"Every scheduling-era event is preceded by earlier events for the "
            f"same unit, so this means the training panel was filtered before "
            f"the lag features were built."
        )


def feature_names(config: dict) -> list[str]:
    """Flatten the config's feature groups into the matrix's column order.

    Order is fixed by the config file so a refit produces the same coefficient
    order, and so a diff of the config is a diff of the model.

    Raises:
        PanelError: if the config lists a forbidden feature. Catching it here
            rather than in a test means it fails for whoever edits the YAML,
            not only in CI.
    """
    names: list[str] = []
    for group in ("event", "calendar", "lag"):
        names.extend(config.get("features", {}).get(group, []) or [])

    forbidden = sorted(set(names) & FORBIDDEN_FEATURES)
    if forbidden:
        raise PanelError(
            f"config/models/m1.yaml lists {forbidden} as feature(s). Schedule "
            f"order is BO-6's independent second term and must not enter M1 — "
            f"see FORBIDDEN_FEATURES and design §4.2."
        )
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise PanelError(f"config lists {duplicates} more than once")
    return names


def build_design_matrix(
    panel: pd.DataFrame, names: list[str]
) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Split a prepared panel into (X with intercept, y, offset).

    Booleans become floats because statsmodels' GLM will not accept an object
    dtype, and a bool column silently coerced elsewhere is how ``accum_flag``
    would end up meaning something different in training than in prediction.

    Returns:
        ``(X, y, offset)`` where ``offset`` is ``log(unit_size)`` — the
        per-address rate formulation. The target stays a count so MAE reads in
        real tickets.
    """
    import numpy as np

    missing = [n for n in names if n not in panel.columns]
    if missing:
        raise PanelError(f"prepared panel is missing feature column(s) {missing}")

    leaked = sorted(set(names) & FORBIDDEN_FEATURES)
    if leaked:  # belt and braces: feature_names() already refuses these
        raise PanelError(f"refusing to build a design matrix containing {leaked}")

    X = panel[names].astype("float64").copy()
    X.insert(0, "const", 1.0)
    y = panel["target"].astype("float64")
    offset = np.log(panel["unit_size"].astype("float64"))
    return X, y, offset
