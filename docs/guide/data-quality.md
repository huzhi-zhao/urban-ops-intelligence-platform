# Data Quality

UOIP checks both whether work ran and whether its output supports the next step.
Those are different questions. A successful query can return misleading data;
a failed query tells us that a check could not be completed.

This distinction grew out of a concrete ingestion failure and now runs through
build gates, scheduled audits and published result metadata.

## The count that looked correct

During the historical 311 load, unordered API pagination repeated rows at page
boundaries and omitted others. On one affected day, Bronze and the upstream both
contained 3,585 records. One record was duplicated and another was missing, so
the counts agreed while the contents were wrong.

The repair combined two checks: primary-key uniqueness within a shard and row
counts against the upstream. Their combined evidence identified **55 affected
days** out of 4,878. Re-pulling those days with stable ordering corrected the
records, followed by downstream rebuilding. The
[pagination incident](../dev/postmortem/bronze-socrata-pagination-incident.md)
records the measurements and repair.

A checksum would not have caught the missing row either: it faithfully describes
the payload that was actually collected. Each check needs a stated question and
a stated blind spot.

## Checks at different boundaries

| Boundary | What is checked | What passing does not establish |
|---|---|---|
| Bronze manifest | Expected object coverage and collection metadata | Record completeness or key uniqueness |
| Bronze content | Composite-key uniqueness and upstream count reconciliation | Every unchanged count has unchanged values |
| Silver transformation | Types, keys, rejected rows, output floors and spatial coverage | Business claims are valid |
| Gold build | Table grains, relationships, ranges and expected panel shapes | Model skill or fairness |
| Independent audit | Layer checks, cross-layer reconciliation and freshness/consistency rules | That unchecked properties hold |
| Figure export | Query output, captions and attached certification | A rendered chart or a certified build merely because export succeeded |

The declared Gold schema checks include 185 assertions in the v1.0 checkpoint.
The independent audit has its own configuration and cadence, so its count is
not expected to equal 185. See [rules](../../config/dq/rules.yaml),
[assertion runner](../../scripts/gold/dq_assertions.py), and
[the schema checkpoint](../../CHANGELOG.md).

## Use the right denominator

A spatial hit rate should ask how many **located** records matched a boundary.
It should not treat the much larger population with no upstream coordinates as
failed joins. Likewise, Bronze–Silver reconciliation must use the project's
actual collection windows rather than the portal's all-time total.

Missing scheduling evidence also needs an explicit state. A complete 1,298-row
score panel contains 924 `partial_no_rank` rows; row count alone cannot establish
that all three factors are present. [Scoring and Recommendations](scoring-and-recommendations.md)
explains how that missingness changes the result.

## Understand certification

`gold_certification` records a verdict for a specific audit run:

| Status | Meaning |
|---|---|
| `certified` | Checks in that run executed and no error-level finding was recorded; warning-level findings may remain |
| `suspect` | Checks executed and at least one error-level finding was recorded |
| `unknown` | No observations were recorded, or one or more checks could not execute |

`unknown` takes precedence when an audit has both a finding and an unexecuted
check. The system cannot claim the observed finding is the whole story.
The [certifier](../../scripts/dq/certify.py) implements this rule.

Certification is a summary of the observed audit log, with its cadence and
coverage. It is not proof of scientific validity, a promise of zero warnings,
or a perpetual approval of future data. Check source freshness, build order and
whether the audit corresponds to the result you are using.

## Inspect a build

Use your own configured Trino deployment; see
[connection settings](getting-started.md#configure-the-project). Build assertions
and audits serve different purposes:

```bash
make gold-assert
make dq-audit CADENCE=daily
```

The audit reads business data and appends observations to `uoip_meta.dq_audit_log`.
Note the `run_id` it reports. Use that same ID for the scorecard and verdict:

```bash
make dq-scorecard RUN_ID="$AUDIT_RUN_ID"
make dq-certify RUN_ID="$AUDIT_RUN_ID" DRY_RUN=1
```

Set `AUDIT_RUN_ID` to the reported value first. The scorecard reads existing
observations; the certification dry run prints a verdict without appending it.
Remove `DRY_RUN=1` when you intend to record a certification row.

Choose `CADENCE=weekly` or `CADENCE=manual` when those configured checks are
needed. A daily run should not be described as executing every possible rule.
The Airflow audit DAG runs the audit, scorecard and certification tasks with a
shared run ID.

**Read the verdict, not just the exit code.** Audit findings do not necessarily
fail the task. The certifier exits successfully for `certified`, `suspect` and
`unknown`, because each is a valid conclusion about the evidence. Conversely,
an unexecuted audit check causes the audit runner to fail.

## Check labels as well as values

An executable query can faithfully return a mislabeled measure. The current
zone–ward figure captions describe area shares, while the producing crosswalk
SQL computes winter-request shares. Numeric gates do not resolve that semantic
conflict. [Results](results.md#a-work-zone-is-not-a-ward) explains the supported
reading and the remaining caption issue.

## Keep evidence with a result

Before exporting figures, inspect the relevant audit, data watermark, model
version and build timestamps. `make eda-export` attaches certification metadata;
it does not turn a suspect or unknown dataset into a certified one.

Keep the query, exported JSON, caption, interpretation limits and repository
revision together. If a rebuild changes an old number, compare those artifacts
before deciding whether the cause is upstream revision, scope, rule changes or
a defect. The dated baseline is in [Results](results.md#data-and-version-context).

For recovery actions, continue to [Operations](operations.md).
