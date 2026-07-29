.PHONY: help install lint test-unit test-unit-offline test-integration spark-submit dag-trigger terraform-init terraform-plan terraform-apply terraform-destroy deploy-composer composer-dags-bucket

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
	@echo "Airflow / Cloud Composer:"
	@echo "  make dag-trigger DAG=<dag-name>    Trigger Airflow DAG locally"
	@echo "  make deploy-composer               Sync DAGs + packages to Cloud Composer GCS"
	@echo ""
	@echo "Terraform (GCP):"
	@echo "  make terraform-init               Initialize Terraform"
	@echo "  make terraform-plan               Preview Terraform changes"
	@echo "  make terraform-apply              Apply Terraform changes"
	@echo "  make terraform-destroy            Destroy GCP resources"

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
		uv run --extra dev python -m sqlfluff lint sql/ --dialect bigquery; \
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

# ── Airflow / Cloud Composer ──────────────────────────────────────────────────

dag-trigger:
	@if [ -z "$(DAG)" ]; then \
		echo "Usage: make dag-trigger DAG=dag_ingest_nyc_311"; \
		exit 1; \
	fi
	airflow dags trigger $(DAG)

# Resolve the Composer DAGs GCS bucket from Terraform output (requires terraform apply first).
COMPOSER_REGION  ?= us-central1
COMPOSER_ENV     ?= nyc-uoip-composer
# Must match project_id in infra/terraform/terraform.tfvars. `pace-lab-bdp` here
# was the pre-migration project and silently targeted the wrong GCP account.
GCP_PROJECT      ?= nyc-uoip-prod
COMPOSER_BUCKET  ?= $(shell cd infra/terraform && terraform output -raw composer_dags_gcs_prefix 2>/dev/null | sed 's|/dags$$||')

# Upload DAG files + our Python packages to Cloud Composer.
# Composer adds gs://<bucket>/plugins/ to PYTHONPATH automatically.
#
# Usage:
#   make deploy-composer                          # auto-resolves bucket from Terraform
#   make deploy-composer COMPOSER_BUCKET=gs://... # override bucket manually
# Inject SOCRATA_APP_TOKEN into the Composer environment after terraform apply.
# Tokens never touch Terraform state or any file.
# Usage: make set-composer-secret TOKEN=your_actual_token
set-composer-secret:
	@if [ -z "$(TOKEN)" ]; then \
		echo "Usage: make set-composer-secret TOKEN=your_token"; \
		exit 1; \
	fi
	gcloud composer environments update $(COMPOSER_ENV) \
		--location=$(COMPOSER_REGION) \
		--update-env-variables=SOCRATA_APP_TOKEN=$(TOKEN) \
		--project=$(GCP_PROJECT)

deploy-composer:
	@if [ -z "$(COMPOSER_BUCKET)" ]; then \
		echo "ERROR: Could not resolve Composer bucket."; \
		echo "Run 'terraform apply' first, or pass COMPOSER_BUCKET=gs://<bucket>"; \
		exit 1; \
	fi
	@echo "Deploying to $(COMPOSER_BUCKET)"
	gsutil -m rsync -r -d dags/      $(COMPOSER_BUCKET)/dags/
	gsutil -m rsync -r    ingestion/ $(COMPOSER_BUCKET)/plugins/ingestion/
	gsutil -m rsync -r    scripts/   $(COMPOSER_BUCKET)/plugins/scripts/
	gsutil -m rsync -r    config/    $(COMPOSER_BUCKET)/plugins/config/
	@echo "Done. DAGs will appear in the Airflow UI within ~1 minute."

# ── Terraform ─────────────────────────────────────────────────────────────────

TERRAFORM_DIR := infra/terraform

terraform-init:
	cd $(TERRAFORM_DIR) && terraform init

terraform-plan:
	cd $(TERRAFORM_DIR) && terraform plan

terraform-apply:
	cd $(TERRAFORM_DIR) && terraform apply

terraform-destroy:
	cd $(TERRAFORM_DIR) && terraform destroy