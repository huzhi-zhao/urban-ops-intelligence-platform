# Getting Started

Start by rendering a sample figure on your computer. Then try a small public-API
pull, or connect the pipeline to infrastructure you run yourself. You can learn
the output format before installing Spark, Airflow or a database.

## Choose a starting point

| Goal | What you need | What you get |
|---|---|---|
| Try a figure locally | Git, Python 3.11+, a browser | Three interactive HTML examples, no services or credentials |
| Inspect a real source | Python 3.11, uv, internet access | A read-only API pull with record counts |
| Run ingestion and transformations | Your own S3 storage, Spark and the project containers | Data collected and transformed in your environment |
| Query Gold and reproduce figures | Hive Metastore, Trino and populated UOIP tables | SQL results and frozen exports from your build |

The commands below assume a shell on macOS, Linux or Windows through WSL.
Run them from the repository root unless a step says otherwise.

## Try a sample figure

### Get the repository

```bash
git clone https://github.com/huzhi-zhao/urban-ops-intelligence-platform.git
cd urban-ops-intelligence-platform
python3 --version
```

Use Python 3.11 or later. A full clone includes the renderer, vendored chart
library and sample JSON files. No Python package installation is needed for
this first exercise: the renderer uses only the standard library.

### Render the examples

```bash
python3 -m scripts.presentation.render_html \
  tests/fixtures/presentation/FIG-BO2-01.json \
  tests/fixtures/presentation/FIG-BO2-02.json \
  tests/fixtures/presentation/FIG-BO2-04.json \
  --out var/guide-demo
```

Expected output ends with `3 of 3 rendered`. Open
`var/guide-demo/FIG-BO2-01.html` in your browser. You can also serve the directory:

```bash
python3 -m http.server 8000 --bind 127.0.0.1 --directory var/guide-demo
```

