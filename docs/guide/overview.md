# Overview

The **Urban Operations Intelligence Platform (UOIP)** is a Lakehouse pipeline that
ingests municipal open data and turns it into a **Winter Operational Load Score**
per zone, with resource-allocation advice and an SLA compliance audit behind it.

The deployment described in this manual runs on **City of Winnipeg** open data.
The final deliverables are a running pipeline, a report, and a paper.

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
consumed, not modelled), complaint-volume prediction (needs ML, out of MVP scope), and
address-level accountability claims (privacy — analysis never goes below neighbourhood).

---

## 2. What the platform produces

Seven business objectives. Each is rule-driven; **there is no ML model anywhere.**

| | Objective | Output |
|---|---|---|
| **BO-1** | Winter service-request load (demand side) | Daily / per-event request counts per neighbourhood and ward, weighted by the official priority tier |
| **BO-2** | Plow execution tracking (supply side) | Event coverage (zones plowed / 22), shift duration, decision lag from snowfall peak to ban start |
| **BO-3** | Snowfall driver | Dose–response curve: centimetres of snow → requests raised; the empirical threshold at which the City issues a ban |
| **BO-4** | Spatial alignment of three geographies | A `dim_geography` carrying neighbourhood, ward and plow-zone attribution plus an area-weighted crosswalk |
| **BO-5** | **SLA compliance audit** | Attainment rate against the City's own P1/P2/P3 commitments, by priority tier and by ward |
| **BO-6** | Winter Operational Load Score | 0–100 per zone per snow event, with load band, driver attribution and a text recommendation |
| **BO-7** | **Longitudinal clearing dataset** | A daily snapshot archive of address-level clearing status — a time series that does not otherwise exist |

**BO-5 and BO-7 are the differentiators.** BO-5 works because the 311 `type` field
embeds the City's *own* priority and shift wording, so compliance can be measured
against the official standard rather than an invented one. BO-7 exists because the
upstream clearing dataset is overwrite-in-place: the archive can only ever be built
forward from the day collection starts.

The score itself is a weighted sum, normalised by road-network kilometres so that
large zones do not score high merely for being large:

```
Winter Operational Load Score (0–100)
  = 0.35 × weighted service requests   (BO-1)
  + 0.30 × supply gap                  (BO-2 — covered by a ban / shift or not)
  + 0.20 × weather severity            (BO-3)
  + 0.15 × emergency incidents         (motor-vehicle fire/paramedic calls)
```

Weights are initial values and are calibrated against the historical events.

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

49 events × 22 plow zones ≈ 1,000 observation units, more once expanded across 242
neighbourhoods. This is a standard **event-study** design, and it deliberately does not
depend on complaint volume being large — complaints measure *perceived service gap*,
they are not the primary explanatory variable.

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
| **`closed_date` semantics are unverified** | It may mean "ticket closed", not "street actually cleared". Until confirmed, SLA conclusions are stated with that caveat |

---

## 5. Where the code stands

| Layer | State |
|---|---|
| Bronze ingestion machinery (clients, loaders, backfill, audit) | Implemented and unit-tested against the self-hosted stack |
| Snapshot collection (BO-7) | Code complete; **deployment on the storage node is the open blocker** — see [Snapshot Collection](snapshot-collection.md) |
| MinIO environment | Not yet verified end to end; integration tests skip without `S3_*` set |
| Winnipeg 311 / supply-side ingestion | Source YAML and backfill pending |
| Silver, Gold, intelligence SQL, dashboards | Not started |

The authoritative, always-current status is the **Implementation status** section of
`CLAUDE.md` in the repository root. This table is a summary and can lag it.

> ⏱ BO-7 is the only item with a running clock. The upstream keeps no history, so every
> day the collector is not deployed is a day of history that can never be recovered.

---

## Next

- [Architecture](architecture.md) — how the layers and the two nodes fit together
- [Data Sources](data-sources.md) — the datasets, their measured sizes and their defects
- [Getting Started](getting-started.md) — install, configure, run
