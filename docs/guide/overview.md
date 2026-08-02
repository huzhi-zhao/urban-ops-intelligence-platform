# Overview

The **Urban Operations Intelligence Platform (UOIP)** is a Lakehouse pipeline that
ingests municipal open data and turns it into a **Winter Operational Load Score**
per zone, driven by a demand forecast and surfaced as ranked resource
recommendations.

The deployment described in this manual runs on **City of Winnipeg** open data.
The final deliverables are a running pipeline, a report, and a talk.

---

## 1. The problem

Winnipeg calls itself *Winterpeg*. Snow clearing overruns, snow-clearing complaints
and confusing parking-ban rules are a standing item at City Council and in local
news — CBC reported the 2023 snow-clearing budget overrun at **CAD 4.2 million**.

Every snowfall, the operations desk needs to answer three questions:

1. Which neighbourhoods / wards carried the **highest service load** during this event?
2. **Why** was it high — heavy snowfall, uncovered plow shift, or slow response?
3. How should zone priorities and crews be **adjusted** for the next snowfall?

Today the demand signal (311), the supply signal (plow shifts and parking bans) and
the driver signal (weather) sit behind three different endpoints and use **three
non-nesting geographies**. Nothing joins them.

### What this platform deliberately does not do

The City already ships **Know Your Zone** and a near-real-time clearing-progress map.
Those answer *"has my street been cleared?"* — current state, single-point lookup.

> 🚫 Out of scope: clearing-status lookup maps, zone-lookup apps, live progress
> dashboards. They duplicate an official product and add nothing.

UOIP does what the official tools do not: **retrospective, cross-source, accountable
operational analysis.** Also excluded: weather prediction (Open-Meteo forecasts are
consumed, not modelled), address-level accountability claims (privacy — analysis never
goes below neighbourhood), and **address- or street-level clearing completion times**
(no such data exists in the open portal — see BO-2 and BO-7 below).

---

## 2. What the platform produces

Eight business objectives. The pipeline, the score and the attribution are
deterministic; **two predictive models sit on top of them** and supply the
forward-looking inputs that drive the recommendation layer.

| | Objective | Output |
|---|---|---|
| **BO-1** | Winter service-request load **and demand forecast** | Per-event request counts per ward (and per neighbourhood by historical share), weighted by the official priority tier; **model M1** predicts the next event |
| **BO-2** | Plow execution and **completion times** | Shift completion time per zone per event, shift duration, event coverage (zones plowed / 22), decision lag from snowfall peak to ban start |
| **BO-3** | Snowfall driver **and event definition** | The snow-event partition used by every other objective; dose–response curve: centimetres of snow → requests raised |
| **BO-4** | Spatial alignment of three geographies | A `dim_geography` carrying neighbourhood, ward and plow-zone attribution, plus the crosswalk that connects zone-level completion times to ward-level reporting |
| **BO-5** | Response time and **overrun risk** | P50/P90 response time by priority tier; **model M2** predicts, at intake, how long a request will stay open |
| **BO-6** | Winter Operational Load Score | 0–100 per zone per snow event, with load band and driver attribution |
| **BO-7** | **Longitudinal clearing dataset** | A daily snapshot archive of address-level clearing status — a time series that does not otherwise exist |
| **BO-8** | **AI-driven recommendation layer** | Ranked, per-zone resource guidance driven by the model outputs, with traceable attribution for every suggestion |

**BO-7 and BO-8 are the differentiators.** BO-7 exists because the upstream clearing
dataset is overwrite-in-place: the archive can only ever be built forward from the day
collection starts, so it is a dataset contribution rather than an analysis. BO-8 is
where the models earn their place — the ranking is predicted, not merely observed.

> ⚠️ **"Completion time" means the plow *shift* completion time for a zone during an
> event** (`tix9-r5tc.shift_end`), not the moment any individual street or address was
> cleared. No dataset in the portal records the latter — which is precisely why BO-7
> exists. See ADR 0007 in the developer documentation.

The score itself is a weighted sum, normalised by the address count in each zone so
that large zones do not score high merely for being large:

```
Winter Operational Load Score (0–100)
  = 0.40 × predicted service requests   (BO-1 / M1 — forward-looking)
  + 0.30 × supply gap                   (BO-2 — covered by a shift or not)
  + 0.30 × weather severity             (BO-3)
```

Weights are initial values and are calibrated against the historical events. The
demand term is a *prediction*, which is what makes the score usable before a snowfall
rather than only after it.

### The two models

| | Predicts | Unit | Baseline it must beat |
|---|---|---|---|
| **M1** | Service-request volume for the next snow event | ward × snow event | Seasonal naive (same ward, historical mean for comparable events) |
| **M2** | Time-to-close / overrun probability for one request | single request | Median duration for that priority tier |

