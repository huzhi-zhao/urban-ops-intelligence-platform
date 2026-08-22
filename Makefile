.PHONY: help install lint test-unit test-unit-offline test-dags test-ml test-integration spark-submit dag-trigger \
        stack-up stack-down stack-down-legacy stack-restart-airflow stack-recreate-airflow \
        stack-rebuild-airflow stack-logs stack-cmd \
        ddl-create ddl-smoke ddl-teardown gold-build gold-dq

# Default target
help:
	@echo "NYC-UOIP Makefile"
	@echo ""
	@echo "Setup:"
	@echo "  make install          Install all Python dependencies (uv)"
	@echo ""
	@echo "Code Quality:"
	@echo "  make lint             Lint Python (ruff) + SQL (sqlfluff)"
	@echo "  make test-unit        Run unit tests only"
	@echo "  make test-dags        Run the DAG tests with airflow installed (slow first run)"
	@echo "  make test-ml          Run the M1 tests with the ml extra installed (own venv)"
	@echo "  make test-integration Run integration tests (requires Docker)"
	@echo ""
	@echo "Spark Jobs:"
	@echo "  make spark-submit JOB=<job-path>  Submit Spark job locally"
	@echo ""
	@echo "Airflow:"
	@echo "  make dag-trigger DAG=<dag-name>    Trigger Airflow DAG locally"
	@echo ""
	@echo "Tables (Trino — shared platform service, see ADR 0006 s9):"
	@echo "  make ddl-create   [PREFIX=smoke-YYYYMMDD]  Create the 25 Silver/Gold tables"
	@echo "  make ddl-smoke    [PREFIX=smoke-YYYYMMDD]  Insert 2 rows per table, read back"
	@echo "  make ddl-teardown PREFIX=smoke-YYYYMMDD    Drop the tables and purge the prefix"
	@echo "  make gold-build [ONLY=seeds|dims|facts|scoring|<table>] [DRY_RUN=1] [PREFIX=...]"
	@echo "  make gold-dq [ONLY=...] [PREFIX=...]   # null-rate baseline as markdown"
	@echo "                                             Rebuild the Gold tables"
	@echo ""
	@echo "Compute-node stack (Docker):"
	@echo "  make stack-up             Start Airflow + Spark"
	@echo "  make stack-down           Stop the stack (volumes kept)"
	@echo "  make stack-down-legacy    Tear down the pre-rename 'docker' project (one-shot)"
	@echo "  make stack-restart-airflow   Restart scheduler/webserver/dag-processor (code only)"
	@echo "  make stack-recreate-airflow  Recreate them — needed for .env changes"
	@echo "  make stack-rebuild-airflow   Rebuild the image — needed for Dockerfile changes"
	@echo "  make stack-logs [S=svc]   Tail stack logs"
	@echo "  make stack-cmd            Print the underlying docker compose command"

# ── Dependencies ──────────────────────────────────────────────────────────────

install:
	uv sync --all-extras

# ── Code Quality ───────────────────────────────────────────────────────────────

# All gates run through `uv run` so they use the project venv without needing it
# activated, and via `python -m` so they don't depend on the venv's console-script
# shebangs (those hardcode an absolute path and break if the repo is moved).
lint:
	uv run --extra dev python -m ruff check .
	@if [ -d sql ]; then \
		uv run --extra dev python -m sqlfluff lint sql/; \
	else \
		echo "lint: no sql/ directory yet — skipping sqlfluff"; \
	fi

test-unit:
	uv run --extra dev python -m pytest tests/unit/ -v

# Offline-safe variant: skips the live-API contract test (see tests/unit/test_api_structure.py)
test-unit-offline:
	uv run --extra dev python -m pytest tests/unit/ -v -m "not network"

