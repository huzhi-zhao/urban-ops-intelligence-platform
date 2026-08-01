.PHONY: help install lint test-unit test-unit-offline test-integration spark-submit dag-trigger

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
