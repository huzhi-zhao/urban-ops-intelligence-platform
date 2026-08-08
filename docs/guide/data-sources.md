# Data Sources

Every upstream source is registered twice: once machine-readable in
`config/sources/<slug>.yaml` (Pydantic-validated, read by the pipeline) and once here
for humans. **The YAML is authoritative** — if the two disagree, the YAML wins.

All datasets come from the **City of Winnipeg Open Data Portal**
(<https://data.winnipeg.ca/>), which runs on **Socrata** and is served through the SODA
API, plus **Open-Meteo** for weather. Licence: Open Government Licence – Winnipeg.

Figures marked **【measured】** come from real API calls (2026-07-29) and are
reproducible; the rest come from the portal's catalogue metadata.

---

## 1. The datasets

Grouped by the role each plays in the analysis, which is the only grouping that matters
when reading a result.

### Demand side — what citizens reported

| Dataset | ID | Size | Cadence |
|---|---|---|---|
| 311 Requests | `u7f6-5326` | **18,346,621 rows**【measured】, 2008-06-17 → today | daily |

The primary demand signal, and the backbone of BO-1 and BO-5. Three fields carry the
classification, and their real semantics are not what the names suggest:

| Field | What it actually is |
|---|---|
| `subject` | Business nature — 60.8% `Information Request` (phone enquiries, no dispatch), 38.6% `Service Request`, 0.5% `VOF` |
| `reason` | **The responsible department**, not the complaint reason (Water and Waste, Public Works, …) |
| `type` | The real ticket type — **3,563 distinct values**【measured】; the analysis grain |

> 🔑 The `type` string **embeds the City's own service standard**: `Snow Removal Street
> Priority 1 Reg`, `Sanding Request Priority 2 After`, `Snow Removal High Piles Pr 2`.
> Priority tier (P1/P2/P3) and shift (regular / after hours) are encoded in the text.
> That is what makes the SLA audit (BO-5) possible against the official standard instead
> of an invented one.

Known issues:

- **Geography is missing on 79% of rows.** Only 20.9% carry `neighbourhood` / `ward` /
  `geometry` — but 80.1% of *winter* rows do, because enquiry calls have no address and
  only real dispatches carry a location. Spatial analysis therefore has an effective
  denominator of ~3.8M rows, and any spatial-join alert must use the geocoded subset as
  its denominator or it will fire permanently.
- **Channel taxonomy broke in 2022.** `Self Service`, `Mobile` and `SMS In` all went to
  zero while `VOF` rose 23×. This is a recording-practice migration, not a behaviour
  change: tickets were relabelled, not lost. Silver must normalise
  `Self Service + Mobile + SMS In → VOF`, and channel-mix comparisons across 2022 are
  invalid.
- **The 3,563 `type` values need a normalisation dictionary** — `Pr 2` / `Priority 2` /
  `P2` all occur, plus `_vof` suffixed variants.
- **Late-arriving updates** — `closed_date` is filled in days after creation, so
  ingestion uses a 7-day lookback window and downstream layers must deduplicate.
- **`closed_date` semantics are unverified** — "ticket closed" or "street actually
  cleared"? Until confirmed, SLA conclusions carry that caveat.
- Socrata **omits keys entirely for null values** rather than sending `null`. Never
  assume a field is present on a given record.

### Supply side — what the City actually did

| Dataset | ID | Size | Notes |
|---|---|---|---|
| Plow Zone Schedule | `tix9-r5tc` | **418 rows**【measured】 — 19 plow events × 22 zones, 2015-12 → 2026-02 | **A schedule, not an execution log.** `shift_number` — the batch a zone is assigned to — is the usable quantity |
| Snow Parking Bans | `mfzv-893p` | **49 rows**【measured】 | The City's decision log: when snowfall was judged severe enough to declare a ban |
| Snow Clearing Status | `g3p4-h83y` | **237,867 rows**【measured】 | Address-level cleared/not-cleared flags. **No time field of any kind** — see below |
| Plow Zones | `39ur-higg` | **82 MultiPolygons / 25 zone values**【measured】 | Zone boundary geometry, for spatial attribution |

`mfzv-893p.id` ↔ `tix9-r5tc.snow_ban_id` links the decision to the work done.

> 🔴 **`tix9-r5tc` records a plan, and the difference matters for every number read
> off it.** Each of the 19 events covers exactly 22 zones over a fixed 60-hour window,
> split into five 12-hour shifts — and every zone within one shift carries the *identical*
> `shift_start` / `shift_end`. So there is no per-zone completion time, no per-zone
> duration, and no "zone that was skipped": those quantities have zero variance or are
> constantly false. What the table does support is the **order** zones are scheduled in,
> which is stable across ten years. See
> `docs/dev/adr/0008-plow-schedule-is-a-plan-not-a-record.md`.

> ⚠️ The boundary table carries **25** zone values while the schedule carries **22**.
> The three extras (`B/D`, `X`, `Downtown` — 6.0% of addresses) have no schedule rows at
> all. **No record is not "scheduled last"** and they must be excluded explicitly rather
> than propagated as NULL.

> ⚠️ The supply side is **sparse by nature** — 19 residential plow events in 11 years.
> That is a business reality, not missing data, and it is why the unit of analysis is a
> snow event × zone rather than a complaint. See [Overview](overview.md) §3.

> 🚨 `g3p4-h83y` is **overwrite-in-place with no history**. `has_street` / `has_alley` /
> `has_walk` describe the *current* state and nothing else. The only way to obtain a time
> series is to snapshot it daily ourselves — which is why it is registered with
> `partition_strategy: snapshot` and collected by a dedicated timer. A day not collected
> is permanently lost. See [Snapshot Collection](snapshot-collection.md).

### Driver side — weather

| Source | Access | Coverage |
|---|---|---|
| Open-Meteo Archive & Forecast | REST, no auth | Daily grain, 18 years of history, verified【measured】 |

Sampled at **one point per plow zone** (zone centroids), not a single city-wide point.
A single point is spatially constant, which makes "did the zones scheduled last simply
get more snow?" unanswerable — and that question has to be answerable for any
scheduling-order result to stand. Fields used: `snowfall_sum` (cm) and
`temperature_2m_min` / `temperature_2m_max` (°C). The free tier allows <10,000
requests/day. One wide call per window, not one call per day.

Known issue: **forecasts change over time.** Bronze partitions by the fetch date so every
forecast snapshot is preserved, and the Silver job uses a 7-day sliding window so later
corrections are absorbed.

### Beyond the portal

| Source | Role |
|---|---|
| **Statistics Canada census** (Dissemination Area level) | Population, age, language and income per DA. Two jobs: it turns the 311 reporting bias from a caveat into something measurable, and it is the socio-economic control for any statement about scheduling order |

311 complaint density is a function of **who reports**, not of how bad the street is —
communities that know how to use 311 report more, and the same bad street generates fewer
calls where residents are older, newer to the country, or less comfortable in English.
Saying so in a disclaimer does not change a conclusion's strength; an external baseline
does. That is the whole reason this source is in scope.

Census years (2021 / 2016 / 2011) do not line up with the analysis window (2015 →), and
that mismatch is stated rather than smoothed over.

### Held for later

These are **not** ingested today. They are catalogued — endpoint, size, activation cost
and the probes to run first — in `docs/dev/requirements/data-source-portfolio.md`, so that
picking one up later does not mean redoing the research.

| Dataset | ID | Why it is worth keeping on the list |
|---|---|---|
| WFPS Call Logs | `yg42-q284` | 1,323,967 rows with `Motor Vehicle Incident = YES` and its own neighbourhood/ward labels — an outcome variable with **no reporting bias**, unlike 311 |
| Road Network | `ngsx-caav` | Street kilometres per zone: a second normalisation denominator and a second control on scheduling order |
| LRS Block Segments | `sr8r-ehr3` | Street classification — the objective handle on whether the City's own P1/P2/P3 policy was actually followed |
| Midblock Traffic | `bh78-7qpb` | Speed and volume every 15 minutes. ⚠️ **Two-month rolling window, no history** — usable only if collected forward, exactly like the snapshot archive |
| Cost of Road Maintenance | `rsyj-x68c` | Budget vs service level. ⚠️ The JSON endpoint returns an empty object; a CSV export is the likely workaround |

> One thing worth stating plainly: **no dataset on the portal removes the supply-side
> ceiling.** Nineteen city-wide plow events in ten years is a business reality, not
> missing data. The only way past it is the snapshot archive being built forward — see
> [Snapshot Collection](snapshot-collection.md).

Datasets considered and set aside — building permits, mosquito traps, tree inventory,
police statistics — are catalogued with sizes and rationale in
`docs/dev/requirements/winnipeg-data-sources.md`.

---

## 2. What is registered in code today

Source ids are literal values from `config/sources/` and appear in every storage path.
**Only ids that exist in the repository are listed** — the remaining datasets get their
id when their YAML is added.

| Source ID | Datasets | API | Partition strategy | Priority |
|---|---|---|---|---|
| `SRC-WPG-SNOW` | `snow_clearing_status` (`g3p4-h83y`) | Socrata (SODA) | `snapshot` | P0 |

Everything else in §1 is pending registration. The Open-Meteo client already exists and
carries over unchanged — only its coordinates differ.

> The repository also still contains source YAML, DAGs and Spark jobs from an earlier
> deployment against another city's open data. They are being retired and are not part of
> this deployment; ignore them when reading this manual.

---

## 3. Bronze scope vs analysis scope

These are **different on purpose**, and the difference is a design decision rather than
an inconsistency:

| Layer | Scope | Why |
|---|---|---|
| **Bronze** | All 18.3M 311 rows, immutable, unfiltered | The engineering problems worth solving — a 3,563-value type dictionary, 79% missing geography, taxonomy drift — only appear at full volume |
| **Silver** | Cleansed, channel-normalised, UTC, geo-availability flagged | Full volume, or a winter slice |
| **Gold** | Modelled to the question. The winter load score needs only the winter slice + weather + shifts | Analysis scope is set by the question, not by ingestion |

Ingesting everything is a genuine engineering contribution; focusing the analysis on
winter is a demonstration application. Both statements are true at once.

---

## 4. Adding a source

1. Add `config/sources/<slug>.yaml`. Pick the partition strategy that matches the
   dataset's shape — see [Ingestion & Bronze](ingestion-bronze.md) for the four
   strategies and their path layouts. `daily` requires a `timestamp_field` on every
   dataset; `snapshot` is the only strategy that permits `timestamp_field: null`.
2. Add the dataset to the tables above.
3. Add `scripts/backfill/backfill_<slug>.py`. It is auto-discovered — no registry edit
   needed. See [Backfill](backfill.md).
4. Add the ingestion DAG under `dags/` — or a timer unit, for a snapshot source.
5. Add the data contract under `contracts/`.

## 5. SODA API notes

```
https://data.winnipeg.ca/resource/<dataset_id>.json?$limit=1000&$offset=0
```

Two traps worth carrying forward:

- `$group` queries default to a 1,000-row cap. Use `count(distinct ...)` when you want a
  true cardinality — a `$limit=2000` silently truncated the 3,563 `type` values during
  profiling.
- Filtering `type` on `%ICE%` also matches `Serv-ice`, `Pol-ice`, `Not-ice` and
  `Invo-ice`. A careless match put winter's share of 311 at 10.40%; the correct patterns
  put it at **1.50%**.

Paging must always be combined with `$order`, or rows are skipped. `SOCRATA_APP_TOKEN`
raises the rate limit and is recommended for backfill.

## Related

- [Overview](overview.md) — what these datasets are used for
- [Ingestion & Bronze](ingestion-bronze.md) — how they land in storage
- `config/sources/README.md` — the YAML schema and its validation rules
