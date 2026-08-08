PYTHON ?= python3
CI_PYTHON_PATHS := $(wildcard scripts/ci/*.py)
PYTHON_PATHS := app1-colmap app2-ia app3-processing app4-dashboard/api shared alembic tests tools $(CI_PYTHON_PATHS)
PRODUCTION_PYTHON_PATHS := app1-colmap app2-ia app3-processing app4-dashboard/api shared tools $(CI_PYTHON_PATHS)
COLMAP_WORKER_PATHS := app1-colmap/colmap_worker app1-colmap/main.py
SHARED_TYPED_PATHS := $(wildcard shared/*.py)
SERVICE_CORE_PATHS := \
	app2-ia/detection_core.py \
	app3-processing/processing_core.py \
	app3-processing/orthomosaic_tiler.py \
	app3-processing/analysis_workflow.py
SHELL_SCRIPTS := scripts/bootstrap-dev.sh scripts/ci/*.sh scripts/deploy/*.sh

.PHONY: check static compile lint worker-lint service-core-lint shared-lint typecheck scripts-check docs-check workflows-check audit test coverage frontend-check frontend-e2e

compile:
	$(PYTHON) -m compileall -q $(PYTHON_PATHS)

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff check --select C90,F401,F841 $(PRODUCTION_PYTHON_PATHS)

worker-lint:
	$(PYTHON) -m ruff check --select B,SIM,UP,RUF,ASYNC $(COLMAP_WORKER_PATHS)
	$(PYTHON) -m ruff check --select C90 --config lint.mccabe.max-complexity=15 app1-colmap/colmap_worker

service-core-lint:
	$(PYTHON) -m ruff check --select B,SIM,UP,RUF,ASYNC $(SERVICE_CORE_PATHS)

shared-lint:
	$(PYTHON) -m ruff check --select B,SIM,UP,RUF,ASYNC --ignore RUF001 shared
	$(PYTHON) -m ruff check --select C90 --config lint.mccabe.max-complexity=18 shared

typecheck:
	$(PYTHON) -m mypy --strict --ignore-missing-imports --follow-imports=skip app1-colmap/colmap_worker
	$(PYTHON) -m mypy --strict --ignore-missing-imports --follow-imports=skip $(SHARED_TYPED_PATHS)

scripts-check:
	shellcheck $(SHELL_SCRIPTS)

docs-check:
	$(PYTHON) tools/check_markdown_links.py

workflows-check:
	actionlint

audit:
	$(PYTHON) -m pip_audit --strict

static: compile lint worker-lint service-core-lint shared-lint typecheck scripts-check docs-check workflows-check

test:
	$(PYTHON) -m pytest -m "not gpu and not integration"

coverage:
	$(PYTHON) -m coverage erase
	$(PYTHON) -m coverage run -m pytest -m "not gpu and not integration"
	$(PYTHON) -m coverage report

frontend-check:
	cd app4-dashboard/frontend && \
	npm ci && \
	npm audit --omit=dev --audit-level=high && \
	npm run duplication && \
	npm run test && \
	npm run lint && \
	npm run build

frontend-e2e:
	cd app4-dashboard/frontend && npm run build && npm run test:e2e

check: static audit coverage
