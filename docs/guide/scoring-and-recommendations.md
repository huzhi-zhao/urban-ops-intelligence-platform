# Scoring and Recommendations

UOIP turns event–zone data into request estimates, load scores and a ranking
comparison. This page follows those outputs in order. Read [Results](results.md)
first for the findings, or [Architecture](architecture.md) for the pipeline.

## Start with the grain

A *fact table* holds measurements at a declared grain. A *dimension* supplies
context, such as which event a row refers to or how many addresses a zone contains.
Changing the grain changes what a number means.

| Table | One row represents | Used for |
|---|---|---|
| `fact_service_request_zone_event` | Event × zone × winter category | Observed demand; categories are summed for M1 |
| `fact_event_zone_rank` | Residential operation × zone | Scheduled shift and its matched snowfall event |
| `fact_request_forecast` | Event × zone × model version | Predicted, actual and baseline request counts |
| `fact_winter_event_zone_load` | Event × zone | Score from one explicitly selected model version |
| `fact_recommendation` | Event × zone × model version | Ranking comparison and rule-based explanation |

Column contracts live in [Gold DDL](../../sql/ddl/). Preserve `model_version`
when querying forecasts or recommendations: summing actual counts across versions
would count the same observed demand more than once.

## Estimate demand with M1

M1 is a Poisson generalized linear model, a statistical model for nonnegative
counts. It predicts winter request counts. An address-count offset accounts for
zone size while keeping the prediction and error metrics in units of requests.

Features include snowfall, temperature, event duration, calendar terms and
strictly prior demand. Scheduled shift is excluded: it enters the score as a
separate factor and should not also be hidden inside the demand estimate.

The configured input panel covers **99 events × 22 zones = 2,178 cells**.
Evaluation holds out the latest snow season; fitting uses earlier seasons.
The stored prediction output covers the scheduling-era subset, **59 × 22 =
1,298 cells per model version**. That entire output is not a held-out test set.
See [model configuration](../../config/models/m1.yaml) and the
[training entry point](../../scripts/models/train_m1.py).

The baseline is the same zone's causal expanding mean of prior demand. Some
configuration and metric labels call it `seasonal_naive`; that name should not
be read as a same-season matching algorithm.

H1 uses historical weather archive values for the event features. It therefore
tests estimates **given an event's characteristics**. A model consuming actual
pre-storm forecast snapshots is a separate evaluation, not established here.
The small holdout, zero-inflated target and control-model result must accompany
any reported MAE; see [the three-way comparison](results.md#the-model-comparison-needs-its-controls).

## Build the load score

For a row with all three inputs:

```text
load_score = 100 × (
    0.40 × request_forecast_factor
  + 0.30 × rank_factor
  + 0.30 × weather_severity_factor
)
```

| Factor | Meaning |
|---|---|
| `request_forecast_factor` | Predicted requests per 1,000 current addresses, min–max scaled over the serving prediction panel |
| `rank_factor` | Scheduled shift divided by five: 0.2, 0.4, 0.6, 0.8 or 1.0 |
| `weather_severity_factor` | Event severity, repeated across zones in H1's single-point weather implementation |

Weather distinguishes events, but cannot distinguish zones within the same event
in this baseline. Nominal weights are not measured influence: the factors occupy
different observed ranges. A component's weighted contribution explains the
formula, not a causal effect on service.

Normalization is tied to the build's reference panel. Appending events or changing
model versions may change the scale, so scores from different builds should not
be compared without checking their inputs and normalization context.

## Two scoring profiles

If no plow operation matches an event, `rank_factor` remains NULL. The calculation
omits that contribution but **does not redistribute its 0.30 weight**.

| `score_status` | `score_weight_profile` | Nominal ceiling | Included in recommendations? |
|---|---|---:|---|
| `scored` | `full_3factor` | 100 | Yes |
| `partial_no_rank` | `demand_weather_only` | 70 | No |

The nominal ceiling is not necessarily attained by the observed inputs. A NULL
rank means “no matched scheduling evidence,” not “scheduled first” or “no crew.”
The three geographic zones without any schedule history are excluded from the
22-zone scoring panel altogether.

Levels use fractions of each profile's own ceiling:

| Level | Fraction of ceiling | Full profile | Partial profile |
|---|---|---|---|
| `LOW` | Below 25% | Below 25 | Below 17.5 |
| `MED` | 25% to below 50% | 25 to below 50 | 17.5 to below 35 |
| `HIGH` | 50% to below 75% | 50 to below 75 | 35 to below 52.5 |
| `CRITICAL` | 75% or above | 75 or above | 52.5 or above |

For illustration, a score of 40 is `MED` in the full profile and `HIGH` in the
partial profile. These are formula examples, not measured zone results. Keep
profiles separate in charts and comparisons. The recorded partial panel has no
`CRITICAL` rows, but the formula allows them.

The executable definition is in
[load score SQL](../../sql/intelligence/fact_winter_event_zone_load.sql).

## Read a recommendation

For each matched snowfall event and model version, zones are ordered by the
combined score. Ties are broken by zone identifier so reruns have a stable order.
The comparison ordering uses the stored baseline request count.

```text
rank_delta = rank_baseline - rank_model
```

A positive difference means the zone moved toward rank 1. It is a displacement,
not measured improvement. Since both lists contain the same 22 zones, every
upward move is balanced elsewhere.

`attribution_rule_id` selects a template according to weighted contributions.
`attribution_text` fills it with values from Gold. The explanation is rule-based,
not generated by a language model, and should not be read as causal attribution.

Use the output to inspect how a proposed ranking responds to its inputs. It does
not optimize routes, assign crews or demonstrate improved clearing outcomes.
The [ranking SQL](../../sql/intelligence/fact_recommendation.sql) is the exact definition.

## Rebuild deliberately

Building scores changes stored tables. Use your own configured deployment and
review the selected model version before rebuilding. The supported CLI accepts
an explicit version; the current Airflow wrapper does not expose that option.

Set `MODEL_VERSION` to an uploaded artifact version in your deployment, then preview:

```bash
make gold-build ONLY=scoring FORECAST_VERSION="$MODEL_VERSION" DRY_RUN=1
```

Inspect the plan, then remove `DRY_RUN=1` to execute it. The build reads uploaded
forecast artifacts, rebuilds the scoring tables in dependency order and runs its
gates. The pipeline does not silently select between multiple model versions.

After a rebuild, run the applicable [quality checks](data-quality.md#inspect-a-build)
and export new figure data. Keep the prior export if you need to preserve a
published result. [Operations](operations.md) covers the wider rebuild sequence.
