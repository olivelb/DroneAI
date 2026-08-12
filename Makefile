PYTHON ?= python3
NPM ?= corepack npm
CI_PYTHON_PATHS := $(wildcard scripts/ci/*.py)
PYTHON_PATHS := app1-colmap app2-ia app3-processing app4-dashboard/api shared alembic tests tools $(CI_PYTHON_PATHS)
PRODUCTION_PYTHON_PATHS := app1-colmap app2-ia app3-processing app4-dashboard/api shared tools $(CI_PYTHON_PATHS)
COLMAP_WORKER_PATHS := app1-colmap/colmap_worker app1-colmap/main.py
GAUSSIAN_ORTHO_TYPED_PATHS := \
	app1-colmap/gaussian_ortho/__init__.py \
	app1-colmap/gaussian_ortho/capacity_planning.py \
	app1-colmap/gaussian_ortho/colmap_loader.py \
	app1-colmap/gaussian_ortho/colmap_subset.py \
	app1-colmap/gaussian_ortho/coverage_quality.py \
	app1-colmap/gaussian_ortho/cuda_rasterizer.py \
	app1-colmap/gaussian_ortho/exif_altitude.py \
	app1-colmap/gaussian_ortho/facade_frame.py \
	app1-colmap/gaussian_ortho/filter_quality.py \
	app1-colmap/gaussian_ortho/generate_gaussian_orthophoto.py \
	app1-colmap/gaussian_ortho/gaussian_model.py \
	app1-colmap/gaussian_ortho/geo_writer.py \
	app1-colmap/gaussian_ortho/height_reference.py \
	app1-colmap/gaussian_ortho/merge.py \
	app1-colmap/gaussian_ortho/model_filtering.py \
	app1-colmap/gaussian_ortho/ortho_renderer.py \
	app1-colmap/gaussian_ortho/phase_artifacts.py \
	app1-colmap/gaussian_ortho/partition.py \
	app1-colmap/gaussian_ortho/pca_alignment.py \
	app1-colmap/gaussian_ortho/rasterizer.py \
	app1-colmap/gaussian_ortho/raster_product.py \
	app1-colmap/gaussian_ortho/render_geometry.py \
	app1-colmap/gaussian_ortho/scene_info.py
SHARED_FRAMEWORK_TYPED_PATHS := shared/event_schemas.py shared/tile_results.py
SHARED_TYPED_PATHS := $(filter-out $(SHARED_FRAMEWORK_TYPED_PATHS),$(wildcard shared/*.py))
APP2_TYPED_PATHS := \
	app2-ia/detection_core.py \
	app2-ia/detection_shard_stage.py \
	app2-ia/detection_stage.py \
	app2-ia/sam3_backend.py \
	app2-ia/stage_executor.py \
	app2-ia/tile_detection_workflow.py \
	app2-ia/main.py
APP3_TYPED_PATHS := \
	app3-processing/processing_core.py \
	app3-processing/orthomosaic_tiler.py \
	app3-processing/analysis_workflow.py \
	app3-processing/legacy_aggregation.py \
	app3-processing/processing_dispatcher.py \
	app3-processing/main.py
API_TYPED_PATHS := \
	app4-dashboard/api/__init__.py \
	app4-dashboard/api/control_leadership.py \
	app4-dashboard/api/control_runtime.py \
	app4-dashboard/api/control_worker.py \
	app4-dashboard/api/health.py \
	app4-dashboard/api/security.py \
	app4-dashboard/api/rate_limit.py \
	app4-dashboard/api/retention.py \
	app4-dashboard/api/messaging.py \
	app4-dashboard/api/realtime.py \
	app4-dashboard/api/kubernetes_jobs.py \
	app4-dashboard/api/kubernetes_status.py \
	app4-dashboard/api/stage_orchestrator.py \
	app4-dashboard/api/image_preview.py
API_FRAMEWORK_TYPED_PATHS := \
	app4-dashboard/api/gcp_schemas.py \
	app4-dashboard/api/schemas.py \
	app4-dashboard/api/map_schemas.py \
	app4-dashboard/api/stage_schemas.py
API_DOMAIN_TYPED_PATHS := \
	app4-dashboard/api/analysis_support.py \
	app4-dashboard/api/dataset_access.py \
	app4-dashboard/api/dataset_uploads.py \
	app4-dashboard/api/feature_audit.py \
	app4-dashboard/api/gcp_audit.py \
	app4-dashboard/api/gcp_workspace.py \
	app4-dashboard/api/identity_api.py \
	app4-dashboard/api/mission_detail.py \
	app4-dashboard/api/mission_access.py \
	app4-dashboard/api/mission_state.py \
	app4-dashboard/api/map_support.py \
	app4-dashboard/api/raster_style_contract.py
API_ROUTE_TYPED_PATHS := \
	app4-dashboard/api/routers/auth.py \
	app4-dashboard/api/routers/datasets.py \
	app4-dashboard/api/routers/identity.py \
	app4-dashboard/api/routers/identity_access_audit.py \
	app4-dashboard/api/routers/identity_capabilities.py \
	app4-dashboard/api/routers/identity_credentials.py \
	app4-dashboard/api/routers/identity_members.py \
	app4-dashboard/api/routers/map_analyses.py \
	app4-dashboard/api/routers/map_exports.py \
	app4-dashboard/api/routers/map_feature_mutations.py \
	app4-dashboard/api/routers/map_features.py \
	app4-dashboard/api/routers/map_gcps.py \
	app4-dashboard/api/routers/map_rasters.py \
	app4-dashboard/api/routers/map_styles.py \
	app4-dashboard/api/routers/maps.py \
	app4-dashboard/api/routers/mission_catalog.py \
	app4-dashboard/api/routers/mission_stages.py \
	app4-dashboard/api/routers/missions.py \
	app4-dashboard/api/routers/operations.py \
	app4-dashboard/api/routers/organization_saas.py \
	app4-dashboard/api/routers/platform.py
SERVICE_CORE_PATHS := $(GAUSSIAN_ORTHO_TYPED_PATHS) $(APP2_TYPED_PATHS) $(APP3_TYPED_PATHS)
SHELL_SCRIPTS := scripts/bootstrap-dev.sh scripts/ci/*.sh scripts/deploy/*.sh

.PHONY: check static compile lint worker-lint service-core-lint shared-lint typecheck scripts-check docs-check workflows-check audit test integration-test coverage frontend-check frontend-e2e

compile:
	$(PYTHON) -m compileall -q $(PYTHON_PATHS)

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff check --select C90,F401,F841 $(PRODUCTION_PYTHON_PATHS)

worker-lint:
	$(PYTHON) -m ruff check --select B,SIM,UP,RUF,ASYNC $(COLMAP_WORKER_PATHS)
	$(PYTHON) -m ruff check --select C90 --config lint.mccabe.max-complexity=15 app1-colmap/colmap_worker

service-core-lint:
	$(PYTHON) -m ruff check --select B,SIM,UP,RUF,ASYNC \
		$(SERVICE_CORE_PATHS) $(API_TYPED_PATHS) \
		$(API_FRAMEWORK_TYPED_PATHS) $(API_DOMAIN_TYPED_PATHS) \
		$(API_ROUTE_TYPED_PATHS)

shared-lint:
	$(PYTHON) -m ruff check --select B,SIM,UP,RUF,ASYNC --ignore RUF001 shared
	$(PYTHON) -m ruff check --select C90 --config lint.mccabe.max-complexity=18 shared

typecheck:
	$(PYTHON) -m mypy --strict --ignore-missing-imports --follow-imports=skip app1-colmap/colmap_worker
	$(PYTHON) -m mypy --strict --ignore-missing-imports --follow-imports=skip $(GAUSSIAN_ORTHO_TYPED_PATHS)
	$(PYTHON) -m mypy --strict --ignore-missing-imports --follow-imports=skip $(SHARED_TYPED_PATHS)
	$(PYTHON) -m mypy --strict --ignore-missing-imports $(SHARED_FRAMEWORK_TYPED_PATHS)
	$(PYTHON) -m mypy --strict --ignore-missing-imports --follow-imports=skip $(APP2_TYPED_PATHS)
	$(PYTHON) -m mypy --strict --ignore-missing-imports --follow-imports=skip $(APP3_TYPED_PATHS)
	MYPYPATH=app4-dashboard $(PYTHON) -m mypy --strict --ignore-missing-imports --follow-imports=skip $(API_TYPED_PATHS)
	MYPYPATH=app4-dashboard $(PYTHON) -m mypy --strict --ignore-missing-imports $(API_FRAMEWORK_TYPED_PATHS)
	MYPYPATH=app4-dashboard $(PYTHON) -m mypy --strict --ignore-missing-imports --follow-imports=skip $(API_DOMAIN_TYPED_PATHS)
	MYPYPATH=app4-dashboard $(PYTHON) -m mypy --strict --ignore-missing-imports --follow-imports=silent $(API_ROUTE_TYPED_PATHS)

scripts-check:
	shellcheck $(SHELL_SCRIPTS)

docs-check:
	$(PYTHON) tools/check_markdown_links.py
	$(PYTHON) tools/production_qualification.py check-tree docs/benchmarks
	$(PYTHON) tools/export_event_schemas.py --check
	$(PYTHON) tools/check_platform_version.py

workflows-check:
	actionlint

audit:
	$(PYTHON) -m pip_audit --strict

static: compile lint worker-lint service-core-lint shared-lint typecheck scripts-check docs-check workflows-check

test:
	$(PYTHON) -m pytest -m "not gpu and not integration"

integration-test:
	$(PYTHON) -m pytest -m integration tests/integration

coverage:
	$(PYTHON) -m coverage erase
	$(PYTHON) -m coverage run -m pytest -m "not gpu and not integration"
	$(PYTHON) -m coverage report

frontend-check:
	cd app4-dashboard/frontend && \
	$(NPM) ci && \
	$(NPM) audit --omit=dev --audit-level=high && \
	$(NPM) run duplication && \
	$(NPM) run test && \
	$(NPM) run lint && \
	$(NPM) run typecheck && \
	$(NPM) run build

frontend-e2e:
	cd app4-dashboard/frontend && $(NPM) run build && $(NPM) run test:e2e

check: static audit coverage
