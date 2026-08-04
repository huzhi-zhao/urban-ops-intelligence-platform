.PHONY: help install lint test-unit test-unit-offline test-integration spark-submit dag-trigger \
        stack-up stack-down stack-down-legacy stack-restart-airflow stack-logs stack-cmd

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
	@echo "  make test-integration Run integration tests (requires Docker)"
	@echo ""
	@echo "Spark Jobs:"
	@echo "  make spark-submit JOB=<job-path>  Submit Spark job locally"
	@echo ""
	@echo "Airflow:"
	@echo "  make dag-trigger DAG=<dag-name>    Trigger Airflow DAG locally"
	@echo ""
	@echo "Compute-node stack (Docker):"
	@echo "  make stack-up             Start Airflow + Spark"
	@echo "  make stack-down           Stop the stack (volumes kept)"
	@echo "  make stack-down-legacy    Tear down the pre-rename 'docker' project (one-shot)"
	@echo "  make stack-restart-airflow  Restart scheduler/webserver/dag-processor"
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
stack-restart-airflow:
	$(COMPOSE) restart $(AIRFLOW_SERVICES)

stack-logs:
	$(COMPOSE) logs -f --tail=200 $(S)