# The DAG tests skip without apache-airflow, and airflow is deliberately not a
# dev dependency (heavy, and nothing in the day-to-day loop needs it). This
# target installs the pinned extra and runs them for real. CI runs the same
# thing in its own job so a skip never passes for a pass — stage E2 shipped four
# defects behind that skip. See O15 in the L2 launch doc.
#
# 🔴 It runs in its OWN environment (.venv-airflow), and that is load-bearing:
# apache-airflow-providers-apache-spark pulls `pyspark-client`, a *separate*
# distribution that writes into the same pyspark/ package directory and
# overwrites the pinned 3.5.1 with 4.2.0 files. uv reports no conflict — the
# lock still says pyspark==3.5.1 — but `import pyspark` then reports 4.2.0 and
# the Spark unit tests fail with an ImportError deep inside pyspark that looks
# nothing like "you ran a different make target". Measured 2026-08-20.
test-dags:
	UV_PROJECT_ENVIRONMENT=.venv-airflow uv run --extra dev --extra airflow \
		python -m pytest tests/unit/test_dag_imports.py tests/unit/test_dag_gold_build.py -v

# M1's tests, same shape and same reasoning as test-dags: the `ml` extra is not
# in `dev`, so these skip during the day-to-day loop and run for real here and
# in CI's `ml` job.
#
# 🔴 Its own environment (.venv-ml) for the reason spelled out above test-dags:
# not because a conflict is expected, but because uv does not report the kind
# that actually bit us. statsmodels resolves numpy/scipy freely here without
# ever sharing a directory with the cluster-pinned pyspark 3.5.1 in .venv.
test-ml:
	UV_PROJECT_ENVIRONMENT=.venv-ml uv run --extra dev --extra ml \
		python -m pytest tests/unit/test_m1_features.py tests/unit/test_m1_model.py \
		tests/unit/test_train_m1.py -v

test-integration:
	uv run --extra dev python -m pytest tests/integration/ -v

# ── Spark ─────────────────────────────────────────────────────────────────────

spark-submit:
	@if [ -z "$(JOB)" ]; then \
		echo "Usage: make spark-submit JOB=spark/jobs/etl_nyc_311.py"; \
		exit 1; \
	fi
	spark-submit --master "spark://localhost:7077" $(JOB)

# ── Airflow ───────────────────────────────────────────────────────────────────

dag-trigger:
	@if [ -z "$(DAG)" ]; then \
		echo "Usage: make dag-trigger DAG=dag_ingest_nyc_311"; \
		exit 1; \
	fi
	airflow dags trigger $(DAG)

# ── Tables (Trino) ────────────────────────────────────────────────────────────

# Trino and the Hive Metastore are shared platform services, not part of this
# repo's compose stack (ADR 0006 §9) — these targets talk to an already-running
# Trino over the network, they do not start anything.
#
# PREFIX isolates a run under a disposable schema suffix and storage path.
# ddl-teardown REQUIRES it: without one it would drop the production tables and
# delete real Silver data. That is why there is no `PREFIX ?=` default here.
DDL = uv run python -m scripts.ddl.apply_ddl

ddl-create:
	$(DDL) create $(if $(PREFIX),--location-prefix $(PREFIX))

ddl-smoke:
	$(DDL) smoke $(if $(PREFIX),--location-prefix $(PREFIX))

# Trino lives on the platform-level stack, so .env holds the *container* view
# (trino:8080) for the Airflow container. A host shell has to go through the
# published port; build_gold prints this hint on a connection failure too.
#   TRINO_HOST=localhost TRINO_PORT=8090 make gold-build
gold-build:
	uv run python -m scripts.gold.build_gold \
	    $(if $(ONLY),--only $(ONLY)) \
	    $(if $(DRY_RUN),--dry-run) \
	    $(if $(PREFIX),--location-prefix $(PREFIX))

# DQ baseline (L2 stage E1): row count + per-column null rate for every built
# Gold table, as markdown for the launch doc. Same host-shell caveat as above.
gold-dq:
	@uv run python -m scripts.gold.dq_baseline \
	    $(if $(ONLY),--only $(ONLY)) \
	    $(if $(PREFIX),--location-prefix $(PREFIX))

