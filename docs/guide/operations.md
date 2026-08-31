# Operations Runbook

What runs when, what to do when it does not, and when to stop and ask a human.

## Daily schedule

| Time | Job | Where | Layer |
|---|---|---|---|
| 06:30 | Snapshot collection | Storage node timer | Bronze |
| 06:00 | Bronze incremental ingestion | Airflow | Bronze |
| 07:00 | Silver ETL | Airflow | Silver |
| 08:00 | `dag_audit_bronze` | Airflow | Bronze integrity |

Monthly sources run on the 1st. Backfill DAGs are manual and never scheduled.

## Common situations

### A Bronze ingest DAG failed

Usually transient. The recovery ladder is automatic: 3 retries → `catchup=True` replays
the interval → `dag_audit_bronze` fills any remaining gap the next morning. Intervene only
if the audit DAG is also failing.

### The audit DAG is red

It found a gap it could not fill. Read the task log for the source and window, then
reproduce with a dry run:

```bash
python -m scripts.backfill.main --source <SRC-ID> --start <date> --end <date> --dry-run
```

If the dry run also fails, the upstream API is the problem, not the pipeline.

### A snapshot day is missing

**This one does not self-heal, and nothing can recover it.** The audit job reports missing
`ingest_date=` partitions but never refills them — writing today's data into yesterday's
partition would fabricate history rather than repair it.

Confirm the timer and the external watchdog are both alive so the *next* day is not lost
too, then record the gap. See [Snapshot Collection](snapshot-collection.md) §5.

### A Silver partition came out empty

A Silver job raises when its output falls below the expected row-count baseline. Zero rows
usually means the Bronze partition is missing or the upstream API had an outage. Check
Bronze first; do not "fix" it by lowering the baseline.

### Code changes are not taking effect

Restart Airflow. The scheduler forks tasks from in-memory state, so pulling new code is not
enough:

```bash
make stack-restart-airflow
```

### Spark reads garbled rows

An object was written without the `.gz` extension. Spark's `s3a://` reader chooses its
codec from the file extension and ignores HTTP headers, so a mislabelled gzip object is
read as text and **does not raise**. Check the object name, not the job.

### Spark job fails with a Python version mismatch

Driver and executor must run the same Python. The relevant `--conf` settings and the
reason each one exists are documented in `dags/_spark_common.py`. Read that file before
changing Spark configuration.

### Spark fails with `NoSuchMethodError` on S3 access

The `hadoop-aws` jar version must match the Hadoop version bundled with Spark exactly —
Spark 3.5.1 ships Hadoop 3.3.4, so `hadoop-aws:3.3.4` + `aws-java-sdk-bundle:1.12.262`.
One minor version off produces this error. Pin exact versions with `--jars`, never
`--packages`.

## Resource limits

There is no metered billing to control — the constraint is fixed capacity.

| Resource | Budget | Notes |
|---|---|---|
| Storage | 90 GB usable, ~10 GB/year measured | ~9 years of headroom. Not a constraint |
| Compute-node memory | 24 GB total, ~8 GB free with current services | **The one real constraint.** Compute a memory budget before adding any service |
| Upstream rate limits | Socrata per-token | Backfill parallelism defaults to 4 (daily) / 2 (monthly). Raise it only with an app token |

The storage node has **no backup**. That is a deliberate, recorded decision — see
[Architecture](architecture.md) §4. It also means the snapshot archive is the one thing on
this system whose loss is unrecoverable.

## Escalate to a human when

- An upstream API schema changes — fields added, renamed, or removed.
- A Silver partition lands with 0 rows: possible upstream outage.
- Spatial attribution returns NULL for more than 10% of records **that carry geographic
  information**. The denominator matters: 79% of 311 rows have no coordinates upstream, so
  a whole-table threshold fires forever and teaches everyone to ignore the alert.
- A snapshot collection fails, or a day's `ingest_date=` partition is missing. It cannot
  be re-collected.

## Known open issues

| Issue | Impact |
|---|---|
| The integration test suite has never been run as a suite | `tests/integration/` (12 tests) skips when `S3_*` is unset. The storage path itself is verified by production traffic, so this is suite coverage, not a question of whether it works |
| Raw-API schema validation missing | Source configuration is validated; API responses are not |
| The most recent day or two of 311 data is always thin | Upstream publishes with roughly a day's lag. This is steady state, not a gap — do not chase it |
| No dashboard yet | Superset is deployed but the operations dashboard is not built, so Gold is currently queried directly through Trino |

Design intent behind each layer lives in the developer documentation under `docs/dev/`.
Current implementation status is tracked in `CLAUDE.md`.
