# AGENTS.md — NYC-UOIP AI Agent Conventions

> Shared by all AI coding agents (Claude Code, GitHub Copilot, Cursor, Codex, etc.).
> Tool-specific overrides live in their own files (CLAUDE.md, .cursorrules, etc.).
> Claude Code reads this file via `@AGENTS.md` import in CLAUDE.md.

---

## Project summary

**Repo**: nyc-uoip
**Purpose**: Production-grade Lakehouse pipeline. NYC Open Data → Bronze/Silver/Gold
layers → daily Operational Load Score per Borough → resource allocation recommendations.
**Language**: Python 3.11+, SQL (**Trino dialect only** — pinned in `.sqlfluff`)
**Package manager**: uv (lockfile at `uv.lock`)
**Test runner**: pytest (`make test-unit` for unit, `make test-integration` for full stack)

---

## Before writing any code

1. Read the relevant `contracts/` file for the dataset you are touching.
   Source IDs: `SRC-NYC-311` (311), `SRC-NYPD` (NYPD), `SRC-Open-Meteo` (weather),
   `SRC-DCP` (borough GeoJSON).
2. Check `spark/schemas/` for the Silver StructType before writing transform logic.
3. Check `sql/ddl/` for the Gold table definition before writing DML.
4. Never assume field names from memory — verify against `contracts/api-contracts/`.

---

## Code quality gates (all must pass before task is complete)

```bash
make lint          # ruff (Python) + sqlfluff (SQL) — zero warnings
make test-unit     # all unit tests green
```

If you add a new public function, add a corresponding unit test in `tests/unit/`.
If you add a new DAG, add a DAG import test (checks for syntax errors on import).

---

## Git conventions

- Branch naming: `feat/<short-description>`, `fix/<short-description>`, `chore/<topic>`
- Commit messages: Conventional Commits format
  `feat(ingestion): add 7-day lookback window to NYPD DAG`
  `fix(spark): correct EST→UTC offset in timestamp_normalizer`
  `chore(sql): add clustering on complaint_type to fact_311`
- One logical change per commit. Do not bundle unrelated files.
- Never commit directly to `main`. All changes via PR.

---

## Security rules (non-negotiable)

- No credentials or API tokens in any tracked file.
- All secrets via environment variables defined in `.env` (see `.env.example`).
- Socrata App Token stored in env var `SOCRATA_APP_TOKEN`.
- Object storage credentials stored in `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY`.
  Never pass them through a Spark `--conf` flag — they surface in the Spark UI
  environment page, the process list and Airflow task logs. Use the worker's
  `spark-defaults.conf` (mode 600) or environment injection.
- If you see a hardcoded secret anywhere, fix it before doing anything else.

---

## Target stack

One self-hosted stack, no managed cloud components: MinIO (S3) · Spark 3.5.1
Standalone · Airflow · Hive Metastore · Trino · Superset, all in Docker, storage
and compute on two separate nodes.

The "Phase 1 (GCP) / Phase 2 (self-hosted)" split was **abolished on 2026-07-30**
and `DEPLOYMENT_PHASE` no longer exists — all four cloud components were dropped
one by one, leaving the split with nothing to refer to. Decision:
`docs/dev/adr/0006-storage-compute-query-stack.md`.

`ingestion/loaders/`, `dags/`, `spark/jobs/` and `infra/` may still contain GCS/GCP
code. That is **debt awaiting removal, not a pattern to copy** — do not add new GCP
code; write to the target stack when you touch those files.

## Bronze partitioning strategies

Each source declares `partition_strategy` in its YAML. The `BackfillFacade`
dispatches on it.

All Bronze data files are **gzipped NDJSON** (`.ndjson.gz`) — newline-delimited so
`spark.read.json()` can stream them and one corrupt line cannot invalidate a file.
Manifests stay **uncompressed** `.json` (a few hundred bytes each; keeping them
readable with `head` is worth more than the saving).

