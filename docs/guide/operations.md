# Operations Runbook

## Daily schedule

| Time (UTC) | DAG | Layer |
|---|---|---|
| 06:00 | `dag_ingest_nyc_311`, `dag_ingest_open_meteo` | Bronze |
| 06:00 (1st of month) | `dag_ingest_nypd` | Bronze |
| 07:00 | `dag_silver_open_meteo` | Silver |
| 08:00 | `dag_audit_bronze` | Bronze integrity |

## Common situations

### A Bronze ingest DAG failed

Usually transient. The recovery ladder is automatic: 3 retries → `catchup=True`
replays the interval → `dag_audit_bronze` fills any remaining gap the next
morning. Intervene only if the audit DAG is also failing.

### The audit DAG is red

It found a gap it could not fill. Read the task log for the source and window,
then reproduce with a dry run:

```bash
python -m scripts.backfill.main --source <SRC-ID> --start <date> --end <date> --dry-run
```

If the dry run also fails, the upstream API is the problem, not the pipeline.

### A Silver partition came out empty

A Silver job raises when its output falls below the expected row-count baseline.
Zero rows usually means the Bronze partition is missing or the upstream API had
an outage. Check Bronze first; do not "fix" it by lowering the baseline.

### Code changes are not taking effect

Restart Airflow. The scheduler forks tasks from in-memory state, so pulling new
code is not enough:

```bash
docker compose -f infra/docker/docker-compose.yml restart airflow-scheduler airflow-webserver airflow-dag-processor
```

### Spark job fails with a Python version mismatch

Driver and executor must run the same Python. The relevant `--conf` settings and
the reason each one exists are documented in `dags/_spark_common.py`. Read that
file before changing Spark configuration.

## Cost controls

> **Do not run a bare `terraform apply`.** The plan's only pending resource is a
> Cloud Composer environment, which the project does not use and which bills
> roughly **$10/day**. Target resources explicitly:
>
> ```bash
> terraform apply -target=<resource>
> ```
>
> If Composer is ever provisioned deliberately, destroy it as soon as the work
> finishes: `terraform destroy -target=google_composer_environment.main`.

Other cost notes:
- Never run a compute cluster without auto-delete configured.
- Dry runs cost one storage read per slice and no writes.

## Escalate to a human when

- An upstream API schema changes (fields added, renamed, or removed).
- A Silver partition lands with 0 rows — possible upstream outage.
- Spatial attribution returns NULL for more than 10% of records.
- A cloud billing alert fires.

## Known open issues

| Issue | Impact |
|---|---|
| Warehouse dataset is declared in a different cloud project than every other resource | Writes will fail with a permissions error until the project is corrected; the fix forces a dataset replacement |
| No CI pipeline | Quality gates run only when someone runs `make lint` / `make test-unit` locally |
| Terraform state has no remote backend | State exists on one machine only |
| Raw-API schema validation missing | Config is validated; API responses are not |

Full detail, including the current handover context, lives in the developer
documentation under `docs/dev/`.