Visit [the local example](http://localhost:8000/FIG-BO2-01.html). Stop the server
with Ctrl+C when finished. The HTML embeds both data and the chart library;
after generation, it does not need a database or internet connection.

### Read what you made

The marker shows a zone's mean **scheduled shift**, and the whisker shows its
observed range. The slope chart adds how the order changes between earlier and
later operations. The scatter chart compares scheduled order with address count.

These files contain **sample data** for learning and renderer verification.
Some endpoint values match published findings, but the full dataset is synthetic.
The sample `certification` value is part of the fixture, not a production audit.
Captions and some metadata are currently in Chinese; use [Results](results.md)
for the English explanation of the real findings.

For a small experiment, copy a fixture into `var/`, change its caption or one
`mean_shift` value, and render the copy. Keep its `[SAMPLE]` label. The `columns`
array names the entries in each `rows` array; changing a column order without
changing the rows changes the meaning of the data. This is a small example of
why the pipeline checks contracts.

The renderer supports these three figure IDs in this baseline. When given other
exports it may print `skip` and still exit successfully. Check the rendered
count rather than assuming a zero exit code means every figure was produced.

## Inspect a real source

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then create
the development environment used by the repository's main CI job:

```bash
uv sync --locked --python 3.11 --extra dev
```

Use the `dev` extra for this path. The current `make install` target installs
**all** extras, including Airflow and ML; those have separate environments in
this repository's testing workflow and are unnecessary for a source pull.

Fetch one winter day without writing anything to object storage:

```bash
uv run python -m scripts.backfill.main \
  --source SRC-WPG-311 \
  --start 2024-01-15 --end 2024-01-16 \
  --dry-run --max-workers 1
```

The end is exclusive. Expect a per-slice record count and a successful summary,
not the entire source history. Counts can change when the publisher updates old
records. This exercise needs the public API but no S3 credentials. A Socrata
application token can be supplied through `SOCRATA_APP_TOKEN` if needed.

Follow the source ID into [its YAML](../../config/sources/winnipeg_311.yaml),
then compare the storage layout in [Ingestion and Bronze](ingestion-bronze.md).

## Run the pipeline on your own infrastructure

MinIO, Spark, Hive Metastore and Trino are standard services that you can run
on your computer or reuse from your own environment. No access to the original
project deployment is required. The repository's Compose starts Airflow and the
project Spark worker; you prepare the remaining components separately.

### Prepare the services

Use Docker Engine with Compose, or Docker Desktop. Allow capacity for the
containers and historical data; the reference compute host has 4 cores and
24 GB RAM, which is context rather than a tested laptop minimum. Begin with a
small window before loading the full history.

1. **Create the shared Docker network** if it does not already exist:

   ```bash
   docker network create bigdata-net
   ```

2. **Start S3-compatible storage** such as [MinIO](https://github.com/minio/minio),
   with persistent storage and a bucket for this experiment. Give the pipeline
   credentials for that bucket. Use the API endpoint, not the management console.
   For a same-computer Docker setup, attach storage to `bigdata-net` with an alias
   such as `minio`, and publish its API port for commands run on the host.

3. **Start a Spark 3.5.1 master** on that network with alias `spark-master` and
   port 7077. It is a standard standalone master; the
   [Spark standalone instructions](https://archive.apache.org/dist/spark/docs/3.5.1/spark-standalone.html)
   also cover a single-machine installation. For a new local Docker master, the
   image used by the project's worker provides the same Spark version:

   ```bash
   docker run -d --name uoip-local-spark-master \
     --network bigdata-net --network-alias spark-master \
     -p 127.0.0.1:18080:8080 \
     --entrypoint /opt/spark/bin/spark-class \
     apache/spark:3.5.1 \
     org.apache.spark.deploy.master.Master --host spark-master
   ```

   Reuse an existing master instead of creating a second one with the same
   network alias. The project starts its own worker in the next section.

4. **For SQL access, start Hive Metastore and Trino.** Use a persistent metastore
   database and a Trino catalog named `hive` pointing to that metastore and your
   storage. The [Apache Hive image instructions](https://hub.docker.com/r/apache/hive)
   cover a standalone metastore; the
   [Trino Hive connector guide](https://trino.io/docs/current/connector/hive.html)
   describes the catalog. No Hive query server is required for Trino.

   Configure S3 endpoint, region, credentials and path-style access in the
   Trino catalog, and enable external-table writes and creation as needed by
   the DDL. The recorded Gold build used Trino 451; current documentation may
   use different S3 property names, so match configuration to your chosen version
   and run the smoke check below. Give Trino the alias `trino` on `bigdata-net`
   and, for the examples here, publish container port 8080 as host port 8090.

Superset is optional for exploring tables in a BI interface. It is not required
to run figure SQL or render HTML. Prepare only the services needed for your
chosen exercise; the setup above assembles standard components, not a bundled
one-command installer.

### Configure the project

```bash
cp .env.example .env
```

Edit the copy with your own values. Keep it untracked. The important distinction
is **where a connection originates**:

| Setting | Example for project containers | Example from the host shell |
|---|---|---|
| `S3_ENDPOINT_URL` | `http://minio:9000` | `http://localhost:9000` if published there |
| `TRINO_HOST` / `TRINO_PORT` | `trino` / `8080` | `localhost` / `8090` if published there |
| Spark master | `spark://spark-master:7077` on `bigdata-net` | Use the container-based workflow below |
| `AIRFLOW_BASE_URL` | `http://localhost:28080` for local browser access | Same browser URL |

Inside a container, `localhost` refers to that container. Store the container
view in `.env`, and override endpoint values for host-side commands.

Fill `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_BUCKET_NAME`, and `S3_REGION`.
Generate independent values for `POSTGRES_PASSWORD`, `AIRFLOW_ADMIN_PASSWORD`,
`AIRFLOW_WEBSERVER_SECRET_KEY`, and `AIRFLOW_JWT_SECRET`; Compose requires all four.
For example, run this once per value and copy each result to the corresponding field:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Set `AIRFLOW_BASE_URL` explicitly for your local setup. The template otherwise
falls back to the reference deployment's hostname. Alert webhooks and watchdogs
are optional for a local experiment; configure them before unattended collection.

### Start and verify the project stack

```bash
make stack-up
```

The first run builds images. This starts the project worker, Airflow services
and metadata Postgres, and initializes Airflow. Open
[Airflow](http://localhost:28080), using username `admin` and the password you
configured. For the local master command above, its UI is at
[Spark master](http://localhost:18080); confirm that `spark-worker-uoip` registers.

Inspect initialization and service state before triggering a job:

```bash
docker compose --env-file .env -f infra/docker/docker-compose.yml ps -a
docker compose --env-file .env -f infra/docker/docker-compose.yml logs --tail=100 airflow-init
```

Keep scheduled DAGs paused while preparing inputs. Catchup can schedule historical
intervals when a DAG is enabled. Load a small Bronze window into **your own bucket**
by repeating the source-pull command without `--dry-run`; when using local storage
published on port 9000, prefix it with `S3_ENDPOINT_URL=http://localhost:9000`.

Before using DAGs that synchronize table metadata, complete the SQL smoke check
below, then run `make ddl-create` without a prefix to declare the working tables
in your own bucket. Follow [Backfill](backfill.md) for reference inputs and Silver
windows; [Silver ETL](silver-etl.md#run-a-reference-job-from-the-project-container)
includes the reference-job submission command. Creating tables alone does not
populate them. Gold build gates assume the
Winnipeg analysis panel, so a one-day experiment is not expected to reproduce
the full set of Gold tables or the historical results.

### Check the SQL and storage connection

After setting your host-side endpoint values, test table creation and reads in a
new disposable namespace:

```bash
export S3_ENDPOINT_URL=http://localhost:9000
export TRINO_HOST=localhost
export TRINO_PORT=8090
make ddl-create PREFIX=guide-smoke
make ddl-smoke PREFIX=guide-smoke
```

Use a fresh prefix if `guide-smoke` already contains work you want to keep.
The smoke procedure writes and reads two synthetic rows per table. It validates
Trino, metadata and storage together; it does not validate historical ETL.
Remove only those test tables and objects when finished:

```bash
make ddl-teardown PREFIX=guide-smoke
```

### Stop the experiment

```bash
make stack-down
```

This keeps project volumes. Stop separately created services using their own
commands; for the example master, use `docker stop uoip-local-spark-master` and
later `docker start uoip-local-spark-master`. Stopping the project stack does
not stop an independently deployed snapshot timer.

## Query and export real results

Once your deployment contains the Gold analysis tables, set host connection
values as above and run one documented query:

```bash
make eda-run ONLY=FIG-BO2-01
```

The H1 baseline returns 22 zone rows with mean and minimum/maximum scheduled
shifts. A fresh dataset may differ; compare build context before interpreting
that as a defect. Check [certification](data-quality.md#inspect-a-build), then
freeze and render this result:

```bash
make eda-export ONLY=FIG-BO2-01 OUT=var/presentation
python3 -m scripts.presentation.render_html \
  var/presentation/FIG-BO2-01.json --out var/presentation/html
```

Read the export's `certification`, `frozen_at`, `source_sql` and caption fields.
Export success does not imply certification. Keep its metadata with the figure;
this is how a real result differs from the bundled sample.

## Development checks

The source-pull environment can run the quality commands. Spark tests also need
a compatible JDK; Java 17 works with the pinned Spark 3.5.1 setup. The network
unit test calls a live public API.

```bash
make lint
make test-unit
```

`make test-unit-offline` excludes the marked live-API test. The DAG and ML suites
have their own environments through `make test-dags` and `make test-ml`.
`make test-integration` needs real storage credentials; skipped tests do not
establish that the storage path works.

For a reading path through the code, follow the source YAML → backfill dispatch
→ fetcher and loader → Silver job → Gold SQL → figure query.
[Architecture](architecture.md) explains why those boundaries exist, and
[Operations](operations.md) covers recovery when a running component fails.
