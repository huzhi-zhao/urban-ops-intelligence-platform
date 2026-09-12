# Results

UOIP's first analysis connects Winnipeg's residential plow schedule with snowfall
events, service requests and work-zone geography. The strongest findings describe
the schedule and the data's coverage. The model and ranking results are exploratory.

This page summarizes recorded measurements from August 30–31, 2026. Each finding
links to its executable query. You do not need to deploy the platform to read the
findings; rerunning those queries requires populated tables in Trino.

## Some zones tend to be scheduled later

Across 19 residential plow operations, zone S's mean scheduled shift was **1.26**
and zone C's was **3.47**. The difference is about **26.5 hours of scheduled
start offset**, using the 12-hour shift length. It does not measure how much
longer residents waited for completed clearing.

The average also hides movement. **21 of 22 zones appeared in the first shift at
least once**, including C. Comparing the first nine operations with the last ten,
V moved later by 1.31 shifts and M by 1.02. The pattern is persistent, but it is
not an unchanged rotation.

Use these two views together:

| View | What to inspect | Query |
|---|---|---|
| Average order with each zone's observed range | Typical position and variation | [FIG-BO2-01](../../sql/presentation/fig_bo2_01_zone_rank_spread.sql) |
| Earlier versus later operations | How the order changed | [FIG-BO2-02](../../sql/presentation/fig_bo2_02_rank_drift.sql) |

Later average order also correlates positively with current address count
(`r = 0.491`, or `0.403` for operations since 2021). Fewer addresses therefore
does not explain the later ordering in this comparison. Routing, road length,
equipment and other operational constraints remain possible explanations; the
correlation alone is not a fairness assessment.
[Address-count comparison query](../../sql/presentation/fig_bo2_04_rank_vs_addresses.sql).

## Snowfall and plowing are different event calendars

The weather rule identifies **99 snowfall events** across 18 snow seasons.
It admits days with at least 3 cm of snowfall or at least 10 cm accumulated over
10 days. Eight events enter only through the accumulation criterion.

Of the 19 residential plow operations, **17 align with a snowfall event** under
the analysis's matching rule. Two remain unmatched. Of the 17 matched operations,
11 start before the weather-defined event ends: a multi-day storm can still be
under way when plowing starts.

This is why “days after snowfall ended” is not a universal response-time metric.
It is also why an event without a residential plow record cannot be described as
receiving no service. The schedule does not cover every kind of winter road work.
See the [event timeline](../../sql/presentation/fig_bo3_01_event_timeline.sql) and
[alignment query](../../sql/presentation/fig_bo3_03_plow_lag.sql).

## A work zone is not a ward

The recorded zone-to-ward crosswalk has only **2 of 25 zones** with all weight
on one ward label. Its median dominant-label share is about **54%**. Zone V
has ten ward labels in the crosswalk; the largest carries about 26% of its weight.

The implemented [crosswalk SQL](../../sql/dml/dim_region_crosswalk.sql) calculates
these weights from **winter service-request counts**, calibrated over the
2023–2024 through 2025–2026 seasons. They describe the mix of labels on requests
assigned to each zone. They are not measurements of geographic area, population
or clearing workload.

The existing [matrix query](../../sql/presentation/fig_bo4_01_zone_ward_matrix.sql)
and [dominant-share query](../../sql/presentation/fig_bo4_02_dominant_share.sql)
read this crosswalk, but their captions still call the weights “area share.”
That wording conflicts with the producing SQL. Do not reuse it to claim physical
containment or render a geographic coverage figure without reconciling the
caption and the underlying measure.