ddl-teardown:
	@if [ -z "$(PREFIX)" ]; then \
		echo "Usage: make ddl-teardown PREFIX=smoke-YYYYMMDD"; \
		echo "Refusing to run without one — see scripts/ddl/apply_ddl.py."; \
		exit 1; \
	fi
	$(DDL) teardown --location-prefix $(PREFIX)

# ── Compute-node Docker stack ─────────────────────────────────────────────────

# Compose has two independent .env mechanisms and this repo's two point at
# different directories:
#   * `env_file: ../../.env` in the compose file    → repo root, injected into containers
#   * `${VAR}` interpolation                        → the *project directory*, which
#     `-f infra/docker/docker-compose.yml` sets to infra/docker/ — where no .env exists,
#     so every `${VAR:?}` aborts the command before a container is ever created.
#
# `--env-file .env` points interpolation at the root file.
#
# The project name no longer depends on any of this — it is pinned to `uoip`
# by `name:` at the top of the compose file, so `--project-directory` is now
# safe too. It used to default to the compose file's parent directory, i.e.
# `docker`; see stack-down-legacy below.
#
# Always invoke these from the repo root — `--env-file` is resolved relative to cwd.
COMPOSE = docker compose --env-file .env -f infra/docker/docker-compose.yml
AIRFLOW_SERVICES = airflow-webserver airflow-scheduler airflow-dag-processor

stack-cmd:
	@echo "$(COMPOSE)"

stack-up:
	$(COMPOSE) up -d

# Volumes are kept on purpose. To drop them (destroys the Airflow metadata DB)
# run the printed command with `down -v` yourself: `make stack-cmd`.
stack-down:
	$(COMPOSE) down

# One-shot teardown of the pre-rename stack.
#
# Before the compose file pinned `name: uoip`, the project was called `docker`
# (compose defaults it to the compose file's parent directory). Those containers
# are invisible to every target above, so `stack-down` leaves them running and
# `stack-up` then dies on "container name /spark-master is already in use".
#
# Volumes are kept — the old ones are `docker_postgres-db` / `docker_airflow-logs`.
# Migrate them before deleting anything: see infra/docker/README.md.
stack-down-legacy:
	$(COMPOSE) -p docker down || true
	@ids=$$(docker ps -aq --filter label=com.docker.compose.project=docker); \
	if [ -n "$$ids" ]; then \
	  echo "removing leftover containers from the old 'docker' project:"; \
	  docker ps -a --filter label=com.docker.compose.project=docker --format '  {{.Names}}'; \
	  docker rm -f $$ids; \
	else \
	  echo "no containers left from the old 'docker' project"; \
	fi

# A `git pull` alone does not pick up code changes — LocalExecutor forks tasks
# from the scheduler's in-memory state.
#
# Picks up code only. `docker compose restart` re-runs the process inside the
# *existing* container, so the environment stays exactly as it was baked in at
# creation time: edits to .env or to the compose file's `environment:` block
# have no effect no matter how many times you run this. Use
# stack-recreate-airflow for those, and note that `docker ps` tells the two
# apart — "Created 11 days ago / Up 2 minutes" means restarted, not recreated.
stack-restart-airflow:
	$(COMPOSE) restart $(AIRFLOW_SERVICES)

# Recreates the containers so .env / compose environment changes take effect.
# It does NOT rebuild the image — a Dockerfile.airflow change needs
# stack-rebuild-airflow below.
stack-recreate-airflow:
	$(COMPOSE) up -d --force-recreate $(AIRFLOW_SERVICES)

# Rebuilds the image, then recreates the containers from it. The one to use
# after editing Dockerfile.airflow; the other two targets both reuse whatever
# image was built last, so a Dockerfile edit deployed with them silently does
# nothing.
stack-rebuild-airflow:
	$(COMPOSE) up -d --build --force-recreate $(AIRFLOW_SERVICES)

stack-logs:
	$(COMPOSE) logs -f --tail=200 $(S)
