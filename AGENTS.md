# AGENTS.md — NYC-UOIP AI Agent Conventions

> Shared by all AI coding agents (Claude Code, GitHub Copilot, Cursor, Codex, etc.).
> Tool-specific overrides live in their own files (CLAUDE.md, .cursorrules, etc.).
> Claude Code reads this file via `@AGENTS.md` import in CLAUDE.md.

---

## Project summary

**Repo**: nyc-uoip
**Purpose**: Production-grade Lakehouse pipeline. NYC Open Data → Bronze/Silver/Gold
layers → daily Operational Load Score per Borough → resource allocation recommendations.
**Language**: Python 3.11+, SQL (BigQuery dialect Phase 1 / Trino dialect Phase 2)
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

- No credentials, API tokens, or GCP service account keys in any tracked file.
- All secrets via environment variables defined in `.env` (see `.env.example`).
- Socrata App Token stored in env var `SOCRATA_APP_TOKEN`.
- GCP service account key path stored in env var `GOOGLE_APPLICATION_CREDENTIALS`.
- If you see a hardcoded secret anywhere, fix it before doing anything else.

---

## Phase awareness

Many files have a Phase 1 (GCP) and Phase 2 (self-hosted) variant.
Use these env vars to switch:

```bash
DEPLOYMENT_PHASE=1   # GCS + BigQuery + Dataproc + Composer
DEPLOYMENT_PHASE=2   # MinIO + Iceberg + Trino + Docker Airflow
```

When generating new loader or DDL code, ask (or check `.env`) which phase is active.
Generate both variants only if explicitly requested.

## Bronze partitioning strategies

Each source declares `partition_strategy: daily|monthly` in its YAML. The
`BackfillFacade` dispatches on it:

All Bronze data files are **NDJSON** (`.ndjson`), not JSON arrays — BigQuery
`LOAD DATA` and `spark.read.json()` both require newline-delimited records.

- `daily` (SRC-NYC-311, SRC-Open-Meteo): records are split by
  `timestamp_field` into per-day files inside a month folder
  (`bronze/raw/{sid}/{ds}/{YYYY-MM}/data_{YYYY-MM-DD}.ndjson` +
  `manifest_{YYYY-MM-DD}.json`). Requires `timestamp_field` on every dataset.
- `monthly` (SRC-NYPD, default): single file per month
  (`bronze/raw/{sid}/{ds}/data_{YYYY-MM}.ndjson` +
  `manifest_{YYYY-MM}.json`).
- `static` (SRC-DCP): single fixed-name file
  (`bronze/raw/{sid}/{ds}/data_static.ndjson` + `manifest_static.json`).

When adding a new source, choose the strategy that matches the dataset's
cardinality and access pattern. High-volume event streams → `daily`;
static reference data and lower-volume streams → `monthly`.

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
- BigQuery GIS reference: https://cloud.google.com/bigquery/docs/reference/standard-sql/geography_functions
- Apache Iceberg spec: https://iceberg.apache.org/spec/