> 🚨 The `.gz` **file extension is mandatory** and `Content-Encoding` must **not**
> be set. Spark's `s3a://` reader picks its decompression codec from the extension
> and ignores HTTP headers: a gzip object named `.ndjson` is read as text and
> produces garbled rows **without raising**. See ADR 0006 §4.1.

| strategy | used by | layout under `bronze/raw/{sid}/{ds}/` |
|---|---|---|
| `daily` | SRC-NYC-311, SRC-Open-Meteo | `{YYYY-MM}/data_{YYYY-MM-DD}.ndjson.gz` + `{YYYY-MM}/manifest_{YYYY-MM-DD}.json` |
| `monthly` (default) | SRC-NYPD | `data_{YYYY-MM}.ndjson.gz` + `manifest_{YYYY-MM}.json` |
| `static` | SRC-DCP | `data_static.ndjson.gz` + `manifest_static.json` |
| `snapshot` | overwrite-in-place upstreams with no time field | `ingest_date={YYYY-MM-DD}/data.ndjson.gz` + `ingest_date={YYYY-MM-DD}/manifest.json` |

`daily` requires a `timestamp_field` on every dataset and splits records by it.
`snapshot` partitions by **collection date** rather than record date and is the
only strategy that allows `timestamp_field: null` — it exists for upstreams that
overwrite in place and keep no history, where each day's pull is the only copy
that will ever exist. `static` writes one fixed filename and would overwrite
yesterday, which is exactly what `snapshot` must avoid.

When adding a new source, pick the strategy matching the dataset's cardinality and
access pattern. High-volume event streams → `daily`; static reference data and
lower-volume streams → `monthly`; overwrite-in-place upstreams → `snapshot`.

### Manifest contract

Every data file has a paired manifest. Two fields describe the **uncompressed
NDJSON payload**, not the stored object — so the idempotency check ("same records
re-run ⇒ same checksum") is unaffected by gzip's embedded timestamp:

| field | describes |
|---|---|
| `file_size_bytes` | uncompressed payload |
| `sha256_checksum` | uncompressed payload |
| `compression` | `"gzip"` or `null` |
| `stored_bytes` | size of the object actually written |

This layout and these field names are a **frozen on-disk contract**. Data already
written cannot be rewritten (`snapshot` history is unrecoverable), so an
implementation may be rewritten but the paths and field semantics may not.

---

## Data contract obligations

Any change to a Silver or Gold schema must:
1. Update the corresponding StructType in `spark/schemas/`.
2. Update the DDL in `sql/ddl/`.
3. Update the data contract in `contracts/`.
4. Add a migration note in `CHANGELOG.md` under `[Unreleased]`.

Breaking changes to Bronze (raw field removal/rename) must be flagged
as a comment in the relevant `ingestion/schemas/` Pydantic model.

---

## Prohibited patterns

| Pattern | Why forbidden | Use instead |
|---|---|---|
| `datetime.now()` in DAGs | Not idempotent | `context['execution_date']` |
| `SELECT *` in Gold SQL | Schema drift risk | Explicit column list |
| Relative imports at top level | Breaks package resolution | Absolute imports |
| Business logic in DAG files | Untestable, hard to reuse | `ingestion/` or `spark/transforms/` |
| `spark.read.json()` without schema | Silent type coercion | Always pass `schema=` arg |
| Hardcoded date strings in SQL | Not replayable | Parameterized `execution_date` |

---

## Reference links

- Source registry: `contracts/source-registry.md`
- Architecture overview: `README.md`
- Data contract standard: `datacontract.yaml` (Open Data Contract Standard v2)
- Socrata API docs: https://dev.socrata.com/docs/queries/
- Open-Meteo API docs: https://open-meteo.com/en/docs
- Trino geospatial functions: https://trino.io/docs/current/functions/geospatial.html
- Apache Iceberg spec: https://iceberg.apache.org/spec/