The supported lesson is still useful: a work-zone result cannot simply be
renamed as the result for its most common ward label. [Architecture](architecture.md#aligning-geography)
explains point assignment and the label crosswalk separately.

## A complete panel can contain incomplete evidence

The scoring panel contains **59 events × 22 zones = 1,298 rows**. Coverage of
the panel does not imply coverage of all three scoring inputs.

| Evidence available | Rows | Scoring profile |
|---|---:|---|
| Demand, scheduled order and weather | 374, across 17 events | `full_3factor` |
| Demand and weather, no matched scheduled order | 924, across 42 events | `demand_weather_only` |

The second group accounts for **71.2%** of the panel. Drawing both groups on
one color scale could turn missing scheduling evidence into an apparent low-load
result. Keep separate panels and scales, and include the profile in any exported
table. The recommendation table covers only the first group.

[Panel query](../../sql/presentation/fig_bo6_01_load_panel.sql) ·
[Score interpretation](scoring-and-recommendations.md#two-scoring-profiles).

## The model comparison needs its controls

M1 estimates winter service-request counts using historical event characteristics
and prior demand. On the held-out 2025–2026 season, the recorded mean absolute
error (MAE, in requests per event–zone row) was:

| Estimator | Holdout MAE |
|---|---:|
| M1 Poisson model | 7.345 |
| Control model with the month feature removed | 7.919 |
| Causal expanding-mean baseline | 23.628 |

All three numbers belong together. The holdout has only **7 events and 154
event–zone rows**, the target is highly zero-inflated, and removing the month
feature changes MAE by only 0.574. The gap may say more about a weak baseline
than a robust predictive advantage. It does not support a general claim that
the model outperforms a suitable operational baseline.

These are estimates conditional on **historical weather archive features**,
not forecasts evaluated using information available before the storm. The
[comparison query](../../sql/presentation/fig_bo1_03_forecast_vs_actual.sql)
selects the holdout explicitly; metrics over the whole prediction table are a
different calculation.

## A changed ranking is an experiment, not an improvement measure

The recommendation table compares a score-based ordering with an ordering by
baseline request count. In each event and model version, both are permutations
of ranks 1–22. Their rank differences necessarily sum to zero.

Both model versions move **188 rows upward**, despite producing different
estimates. Counting upward movements cannot establish improved service. The
contribution is a reproducible ranking whose inputs and explanations can be
inspected; operational benefit requires a separate outcome evaluation.
[Rank-displacement query](../../sql/presentation/fig_bo8_01_rank_displacement.sql).

## Data and version context

The [measurement ledger](../dev/requirements/bo-conclusions-and-figures.md)
records the evidence in detail, including corrections to earlier exploratory
measurements. Its baseline includes:

| Item | Recorded value |
|---|---|
| Schema | v1.0, August 22, 2026: 8 Silver and 17 Gold tables |
| Gold dimension build | `l2-20260820T033431Z` |
| BO-2 fact build | `l2-20260820T153519Z` |
| Source watermark for those five descriptive fact tables | `2026-08-17` |
| Recorded certification | `dq-20260830T083000-154b32`, `certified`, 83 checks, no findings or unexecuted checks |
| M1 version | `m1-poisson-20260822-df31d954` |
| Control version | `m1-poisson-nomonth-20260822-30af82f4` |

The certification describes that audit run, not all future rebuilds. Partial
builds can have different run IDs; compare source watermarks and dependency
build order as well. Do not apply the descriptive-table watermark to every
artifact without checking its metadata.

The 19 figure queries were validated in the
[August 31 execution record](../dev/launch/20260827-bo-eda-and-presentation-sql-launch.md).
Three scheduling figures have a repository HTML renderer. The bundled JSON
fixtures are **sample data**, not the frozen production results; their sample
certification field is also a fixture, not evidence of an audit.

For an existing deployment, [Getting Started](getting-started.md#query-and-export-real-results)
shows how to run a figure query and freeze a new export. Preserve the JSON,
its caption and interpretation limits, the repository revision, model version
and relevant build metadata when sharing a result. Upstream records and weather
archives can change, so a fresh pull need not reproduce an old number exactly.

## Next steps

- [Scoring and Recommendations](scoring-and-recommendations.md) explains the calculation.
- [Getting Started](getting-started.md) renders a sample figure locally.
- [Data Quality](data-quality.md) explains what certification does and does not establish.
