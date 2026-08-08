# Overview

The **Urban Operations Intelligence Platform (UOIP)** answers one question a winter city
cannot currently answer about itself: *when it snows, which parts of the city wait
longest for service — and is that wait explained by the weather, or by the running
order?*

It is a data platform that joins what citizens reported, how the City scheduled its
crews, and what the sky actually did, and turns the three into a **Winter Operational
Load Score** per zone per snow event, with a ranked recommendation for the next one.

It is built to be **checked**, not believed: every figure below comes from a public
dataset, every query is published, and anyone can re-run the whole thing.

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
2. **Why was it heavy** — was it the snowfall, or was this a zone that sits near the
   back of the running order every time?
3. **What should change before the next snowfall** — within the residential tier,
   which zones move up?

None of these is an exotic analytical question. A city that could answer them would
plan differently. Today, nobody can answer them — not because the data is secret, but
because it has never been put in one place.

---

## 2. Why nobody can answer it today

The evidence exists, in three piles, and the piles do not touch.

| The signal | Where it lives | What it alone can tell you |
|---|---|---|
| **Demand** — what residents reported | 311 service requests, 18.3M rows since 2008 | That people complained. Not whether they were right |
| **Supply** — how the City scheduled its crews | Plow shift schedules and parking bans | The running order for one operation. Not whether that order is unusual |
| **Driver** — what the weather did | Daily snowfall, 18 years | That it snowed. Not what it cost |

Three separate endpoints, three separate release cadences — and, decisively, **three
geographies that do not nest inside one another**. A plow zone is not a ward. A ward is
not a neighbourhood. A complaint arrives tagged with one of them, a shift record with
another, and no official crosswalk connects them. That single misalignment is why the
join has not simply been done by somebody with a spreadsheet, and building it is one of
the platform's load-bearing pieces of work.

Put the three together and one specific sentence becomes measurable for the first time:

> **Same snowfall. Some zones start plowing on day one, and some wait about
> twenty-six hours longer — and it has come out the same way for ten years.**

That sentence — the running order, held up against ten winters and against how many
addresses are doing the waiting — is the core of everything below. It cannot be seen in
any of the three sources on its own.

> ⚠️ **An earlier version of this page said something stronger and wrong.** It claimed
> the platform could measure *"it snowed here, and no crew was sent."* Testing the
> schedule dataset in August 2026 showed that claim cannot be made: every one of the 19
> recorded operations covers all 22 zones, and the dataset only records **city-wide
> residential plow operations** in the first place — routine sanding and main-road
> maintenance never appear in it. Silence in that table is not evidence that nobody
> came. The claim was retired; the running order replaced it. See
> [Data Sources](data-sources.md) for the measurement.

---

## 3. What UOIP puts on the desk

Three things, in plain terms.

**A score.** For every zone in every snow event, a 0–100 **Winter Operational Load
Score**: how hard this zone was hit relative to what was done about it. Normalised by
the number of addresses in the zone, so a large zone does not score high merely for
being large.

**A reason.** The score is decomposed, never a black-box number. Every score comes with
its drivers: how much of it was snowfall, how much was a late position in the running
order, how much was demand.

**A recommendation.** A ranked list of which residential zones should move up before the
next snowfall — driven by a *forecast* of demand, not just a record of the last storm,
and traceable back to the evidence that produced it.

Illustrative shape of the output — **not real results**, the scoring layer is still
being built:

| Zone | Event | Score | Leading driver | Recommendation |
|---|---|---|---|---|
| Zone A | 2024-01-12, 18 cm | 87 | Scheduled in the last shift, as usual | Move earlier in the running order |
| Zone B | 2024-01-12, 18 cm | 61 | Heavy snowfall, mid-order | Hold; monitor |
| Zone C | 2024-01-12, 18 cm | 24 | Early in the order, low demand | No change |

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
my street*; this answers *was this winter handled evenly, and what should change*.

