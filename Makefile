.PHONY: check compile lint test coverage frontend-check

compile:
	python3 -m compileall -q app1-colmap app2-ia app3-processing app4-dashboard/api shared alembic tests tools

lint:
	python3 -m ruff check .
	python3 -m ruff check --select C90,F401,F841 app1-colmap app2-ia app3-processing app4-dashboard/api shared tools

test:
	python3 -m pytest -m "not gpu and not integration"

coverage:
	python3 -m coverage erase
	python3 -m coverage run -m pytest -m "not gpu and not integration"
	python3 -m coverage report

frontend-check:
	cd app4-dashboard/frontend && \
	npm ci && \
	npm audit --omit=dev --audit-level=high && \
	npm run duplication && \
	npm run test && \
	npm run lint && \
	npm run build

check: compile lint coverage
