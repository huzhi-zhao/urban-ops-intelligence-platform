---
name: bq-converge
description: Run one iteration of the BQ hypothesis-validation loop (state hypothesis → data discovery → metric feasibility → technical convergence → stakeholder sign-off) for a single business question, or re-enter the rework loop after a sign-off is rejected or new evidence surfaces. Use when the user says "推进 BQ", "对 BO-N 做新一轮收敛", "stakeholder 没通过，重新走一轮", "把这个 BQ 收敛一下", or names a BQ/BO id together with a feasibility or wording problem.
---

# BQ hypothesis-validation loop

A BQ (business question) is a hypothesis, not a requirement: it is validated
against data or it is redefined or killed. This skill runs one iteration of
that loop for one BQ and stops at the one gate a human must clear.

```
2 State BQ as hypothesis ──► 3 Data discovery & sampling
        ▲                              │
        │                              ▼
        │                    4 Metric feasibility assessment
        │                    (availability/quality/validity/usefulness)
        │                    verdict: go | redefine | kill
        │                              │ go
        │                              ▼
        │                    5 Technical convergence (data team)
        │                              │
        └────── rejected / new evidence ── 6 Stakeholder sign-off ──► done
```

Stage 1 ("business analysis" — strategy, decision owner, success criteria) is
a one-time, per-BO exercise, not per-loop. This skill starts at stage 2.

## Project wiring (this repo)

Do not invent a parallel `docs/bq/` tree. The state already lives in two
existing files, per the docs-conventions rule that each topic has exactly one
home:

| Role | File |
|---|---|
| BQ definitions (the hypothesis text, decision/action it drives) | `docs/dev/requirements/business-objectives.md`, one `## BO-N` section per BQ |
| Feasibility ledger (measured numbers, verdicts, probe entry points) | `docs/dev/requirements/metric-feasibility-audit.md`, one row per metric under a BQ |
| Design rationale for the feasibility probes | `docs/dev/design/20260808-metric-feasibility-probe.md` |
| Probe scripts (stage 3/4 tooling) | `scripts/analysis/<probe>.py`, shared fetch/cache in `scripts/analysis/_probe_common.py` |
| Definitional decisions that survive a BQ redefinition | an ADR in `docs/dev/adr/` (e.g. ADR 0008, ADR 0009) |

Reusing this skill in another project: change only those five path bindings
at the top of your working notes; the stage logic and gates below do not
change.

## Invocation forms

- `BQ-only` (e.g. "推进 BO-3"): first pass through the loop, or continuing
  from wherever the ledger says it stopped.
- `BQ + rejection` (e.g. "BO-3 stakeholder 说滚动累积窗口没有依据"): a rework
  loop. **Re-entry point is stage 2, not stage 1** — the rejected wording or
  the new evidence is new information about the hypothesis, not a reason to
  redo the business case. Read the rejection as a new falsification attempt
  on the current hypothesis text.
- `BQ + new evidence` (e.g. "任务 X 的探针发现了一个新的反例"): also re-enters
  at stage 2 — evidence changes the hypothesis before it changes the metric
  definition.

## Procedure

1. **Locate state.** Read the BQ's `## BO-N` section in `business-objectives.md`
   and every row tagged `BO-N` in `metric-feasibility-audit.md`. Read the
   linked ADR if one exists for this BQ. Do not trust memory of prior
   sessions — the ledger is the single source of truth for what has already
   been measured.

2. **Stage 2 — restate the hypothesis.** Write down: what is being claimed,
   and *who does what differently, at what point, because this number came
   out one way vs. another*. If that question has no answer, the
   recommendation is `kill`, and the loop ends here — say so plainly and stop
   for human confirmation before touching any file. This is the same
   decision-action filter the project's `BO和BQ的区分` note calls the real
   difference between a BO and a BQ.

3. **Stage 3 — data discovery & sampling.** Identify which source(s) back the
   revised hypothesis (`config/sources/*.yaml`, `contracts/api-contracts/`).
   Reuse `scripts/analysis/_probe_common.py`'s cached weather archive / zone
   index rather than re-fetching. If a new probe is needed, it is a new
   module under `scripts/analysis/`, read-only against public APIs, mirroring
   the existing ones — never a one-off script outside that directory.

4. **Stage 4 — feasibility assessment.** All four axes must independently
   pass; any single failure has a fixed disposition, do not blend them:

   | Axis | Failure means | Disposition |
   |---|---|---|
   | Availability | can't be computed | pause — need a new/better source |
   | Quality | result isn't trustworthy | pause — fix upstream data first |
   | Validity | the metric doesn't measure what the BQ claims | redefine |
   | Usefulness | computable and valid, but no one acts on it | do not ship |

   Run (or re-run with new parameters) the probe script, then append/update
   the row(s) in `metric-feasibility-audit.md` with the measured number, the
   rerunnable query, the date, and one of `成立 / 需改口径 / 不成立 / 未测 /
   已放弃`. Never write a conclusion without a number — an untested metric is
   `未测`, not "probably fine", and per that file's own discipline it cannot
   carry P0 or BO-6 weight while untested.

   ⚠️ Running a new probe costs compute/API calls against public endpoints —
   confirm with the user before adding a new probe module, not before
   re-running an existing one with a cheap flag change.

5. **Stage 5 — technical convergence.** This is the data team's own call, no
   stakeholder needed: resolve wording ambiguity, pick the threshold/window/
   grain the stage-4 numbers actually support, and draft the redline to the
   `## BO-N` section of `business-objectives.md`. Show the diff. Do not apply
   it yet.

6. **Stage 6 — stakeholder sign-off (human gate).** Present: the restated
   hypothesis, the feasibility verdicts with their evidence rows, and the
   proposed redline. Ask explicitly for go / reject-with-reason. Do **not**
   edit `business-objectives.md`'s committed text without an explicit yes —
   this file is the "single source of truth" the flowchart's stage 6 refers
   to, and an unconfirmed edit defeats the gate that stage exists for.

   - **Sign-off given:** apply the redline, update the feasibility ledger
     rows to their final verdict, and note the convergence round inline
     (e.g. "2026-08-09 收敛，取代 2026-08-07 版本") so a future rework loop
     has history instead of re-deriving it.
   - **Rejected:** record the rejection reason next to the BQ (in the BO-N
     section or as a new ledger row), then re-run this procedure from stage 2
     with that reason as input. Do not restart at stage 1.

## Human gates — never cross these autonomously

- Any `kill` verdict.
- Adding a new probe module (new compute/API cost).
- Stage 6 sign-off itself — only the human can accept the redline.

## What this skill deliberately does not do

- It does not create a `docs/bq/_registry.md` or per-BQ file tree. The ledger
  table already is the registry; a second one would violate the "one file,
  one topic, referenced exactly once" documentation rule and would drift.
- It does not touch Silver/Gold code. Stage 4→5 findings only ever change
  `business-objectives.md` prose and the feasibility ledger. Downstream
  modeling changes are a separate, later task once the BQ is signed off.