That is not a gap in the City's technology. It is a gap in **standpoint**: no operating
agency produces the accountability baseline for its own performance, and it should not
be expected to. That work has always come from outside — reporters, researchers, civic
groups. This platform is one of those.

Three things are deliberately excluded.

**Weather prediction.** Forecasts are consumed, not modelled.

**Anything below the neighbourhood level.** Analysis never descends to an address, for
privacy reasons that do not need re-litigating each time.

**The priority tiers themselves.** Which street classes get cleared first — main routes,
then collectors, then residential — is **policy**, set in public and carrying statutory
time limits. UOIP does not second-guess it. Everything here operates *inside* the
residential tier, on the ordering that policy leaves open.

And one thing this platform does **not** claim: that a late position in the running
order is unfair. There may be perfectly good reasons for it — road kilometres,
equipment routing, geography. What can be said is narrower and, we think, more useful:
the ordering is **stable, systematic, and ten years old, and no public document explains
how it was set.** The contribution is making that discussable, not settling it.

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

This is also the absence that shapes everything else on this page. The portal has no
record of when clearing *finished* — not per street, and, it turns out, not per zone
either. The shift dataset is a **plan**: fixed 60-hour windows, five 12-hour shifts, all
zones in a shift stamped with the same start and end time. It says when work was
*scheduled*, never when it was done.

So this platform measures what the data actually contains — **the order zones are
scheduled in** — and says so plainly rather than dressing a plan up as a record. Ending
that absence is exactly what the snapshot archive is for.

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
snowfall (cm)      →  ban issued?      →  scheduling order  →  requests raised
driver variable       decision variable   supply variable      outcome variable
daily, 18 years       49 bans             418 shift rows       ~220k geocoded rows
```

**Events are defined by the weather, not by the City's response.** Partitioning 18 years
of daily snowfall at a calibrated threshold yields events in the *hundreds*; defining
them by the City's response would leave 19.

The difference between the two is **not** read as "it snowed and nothing was
dispatched". That reading was tried and abandoned: the schedule dataset covers only
city-wide residential plow operations, so its silence says nothing about sanding, main
roads or local clearing. All the difference does is bound where the ordering measure is
defined — inside those 19 operations, and nowhere else.

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
  + 0.30 × scheduling position          (how late in the running order this zone sits)
  + 0.30 × weather severity
```

The weights are initial values, calibrated against historical events. The demand term is
a *prediction*, which is what makes the score usable **before** a snowfall rather than
only after it.

The middle term is the one that changed. It began as a *supply gap* — was a crew sent
at all — and that turned out to be unmeasurable and, worse, quietly false: all 19
recorded operations cover all 22 zones. It is now the **scheduling position**, which the
data does support and which, measured across ten winters, is strikingly stable:

| | Mean shift position over 19 operations |
|---|---|
| Earliest-scheduled zone | **1.26** |
| Latest-scheduled zone | **3.47** |

Roughly 26 hours apart, every time, for a decade. And the waiting is not spread evenly
over the city: the correlation between how late a zone is scheduled and how many
addresses it contains is **r = +0.49** — the zones that wait longest are also, broadly,
the zones with the most people waiting.

Outside those 19 operations the term is **NULL**, not zero. Absence of a schedule record
is not evidence of anything, and the pipeline is built to refuse that inference rather
than to fill it in.

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

Two more, specific to the numbers above:

**Complaint density measures who complains.** Communities that know how to use 311 file
more; communities that are older, newer to the country, or less comfortable in English
file less — for the same street conditions. This is the standard civic-tech bias, and it
is why complaint maps here are used to *raise* the question and never to answer it. The
answer comes from the supply side, which does not depend on anyone picking up a phone.

**The address counts are a current snapshot.** They are used to normalise events going
back to 2015, and the city has grown since. The effect is small, but the figure is not
a historical one and is not presented as such.

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