Both are evaluated on a **temporal split** — earlier winters train, the most recent
held-out winter tests. Random splits are not used: with a panel and a long-term
volume trend, they leak. **Every reported model metric is paired with its baseline**;
a model number without its baseline is not a result.

Full objective breakdown, acceptance criteria and measured evidence live in the
developer documentation (`docs/dev/requirements/`, written in Chinese).

---

## 3. The unit of analysis: events, not complaints

The supply side is **sparse by nature** — 11 years of data contain 49 parking bans and
only 19 residential plow events. City-wide residential plowing is a rare, major
operation, not a daily one.

So the unit of analysis is not a complaint. It is a **snow event × zone**:

```
snowfall (cm)      →  ban issued?      →  shifts worked   →  complaints raised
driver variable       decision variable   supply variable    outcome variable
daily, 18 years       49 bans             418 shift rows     ~220k geocoded rows
```

**Events are defined by the weather, not by the City's response.** Partitioning 18
years of daily snowfall at a calibrated threshold yields events in the *hundreds*;
there are only 19 residential plow operations and 49 bans in the same period. Using
the City's response to define events would leave far too few observations to model
anything. The difference between the two — *it snowed, but no crew was dispatched* —
is itself the supply-gap measure that feeds the score.

This is a standard **event-study** design, and it deliberately does not depend on
complaint volume being large — complaints measure *perceived service gap*, they are
not the primary explanatory variable.

Modelling happens at **ward** level (15 units, ~5 winter requests per ward per day) and
is allocated down to **neighbourhood** by historical share. Neighbourhoods are not
modelled directly: at 242 units the daily count averages ~0.34, so almost every cell is
zero and a model that always predicts zero would score well while saying nothing.

That matters, because winter work is only **1.50%** of all 311 requests. The design
answers that objection head-on rather than hiding it.

---

## 4. Constraints you should know before reading any number

These are measured facts about the upstream data, not pipeline defects. Every one of
them changes how a result must be read.

| Constraint | Consequence |
|---|---|
| **Geography is missing on 79% of 311 rows** (20.9% carry a neighbourhood; 80.1% of *winter* rows do) | Spatial analysis has an effective denominator of ~3.8M rows, not 18.3M. Alerting on spatial-join misses must use the geocoded subset or it fires forever |
| **Channel taxonomy broke in 2022** — three digital channels went to zero as `VOF` rose 23× | Channel-mix comparisons across 2022 are invalid. Volume and type comparisons remain valid. Silver normalises the channels |
| **Supply side is small** — 49 bans, 19 plow events | Forces the event-study design above; it is a business reality, not missing data |
| **Total request volume fell 66%** from the 2013 peak, then recovered | Verified as a global trend, not a reclassification of winter categories. Year-over-year absolute comparisons need this caveat stated |
| **`closed_date` semantics are unverified** | It may mean "ticket closed", not "street actually cleared". M2 is unaffected — its target *is* time-to-close, by definition. An audit against the City's official commitments would be affected, which is why that audit is out of this round's scope |
| **No address- or street-level clearing time exists** | `g3p4-h83y` carries clearing status with **no time field at all** and is overwritten in place. Completion time is therefore measured at plow-zone × shift granularity (BO-2), and BO-7 exists to build the missing time series going forward |

---

## 5. Where the code stands

| Layer | State |
|---|---|
| Bronze ingestion machinery (clients, loaders, backfill, audit) | Implemented and unit-tested against the self-hosted stack |
| Snapshot collection (BO-7) | Code complete; **deployment on the storage node is the open blocker** — see [Snapshot Collection](snapshot-collection.md) |
| MinIO environment | Not yet verified end to end; integration tests skip without `S3_*` set |
| Winnipeg 311 / supply-side ingestion | Source YAML and backfill pending |
| Plow-zone boundaries (`39ur-higg`) | **Unverified** — metadata only, never actually called. The zone ↔ ward crosswalk depends on it, and so does the link between completion times and the reporting geography |
| Silver, Gold, intelligence SQL, dashboards | Not started |
| Prediction layer (M1, M2) | Not started; depends on Silver and the snow-event partition |

The authoritative, always-current status is the **Implementation status** section of
`CLAUDE.md` in the repository root. This table is a summary and can lag it.

> ⏱ BO-7 is the only item with a running clock. The upstream keeps no history, so every
> day the collector is not deployed is a day of history that can never be recovered.

---

## Next

- [Architecture](architecture.md) — how the layers and the two nodes fit together
- [Data Sources](data-sources.md) — the datasets, their measured sizes and their defects
- [Getting Started](getting-started.md) — install, configure, run
