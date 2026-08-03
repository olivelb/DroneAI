PYTHON ?= python3
PYTHON_PATHS := app1-colmap app2-ia app3-processing app4-dashboard/api shared alembic tests tools
PRODUCTION_PYTHON_PATHS := app1-colmap app2-ia app3-processing app4-dashboard/api shared tools
COLMAP_WORKER_PATHS := app1-colmap/colmap_worker app1-colmap/main.py

.PHONY: check static compile lint worker-lint typecheck docs-check workflows-check test coverage frontend-check

compile:
	$(PYTHON) -m compileall -q $(PYTHON_PATHS)

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff check --select C90,F401,F841 $(PRODUCTION_PYTHON_PATHS)

worker-lint:
	$(PYTHON) -m ruff check --select B,SIM,UP,RUF,ASYNC $(COLMAP_WORKER_PATHS)
	$(PYTHON) -m ruff check --select C90 --config lint.mccabe.max-complexity=15 app1-colmap/colmap_worker

typecheck:
	$(PYTHON) -m mypy --strict --ignore-missing-imports --follow-imports=skip app1-colmap/colmap_worker

docs-check:
	$(PYTHON) tools/check_markdown_links.py

workflows-check:
	actionlint

static: compile lint worker-lint typecheck docs-check workflows-check

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

check: static coverage
