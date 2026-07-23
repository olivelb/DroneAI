.PHONY: check compile lint test frontend-check

compile:
	python3 -m compileall -q app1-colmap app2-ia app3-processing app4-dashboard/api shared alembic tests tools

lint:
	python3 -m ruff check .

test:
	python3 -m pytest -m "not gpu and not integration"

frontend-check:
	cd app4-dashboard/frontend && npm ci && npm run lint && npm run build

check: compile lint test
