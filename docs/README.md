# Documentation

Documentation comes in two kinds. They have different audiences and different
languages, and they are never mixed.

| Directory | Audience | Language | Content |
|---|---|---|---|
| **[guide/](guide/)** | Outside readers and users | English only | What the platform is, what it can do, how to run it, how to troubleshoot it |
| **[dev/](dev/)** | Developers | Chinese permitted | Requirements, architecture, decision records (ADRs), technical notes |

The root [README.md](../README.md) links only to `guide/`.

---

## guide/ — learn, try and understand the project

The English guide is for readers who have not worked on UOIP. It starts with the
project's purpose and a small runnable example, then offers conceptual explanations
and task-focused instructions. Technical lessons appear where they explain a
choice or help the reader use the system; the guide is not a chronological
account of development.

Read Overview for orientation, Getting Started for a hands-on introduction,
and Results for the evidence. Architecture and Scoring explain the mechanisms.
The remaining pages support particular learning or operating tasks.

| Document | Content |
|---|---|
| [Overview](guide/overview.md) | Problem, approach, key lessons and H1 delivery scope |
| [Getting Started](guide/getting-started.md) | Sample figures, a real source pull and deployment on your own standard components |
| [Results](guide/results.md) | Findings, interpretation limits, query links and dated build context |
| [Architecture](guide/architecture.md) | A record's path through the system, component responsibilities and engineering trade-offs |
| [Data Sources](guide/data-sources.md) | Registered inputs, semantics, collection scope and known limitations |
| [Scoring and Recommendations](guide/scoring-and-recommendations.md) | M1 evaluation, factors, scoring profiles and ranking interpretation |
| [Data Quality](guide/data-quality.md) | Build checks, independent audits, reconciliation and certification |
| [Ingestion and Bronze](guide/ingestion-bronze.md) | File format, manifests, source strategies and rerun behavior |
| [Silver ETL](guide/silver-etl.md) | Keys, local dates, spatial assignment and event rebuilding |
| [Backfill](guide/backfill.md) | Bounded historical loads, prerequisites and recovery |
| [Snapshot Collection](guide/snapshot-collection.md) | Independent collection, service setup and missing-observation handling |
| [Operations](guide/operations.md) | Schedules, analytical refresh order and troubleshooting |

The H1 scope is maintained in Overview and the measurement context in Results.
Other pages link to those baselines rather than copying a live implementation
status list. Commands describe repository interfaces; they do not imply every
infrastructure component is bundled or every dataset is included in the checkout.

## dev/ — developer documentation

These documents come in two natures: **evergreen** (they describe how things are
now, and are rewritten in place) and **event** (they describe a one-time event,
and are frozen once written — append, never revise). **Directories exist only for
things that grow.** Event documents accumulate monotonically, so each kind gets a
directory; evergreen documents other than requirements do not grow, so they sit
directly in `dev/`. The decision procedure and the writing contract are in
[dev/README.md](dev/README.md).

**Evergreen — what the system is now**

| Document | Content |
|---|---|
| [roadmap.md](dev/roadmap.md) | The target stack and the capability phases |
| [platform-architecture.md](dev/platform-architecture.md) | Layering intent, deployment topology, key design considerations |
| [data-volume-baseline.md](dev/data-volume-baseline.md) | Measured bytes per row and compression ratios — the basis for capacity planning |
| [requirements/project-overview.md](dev/requirements/project-overview.md) | Project positioning, business background, MVP scope |
| [requirements/business-objectives.md](dev/requirements/business-objectives.md) | BO-1 … BO-8, the prediction layer, acceptance criteria and known constraints |
| [requirements/winnipeg-data-sources.md](dev/requirements/winnipeg-data-sources.md) | Winnipeg data-source research (measured against the SODA API) — the evidence base for the two documents above |
| [requirements/data-source-portfolio.md](dev/requirements/data-source-portfolio.md) | Which sources are adopted, which are held for H2, and what activating a held source costs — the decision layer above the research |
| [requirements/metric-feasibility-audit.md](dev/requirements/metric-feasibility-audit.md) | Per-metric measured evidence: the number, the query that produced it, the verdict — and the two-level "source measured" / "metric measured" marking |
| [requirements/bo-conclusions-and-figures.md](dev/requirements/bo-conclusions-and-figures.md) | Per-BO conclusions read off the production Gold tables, and the figure catalogue they turn into — every row carries a number, its source query and the build it was measured on |

**Event — what happened (frozen once written, accumulating by directory)**

| Directory | Content |
|---|---|
| [adr/](dev/adr/README.md) | The trade-off behind one **decision** — a technology choice, a business-semantics call, or a working method; never renamed, never deleted |
| [design/](dev/design/README.md) | How one change is **intended** to be carried out |
| [launch/](dev/launch/README.md) | How one change **actually** went live |
| [postmortem/](dev/postmortem/README.md) | Post-incident reviews of failures that caused real impact |
| [archive/](dev/archive/README.md) | 🚚 **Temporary**: three obsolete documents awaiting migration to an external knowledge platform; the directory is deleted once they are gone. Closed — it accepts no new documents |

> `dev/notes/` was abolished on 2026-07-30. Its definition was a negative — "does
> not belong to any other category" — so it became a dumping ground for six
> documents of six different natures. `dev/architecture/` was abolished in the
> same pass: it held only two documents and would never grow, and two documents
> do not justify a directory. Where the six went is recorded in
> [dev/README.md](dev/README.md#附原-notes-六篇的去向).

The repository root additionally holds `CLAUDE.md` / `AGENTS.md` (binding
conventions shared by humans and AI agents) and `.claude/rules/backfill.md` (the
backfill layer architecture and the DAG inventory).

---

## Writing rules

- Directory names are semantic, never numeric prefixes. Numbers are only for ADR
  numbering.
- File names are always English kebab-case. **Language differences show up in the
  body text, never in the path.**
- A document belongs to exactly one kind: `guide/` teaches the project and its
  use to outside readers; `dev/` records detailed requirements, decisions and
  implementation evidence. Guide explanations summarize the relevant reasoning
  and link to the detailed public record.
- Every document must be linked from this index **exactly once**. Anything not
  linked should be deleted or moved to `dev/archive/`.
- Prefer merging over splitting. Target size ≈ 20 documents.
- Images go in `images/`; file names must not contain a city name.
- 🔴 **This repository is public-facing; private notes are not. The dependency
  runs one way only.** A private knowledge base may cite this repository; **this
  repository may never cite it** — no titles, paths or links to private
  documents, and no context that only holds over there (private
  correspondence, conversation records, unpublished third-party material,
  personal schedules). The test is one question: **could a reader who has
  access to this repository and nothing else verify this reference?** If not,
  it is a leak — even when it is only a title. The table below keeps that
  content *out*; this rule keeps references from *pointing at* it. A document
  that collapses once the private context is removed was never grounded here:
  rebuild its argument from what the repository already holds (a
  `dev/requirements/` entry, an ADR) instead of adding an outbound link.

### Four kinds of content that do not belong in this repository

They have a shorter lifespan than the documents that would hold them, and each
has a natural home elsewhere. Check against this table before writing; the
details and their corollaries are in
[dev/README.md](dev/README.md#二不要写进-design-doc-的东西).

| Content | Where it goes |
|---|---|
| Which files this change touched and how it was verified | **Pull request description** |
| Why this line is written this way, naming, a missing check | **Code review** |
| What this change did and why | **Commit message** |
| Progress, schedules, chasing, temporary blockers, status updates | **Ticket comment** (not in the git repository — maintained in another system) |
