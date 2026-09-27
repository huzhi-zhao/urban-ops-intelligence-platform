"""Score forecast snowfall events with a trained M1 — the forward chain (H2-R12).

Takes events cut from a forecast (``outlook_weather``), puts them next to the
real panel M1 was trained on, derives features with **the same code training
used** (``features.build_panel_features``), and applies a trained model's
stored coefficients.

Why reuse the panel instead of computing features here: ``season_index``,
``prev_target`` and ``expanding_mean`` are defined relative to the rest of the
panel. A second implementation of them would be a second definition, and the
first place the two disagreed would be a prediction nobody could reproduce.
Appending the new event and running the training path makes agreement
structural — and it is what the replay check (a past event fed back in as a
"perfect forecast" must reproduce F5 exactly) verifies.

No refit: the log link makes the mean ``exp(X·β + log(unit_size))``, whatever
the family. A negative-binomial M1 (H2-R1) would plug in unchanged.

Role names only; see the package docstring.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from models.request_forecast import features as feat
from models.request_forecast.outlook_weather import SeverityBounds, SnowfallEvent, snow_season

if TYPE_CHECKING:  # pragma: no cover - import-time only
    import pandas as pd

# Event attributes the panel carries and M1 reads. Kept equal to the config's
# `features.event` list by a test, not by convention.
EVENT_ATTRIBUTES = (
    "total_snowfall_cm",
    "peak_daily_snowfall_cm",
    "duration_days",
    "min_temperature_c",
    "accum_flag",
    "severity_score",
)

# Attributes whose training range is checked. `accum_flag` is boolean and
# `severity_score` is checked against [0, 1] separately.
RANGE_CHECKED = ("total_snowfall_cm", "peak_daily_snowfall_cm", "duration_days", "min_temperature_c")


class OutlookError(ValueError):
    """The outlook cannot be scored honestly."""


@dataclass(frozen=True)
class TrainingEnvelope:
    """What the model saw in training — for flagging extrapolation, not refusing it.

    Taken from the training split (every season except the holdout), because a
    value is "inside training" only if the coefficients were fitted on it.
    """

    ranges: dict[str, tuple[float, float]]
    max_season_index: float
    months: frozenset[int]

    @classmethod
    def from_panel(cls, prepared: pd.DataFrame, holdout_season: str) -> TrainingEnvelope:
        train = prepared[prepared["season"] != holdout_season]
        if train.empty:
            raise OutlookError(f"no training rows outside holdout season {holdout_season!r}")
        ranges = {
            name: (float(train[name].min()), float(train[name].max())) for name in RANGE_CHECKED
        }
        return cls(
            ranges=ranges,
            max_season_index=float(train["season_index"].max()),
            months=frozenset(int(m) for m in train["month"].unique()),
        )


def outlook_event_id(issue_date: date, start: date) -> str:
    """A namespace of its own, so an outlook never collides with a real event id."""
    return f"OUTLOOK-{issue_date:%Y%m%d}-{start:%Y%m%d}"


def event_rows(
    event: SnowfallEvent,
    *,
    event_id: str,
    units: pd.DataFrame,
    bounds: SeverityBounds,
    season_first_month: int,
) -> pd.DataFrame:
    """One panel row per unit for a forecast event; ``target`` unknown (NaN)."""
    import numpy as np

    out = units[["unit_id", "unit_size"]].copy()
    out["event_id"] = event_id
    out["event_start"] = np.datetime64(event.start_date, "ns")
    out["season"] = snow_season(event.start_date, season_first_month)
    out["total_snowfall_cm"] = float(event.total_snowfall_cm)
    out["peak_daily_snowfall_cm"] = (
        float(event.peak_daily_snowfall_cm) if event.peak_daily_snowfall_cm is not None else np.nan
    )
    out["duration_days"] = int(event.duration_days)
    out["min_temperature_c"] = (
        float(event.min_temperature_c) if event.min_temperature_c is not None else np.nan
    )
    out["accum_flag"] = bool(event.accum_flag)
    out["severity_score"] = bounds.score(event.total_snowfall_cm, event.min_temperature_c)
    out["target"] = np.nan
    return out


def units_of(panel: pd.DataFrame) -> pd.DataFrame:
    """The unit set and each unit's size, as the panel knows them.

    Refuses a unit whose size differs between rows: ``unit_size`` is a single
    snapshot (address count) and a disagreement means the panel query joined
    something it should not have.
    """
    sizes = panel.groupby("unit_id")["unit_size"].nunique()
    unstable = sorted(sizes[sizes > 1].index)
    if unstable:
        raise OutlookError(f"unit_size is not constant within unit(s) {unstable}")
    return (
        panel.drop_duplicates("unit_id")[["unit_id", "unit_size"]]
        .sort_values("unit_id")
        .reset_index(drop=True)
    )


def prepare_event(
    history: pd.DataFrame,
    event: SnowfallEvent,
    *,
    event_id: str,
    bounds: SeverityBounds,
    season_first_month: int,
) -> pd.DataFrame:
    """The event's rows with every M1 feature derived, as training would derive them.

    History is cut to events that **started strictly before** this one — the
    same causal boundary as the lag features' ``shift(1)``. Anything later would
    be an outcome the forecast could not have known.

    Events are prepared one at a time on purpose. Two forecast events appended
    together would give the second one a ``prev_target`` of NaN (the first one's
    count is unknown), where the right answer is the last *observed* event.
    """
    import pandas as pd

    prior = history[history["event_start"] < pd.Timestamp(event.start_date)]
    if prior.empty:
        raise OutlookError(f"no history before {event.start_date}; lag features are undefined")
    rows = event_rows(
        event,
        event_id=event_id,
        units=units_of(history),
        bounds=bounds,
        season_first_month=season_first_month,
    )
    combined = pd.concat([prior, rows], ignore_index=True, sort=False)
    prepared = feat.build_panel_features(combined)
    mine = prepared[prepared["event_id"] == event_id].reset_index(drop=True)
    feat.assert_prediction_panel_has_history(mine)

    # Which observed event the lags stop at, per unit — the thing that goes
    # stale in winter until Gold is rebuilt (design §3.3).
    last = prior.sort_values(["unit_id", "event_start", "event_id"]).groupby("unit_id").tail(1)
    return mine.merge(
        last[["unit_id", "event_id"]].rename(columns={"event_id": "lag_asof_event_id"}),
        on="unit_id",
        how="left",
    )


def predict_mean(
    prepared: pd.DataFrame, coefficients: Mapping[str, float], names: Sequence[str]
) -> pd.Series:
    """``exp(X·β + log(unit_size))`` from stored coefficients.

    Raises:
        OutlookError: when the coefficient set is not exactly
            ``const`` + the configured features. A config edited since the
            model was trained would otherwise silently drop or misalign a term.
    """
    import numpy as np

    expected = ["const", *names]
    if list(coefficients) != expected:
        raise OutlookError(
            f"coefficients {list(coefficients)} do not match the configured design "
            f"{expected}; the model was trained on a different feature list"
        )
    x, _y, offset = feat.build_design_matrix(prepared, list(names))
    beta = np.array([float(coefficients[n]) for n in expected])
    return np.exp(x.to_numpy() @ beta + offset.to_numpy())


def flag_extrapolation(prepared: pd.DataFrame, envelope: TrainingEnvelope) -> pd.DataFrame:
    """Boolean columns naming each way a row sits outside what training saw.

    These are for H2-R3 to judge. This module only reports them.
    """
    out = prepared.copy()
    for name, (lo, hi) in envelope.ranges.items():
        value = out[name]
        out[f"outside_{name}"] = value.notna() & ((value < lo) | (value > hi))
    out["outside_severity_score"] = (out["severity_score"] < 0.0) | (out["severity_score"] > 1.0)
    out["outside_season_index"] = out["season_index"] > envelope.max_season_index
    out["outside_month"] = ~out["month"].astype(int).isin(envelope.months)
    return out


def flag_columns(frame: pd.DataFrame) -> list[str]:
    return [c for c in frame.columns if c.startswith("outside_")]
