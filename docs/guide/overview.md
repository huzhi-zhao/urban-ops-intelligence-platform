# Overview

The **Urban Operations Intelligence Platform (UOIP)** answers one question a winter city
cannot currently answer about itself: *after that snowfall, where did service actually
break down — and where should the next storm's crews go?*

It is a data platform that joins what citizens reported, what crews actually did, and
what the sky actually did, and turns the three into a **Winter Operational Load Score**
per zone per snow event, with a ranked recommendation for the next one.

> **Status:** in build. The deployment described here runs on **City of Winnipeg** open
> data, targeting a September 2026 delivery. Ingestion is running; the scoring and
> recommendation layers are being built. [Where the project stands](#8-where-the-project-stands)
> is at the bottom of this page.

---

## 1. The morning after

Winnipeg calls itself *Winterpeg*, and it does not mean it kindly. Snow clearing
overruns its budget, snow clearing generates complaints, and the parking-ban rules
confuse the people they are meant to protect. All three are standing items at City
Council and in the local news — CBC put the **2023 snow-clearing overrun at CAD 4.2
million**.

So: it snowed 14 cm overnight, and it is now Tuesday morning. The operations desk needs
three answers.

1. **Which neighbourhoods carried the heaviest service load** during this event?
2. **Why was it heavy** — was it the snowfall, was it a zone no crew was sent to, or was
   it a normal response that simply ran long?
3. **What should change before the next snowfall** — which zones move up the priority
   list, and where do the crews go?

None of these is an exotic analytical question. A city that could answer them would
plan differently. Today, nobody can answer them — not because the data is secret, but
because it has never been put in one place.

---

## 2. Why nobody can answer it today

The evidence exists, in three piles, and the piles do not touch.

| The signal | Where it lives | What it alone can tell you |
|---|---|---|
| **Demand** — what residents reported | 311 service requests, 18.3M rows since 2008 | That people complained. Not whether they were right |
| **Supply** — what the City actually did | Plow shift records and parking bans | That crews worked. Not whether it was enough, or where it wasn't |
| **Driver** — what the weather did | Daily snowfall, 18 years | That it snowed. Not what it cost |

Three separate endpoints, three separate release cadences — and, decisively, **three
geographies that do not nest inside one another**. A plow zone is not a ward. A ward is
not a neighbourhood. A complaint arrives tagged with one of them, a shift record with
another, and no official crosswalk connects them. That single misalignment is why the
join has not simply been done by somebody with a spreadsheet, and building it is one of
the platform's load-bearing pieces of work.

Put the three together and one specific sentence becomes measurable for the first time:

> **It snowed here, and no crew was sent.**

That sentence — the gap between the driver and the supply — is the core of everything
below. It cannot be seen in any of the three sources on its own.

---

## 3. What UOIP puts on the desk

Three things, in plain terms.

**A score.** For every zone in every snow event, a 0–100 **Winter Operational Load
Score**: how hard this zone was hit relative to what was done about it. Normalised by
the number of addresses in the zone, so a large zone does not score high merely for
being large.

**A reason.** The score is decomposed, never a black-box number. Every score comes with
its drivers: how much of it was snowfall, how much was an uncovered plow shift, how much
was slow response.

**A recommendation.** A ranked list of where service and resources should go before the
next snowfall — driven by a *forecast* of demand, not just a record of the last storm,
and traceable back to the evidence that produced it.

Illustrative shape of the output — **not real results**, the scoring layer is still
being built:

| Zone | Event | Score | Leading driver | Recommendation |
|---|---|---|---|---|
| Zone A | 2024-01-12, 18 cm | 87 | No shift worked within 48 h | Raise priority; earliest crew |
| Zone B | 2024-01-12, 18 cm | 61 | Heavy snowfall, shift covered | Hold; monitor response times |
| Zone C | 2024-01-12, 18 cm | 24 | Covered, low demand | No change |

The point of the third column is not the number. It is that a number this specific can
be **argued with** — traced to the snowfall it came from, the shift record it came from,
and the requests it came from. An operations decision that can be audited is a different
kind of object from one that cannot.

---

## 4. What this platform deliberately does not do

The City already ships **Know Your Zone** and a near-real-time clearing-progress map.
They answer *"has my street been cleared?"* — current state, single-point lookup, and
they answer it well.

> 🚫 Out of scope: clearing-status lookup maps, zone-lookup apps, live progress
> dashboards. They duplicate an official product and add nothing.

UOIP does the part the official tools do not: **retrospective, cross-source, accountable
operational analysis** — and the forecast that follows from it. Status answers *where is
my street*; this answers *was this winter handled well, and what should change*.

Also excluded, deliberately: weather prediction (forecasts are consumed, not modelled),
and any accountability claim below the neighbourhood level — analysis never descends to
an address, for privacy reasons that do not need re-litigating each time.

---

## 5. The record that does not exist yet

One dataset in this project is not an analysis. It is a **contribution**.

The City publishes address-level clearing status, and that dataset carries **no time
field at all**. It is overwritten in place, every day. Yesterday's picture of the city
is not archived anywhere — not by the City, not by the portal, not by anyone. It is
simply gone.

So UOIP takes a snapshot of it, every morning, and keeps it. Do that for a winter and
something exists that did not exist before: **a longitudinal record of how Winnipeg
actually clears itself, day by day**, against which any future claim about clearing
performance can be checked.

> ⏱ This is the one part of the project with a running clock. The archive can only ever
> be built **forward** from the day collection starts. Every day the collector is not
> running is a day of history that nobody — not this project, not the City, not a
> researcher in 2030 — can ever recover.

This is also why the platform is honest about a limit built into its own headline:
"clearing completion time" here means **the plow shift's completion time for a zone**,
not the moment any individual street was cleared. No dataset in the portal records the
latter. That absence is exactly what the snapshot archive is being built to end.

---

## 6. Why the unit is a snow event, not a complaint

A natural instinct is to count complaints. The data does not support it, and the design
says so out loud.

The supply side is **sparse by nature**: eleven years of records contain 49 parking bans
and only 19 city-wide residential plow operations. City-wide plowing is a rare, major
mobilisation, not a daily routine. And winter work is only **1.50%** of all 311 requests
— complaints measure *perceived* service gap; they are not the explanatory variable.

So the unit of analysis is a **snow event × zone**:

```
snowfall (cm)      →  ban issued?      →  shifts worked   →  requests raised
driver variable       decision variable   supply variable    outcome variable
daily, 18 years       49 bans             418 shift rows     ~220k geocoded rows
```

**Events are defined by the weather, not by the City's response.** Partitioning 18 years
of daily snowfall at a calibrated threshold yields events in the *hundreds*; defining
them by the City's response would leave 19. And the difference between the two — *it
snowed, but nothing was dispatched* — is not noise to be discarded. It is the supply-gap
measurement itself.

This is a standard **event-study** design, chosen because it survives the data's actual
shape rather than the shape one would prefer.

Modelling happens at **ward** level (15 units) and is allocated down to
**neighbourhood** (242 units) by historical share. Neighbourhoods are not modelled
directly: at 242 units the daily count averages ~0.34, so nearly every cell is zero and
a model that always predicted zero would score well while saying nothing.

---

## 7. How the score is built

The pipeline, the score and the attribution are **deterministic**. Two predictive models
sit on top and supply the forward-looking inputs:

```
Winter Operational Load Score (0–100)
  = 0.40 × predicted service requests   (forward-looking — model M1)
  + 0.30 × supply gap                   (covered by a shift, or not)
  + 0.30 × weather severity
```

The weights are initial values, calibrated against historical events. The demand term is
a *prediction*, which is what makes the score usable **before** a snowfall rather than
only after it.

| Model | Predicts | Unit | Baseline it must beat |
|---|---|---|---|
| **M1** | Service-request volume for the next snow event | ward × snow event | Seasonal naive (same ward, historical mean for comparable events) |
| **M2** | Time-to-close / overrun probability for one request | single request | Median duration for that priority tier |

Both are evaluated on a **temporal split** — earlier winters train, the most recent
held-out winter tests. Random splits are not used: with panel data and a long-term
volume trend, they leak. **Every reported model metric is paired with its baseline.** A
model number without its baseline is not a result.

### How to read any number this platform produces

The upstream data has measured, documented defects — 79% of 311 rows carry no
geography, the channel taxonomy broke in 2022, total request volume fell 66% from its
2013 peak and then recovered. None of these are pipeline bugs, and none of them are
hidden: each one changes how a result must be read, and each is written down with the
measurement that established it in **[Data Sources](data-sources.md)**. Read that page
before quoting a figure from this one.

---

## 8. Where the project stands

| | State |
|---|---|
| Bronze ingestion — clients, loaders, backfill, audit | Implemented and unit-tested |
| Winnipeg sources — 311, plow shifts, parking bans, zone boundaries | Registered and backfilling |
| Daily snapshot archive | Code complete; deployment is the item with the clock |
| MinIO storage environment | Not yet verified end to end |
| Silver, Gold, scoring SQL, dashboards | Not started |
| Prediction layer (M1, M2) | Not started; depends on Silver |

The authoritative, always-current status is the **Implementation status** section of
`CLAUDE.md` in the repository root. This table summarises it and can lag it.

---

## Next

- [Architecture](architecture.md) — how the layers and the two nodes fit together
- [Data Sources](data-sources.md) — the datasets, their measured sizes and their defects
- [Getting Started](getting-started.md) — install, configure, run

The developer-side breakdown — eight numbered business objectives, their acceptance
criteria and the measured evidence behind every figure quoted above — lives in
`docs/dev/requirements/` (written in Chinese).
